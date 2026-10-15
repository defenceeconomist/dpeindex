import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.redif import parse_file
from app.topic_store import CURRENT, FINGERPRINT, corpus_fingerprint, load_topics, publish_snapshot, read_corpus, validate_snapshot
from app.topics import check_token_coverage, embedding_key


class MemoryPipeline:
    def __init__(self, client):
        self.client, self.actions = client, []
        self.watching = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def watch(self, *keys):
        self.watching = True

    def multi(self):
        self.watching = False

    def get(self, key):
        if self.watching:
            return self.client.data.get(key)
        self.actions.append(("get", key))
        return self

    def mget(self, keys):
        self.actions.append(("mget", keys))
        return self

    def set(self, key, value):
        self.actions.append(("set", key, value))
        return self

    def execute(self):
        if self.client.fail:
            raise RuntimeError("Redis transaction failed")
        result = []
        for action in self.actions:
            if action[0] == "get":
                result.append(self.client.data.get(action[1]))
            elif action[0] == "mget":
                result.append([self.client.data.get(key) for key in action[1]])
            else:
                self.client.data[action[1]] = action[2]
                result.append(True)
        return result


class MemoryClient:
    def __init__(self):
        self.data, self.fail = {}, False

    def pipeline(self, transaction=True):
        return MemoryPipeline(self)


def snapshot_fixture():
    return {"metadata": {"run_id": "test-run", "corpus_fingerprint": "original", "abstract_count": 3,
                         "missing_abstract_count": 1, "article_count": 4,
                         "token_coverage": {"truncated_abstracts": 0}, "outliers": {"after": 1}},
            "topics": [{"id": 0, "count": 1}, {"id": 1, "count": 1}],
            "documents": [{"handle": "a", "topic": 0, "x": 1., "y": 2.},
                          {"handle": "b", "topic": 1, "x": 3., "y": 4.},
                          {"handle": "c", "topic": -1, "x": 2., "y": 3.}],
            "hierarchy": [{"id": "0", "topics": [0], "children": [], "distance": 0},
                          {"id": "1", "topics": [1], "children": [], "distance": 0},
                          {"id": "2", "topics": [0, 1], "children": ["0", "1"], "distance": 1}]}


class Tokenizer:
    def __call__(self, texts, **kwargs):
        assert kwargs == {"truncation": False, "padding": False, "add_special_tokens": True}
        return {"input_ids": [[1, *range(len(text.split())), 2] for text in texts]}


class CoverageTests(unittest.TestCase):
    def test_longest_current_abstract_is_checked_in_full(self):
        articles = parse_file(Path(__file__).resolve().parents[1] / "data/defpea.redif")
        longest = max(articles, key=lambda item: len(item["abstract"].split()))
        result = check_token_coverage([longest], Tokenizer(), 32000)
        self.assertEqual(result["max_tokens"], len(longest["abstract"].split()) + 2)
        self.assertEqual(result["truncated_abstracts"], 0)
        self.assertEqual(result["per_article"][0]["handle"], longest["handle"])

    def test_over_limit_error_identifies_article_without_slicing(self):
        article = {"handle": "long-article", "abstract": "word " * 200}
        with self.assertRaisesRegex(ValueError, "long-article.*202 tokens"):
            check_token_coverage([article], Tokenizer(), 100)
        self.assertEqual(len(article["abstract"].split()), 200)

    def test_embedding_cache_invalidates_content_and_revision(self):
        key = embedding_key("An entire abstract including its final words", "revision-1")
        self.assertNotEqual(key, embedding_key("An entire abstract", "revision-1"))
        self.assertNotEqual(key, embedding_key("An entire abstract including its final words", "revision-2"))

    def test_fingerprint_ignores_order_but_tracks_abstracts_and_metadata(self):
        articles = [{"handle": "a", "title": "One", "abstract": "Full abstract"}, {"handle": "b", "abstract": ""}]
        original = corpus_fingerprint(articles)
        self.assertEqual(original, corpus_fingerprint(articles[::-1]))
        changed = copy.deepcopy(articles)
        changed[0]["abstract"] += " final sentence"
        self.assertNotEqual(original, corpus_fingerprint(changed))
        changed = copy.deepcopy(articles)
        changed[1]["title"] = "Updated"
        self.assertNotEqual(original, corpus_fingerprint(changed))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.client = MemoryClient()
        self.snapshot = snapshot_fixture()
        self.client.data[FINGERPRINT] = "original"

    def test_not_built_ready_and_stale(self):
        self.assertEqual(load_topics(self.client)["status"], "not_built")
        publish_snapshot(self.snapshot, self.client)
        self.assertFalse(load_topics(self.client)["stale"])
        self.client.data[FINGERPRINT] = "updated"
        result = load_topics(self.client)
        self.assertTrue(result["stale"])
        self.assertEqual(result["metadata"]["run_id"], "test-run")
        self.assertIn("dpe:topics:run:test-run", self.client.data)

    def test_invalid_or_failed_run_keeps_last_successful_result(self):
        publish_snapshot(self.snapshot, self.client)
        original = self.client.data[CURRENT]
        invalid = copy.deepcopy(self.snapshot)
        invalid["documents"][0]["x"] = float("nan")
        with self.assertRaises(ValueError):
            publish_snapshot(invalid, self.client)
        self.assertEqual(self.client.data[CURRENT], original)
        self.client.fail = True
        with self.assertRaises(RuntimeError):
            publish_snapshot(self.snapshot, self.client)
        self.assertEqual(self.client.data[CURRENT], original)

    def test_counts_point_identity_and_hierarchy_are_validated(self):
        validate_snapshot(self.snapshot)
        cases = []
        invalid = copy.deepcopy(self.snapshot)
        invalid["documents"][0]["handle"] = "b"
        cases.append(invalid)
        invalid = copy.deepcopy(self.snapshot)
        invalid["topics"][0]["count"] = 10
        cases.append(invalid)
        invalid = copy.deepcopy(self.snapshot)
        invalid["hierarchy"][-1]["topics"] = [0]
        cases.append(invalid)
        invalid = copy.deepcopy(self.snapshot)
        invalid["metadata"]["token_coverage"]["truncated_abstracts"] = 1
        cases.append(invalid)
        for invalid in cases:
            with self.assertRaises(ValueError):
                validate_snapshot(invalid)

    def test_corpus_snapshot_reads_full_canonical_records(self):
        from app.store import article_key
        rows = [{"handle": "b", "abstract": "The complete final sentence."}, {"handle": "a", "abstract": ""}]
        self.client.data["dpe:catalog"] = json.dumps({"articles": [{"handle": a["handle"]} for a in rows]})
        for row in rows:
            self.client.data[article_key(row["handle"])] = json.dumps(row)
        self.assertEqual(read_corpus(self.client), rows[::-1])
        del self.client.data[article_key("b")]
        with self.assertRaisesRegex(RuntimeError, "missing article"):
            read_corpus(self.client)


class TopicApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_api_states(self):
        for result in ({"status": "not_built"}, {"status": "ready", "stale": False}, {"status": "ready", "stale": True}):
            expected = {**result, **({"comparison": {"status": "ready"}, "timeline": {"status": "not_built"}} if result["status"] == "ready" else {})}
            with patch("app.main.load_topics", return_value=copy.deepcopy(result)), patch("app.main.load_comparison", return_value={"status": "ready"}), patch("app.main.load_timeline", return_value={"status": "not_built"}):
                response = self.client.get("/api/topics")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), expected)
        with patch("app.main.load_topics", side_effect=RuntimeError("internal redis address")):
            response = self.client.get("/api/topics")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("internal redis", response.text)

    def test_topic_page_and_shared_navigation(self):
        for route in ("/", "/topics/"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)
            self.assertIn('aria-label="Main navigation"', response.text)
            self.assertIn('aria-current="page"', response.text)
        for route in ("/topics.js", "/topics.css", "/article-ui.js", "/retrospectives.js", "/topic-timeline.js"):
            self.assertEqual(self.client.get(route).status_code, 200)
        redirect = self.client.get("/topics", follow_redirects=False)
        self.assertEqual(redirect.status_code, 308)
        self.assertEqual(redirect.headers["location"], "topics/")

    def test_web_import_does_not_load_modelling_libraries(self):
        subprocess.run([sys.executable, "-c", "import sys; import app.main; assert not any(x in sys.modules for x in ('torch', 'bertopic', 'sentence_transformers', 'umap'))"], check=True)


class OutlierTests(unittest.TestCase):
    def test_keyword_processing_survives_final_topic_update(self):
        import importlib.util
        if importlib.util.find_spec("bertopic") is None:
            self.skipTest("BERTopic is installed only in the modelling environment")
        import numpy as np
        from app.topics import fit_model, make_snapshot
        rng = np.random.default_rng(42)
        docs = (["The peace treaty and the civil conflict end with peaceful negotiations."] * 30 +
                ["The arms trade and the military aircraft procurement increase defence costs."] * 30)
        embeddings = np.vstack([rng.normal([1, 0, 0], .02, (30, 3)), rng.normal([0, 1, 0], .02, (30, 3))])
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        reduced = np.vstack([rng.normal([0, 0], .02, (30, 2)), rng.normal([5, 5], .02, (30, 2))])
        params = {"n_neighbors": 10, "n_components": 2, "min_cluster_size": 10, "min_samples": 1}
        model, labels = fit_model(docs, embeddings, reduced, params)
        articles = [{"handle": str(i), "abstract": doc, "title": doc, "authors": [],
                     "volume": "1", "issue": "1", "year": "2020", "pages": "1", "doi": ""} for i, doc in enumerate(docs)]
        outliers = {"before": labels.count(-1), "after": labels.count(-1)}
        with patch("app.topics.projection", return_value=reduced):
            result = make_snapshot(model, articles, articles, embeddings, labels, labels, np.ones(len(labels)),
                                   params, {"truncated_abstracts": 0}, "revision", "test-run", {"outliers": outliers})
        terms = {word["term"] for topic in result["topics"] for word in topic["keywords"]}
        self.assertNotIn("the", terms)
        self.assertNotIn("and", terms)
        self.assertTrue(any(" " in term for term in terms), "Bigram keyword extraction must survive update_topics")
        expected = np.stack([embeddings[np.array(labels) == topic].mean(axis=0) for topic in sorted(set(labels))])
        np.testing.assert_allclose(model.topic_embeddings_, expected)
        validate_snapshot(result)

    def test_cautious_reassignment_preserves_ambiguous_and_original_members(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("Numerical modelling dependencies are in the topics image")
        from app.topics import calibrate_outliers
        rng = np.random.default_rng(42)
        first = rng.normal([1, 0, 0], .02, (100, 3))
        second = rng.normal([0, 1, 0], .02, (100, 3))
        vectors = np.vstack([first, second, [1, 0, 0], [1, 1, 0]])
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        labels = [0] * 100 + [1] * 100 + [-1, -1]
        docs = ["entire abstract final words"] * len(labels)

        class Model:
            probabilities_ = np.ones(len(labels))

            def reduce_outliers(self, documents, topics, **kwargs):
                assert documents == docs and kwargs["strategy"] == "embeddings"
                return topics[:-2] + [0, 0]

        final, similarities, report = calibrate_outliers(Model(), labels, vectors, docs)
        self.assertEqual(final[:200], labels[:200])
        self.assertEqual(final[-2:], [0, -1])
        self.assertEqual((report["before"], report["after"]), (2, 1))
        self.assertGreaterEqual(report["agreement"], .95)
        self.assertEqual(len(similarities), len(labels))


if __name__ == "__main__":
    unittest.main()
