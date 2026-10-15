import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.topic_guidance import (alignment, cached_embedding_backend, fingerprint, load_guidance,
                                saved_settings, seed_topic_list, validate_guidance)
from app.topics import CachedProjection, embedding_key, encode_abstracts, fit_model, make_snapshot, projection, tune

ROOT = Path(__file__).resolve().parent.parent


def guidance_fixture():
    manifest = {"schema_version": 1, "topics": [
        {"id": "peace", "name": "Peace", "terms": ["peace", "conflict"]},
        {"id": "arms", "name": "Arms", "terms": ["arms", "procurement"]}]}
    return {"method": "retrospective_seeds", "manifest": manifest, "fingerprint": fingerprint(manifest)}


class SeedEvidenceTests(unittest.TestCase):
    def test_all_substantive_categories_are_consolidated_with_provenance(self):
        guidance = load_guidance(ROOT / "data/topic-seeds.json")
        topics = guidance["manifest"]["topics"]
        self.assertEqual(len(topics), 20)
        self.assertEqual(len({topic["id"] for topic in topics}), 20)
        manpower = next(topic for topic in topics if topic["id"] == "military-personnel")
        self.assertEqual({source["category"] for source in manpower["sources"]},
                         {"Military manpower", "Military personnel and recruitment"})
        for topic in topics:
            for source in topic["sources"]:
                self.assertTrue(source["definition"])
                self.assertTrue(source["source"]["doi"].startswith("10."))
                self.assertNotIn(source["category"], {"Others", "Country surveys", "Company surveys"})
                self.assertNotIn("count", source)

    def test_counts_crosswalk_notes_and_old_assignments_cannot_change_guidance(self):
        reference = json.loads((ROOT / "data/retrospectives.json").read_text())
        original = load_guidance(ROOT / "data/topic-seeds.json", reference)
        for period in reference["periods"]:
            period["classified_total"] = 999999
            for category in period["categories"]:
                category.update(count=999999, note="different model commentary", topic_ids=[123])
        reference["audited_topics"] = []
        reference["reviewed_examples"] = {}
        self.assertEqual(load_guidance(ROOT / "data/topic-seeds.json", reference), original)

    def test_missing_provenance_and_duplicate_ids_fail(self):
        data = json.loads((ROOT / "data/topic-seeds.json").read_text())
        for mutation in ("category", "duplicate", "missing"):
            changed = copy.deepcopy(data)
            if mutation == "category": changed["topics"][0]["sources"][0]["category"] = "invented"
            if mutation == "duplicate": changed["topics"][1]["id"] = changed["topics"][0]["id"]
            if mutation == "missing": changed["topics"].pop()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "seeds.json"
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError): load_guidance(path)

    def test_saved_guidance_is_frozen_and_legacy_settings_remain_unseeded(self):
        metadata = {"params": {"n_neighbors": 10}, "model_revision": "revision", "run_id": "run"}
        self.assertNotIn("guidance", saved_settings(metadata))
        self.assertIsNone(validate_guidance(None))
        metadata["guidance"] = load_guidance(ROOT / "data/topic-seeds.json")
        restored = json.loads(json.dumps(saved_settings(metadata)))
        self.assertEqual(validate_guidance(restored["guidance"]), metadata["guidance"])
        changed = copy.deepcopy(restored["guidance"])
        changed["manifest"]["topics"][0]["terms"].append("changed")
        with self.assertRaises(ValueError): validate_guidance(changed)


class AlignmentTests(unittest.TestCase):
    def test_matching_is_one_to_one_and_unmatched_seeds_score_zero(self):
        guidance = guidance_fixture()
        embeddings = np.array([[1., 0.], [1., 0.], [0., 1.], [0., 1.]])
        seeds = np.eye(2)
        result = alignment([0, 0, 1, 1], embeddings, guidance, seeds)
        self.assertEqual(result["score"], 1.)
        self.assertEqual([item["topic"] for item in result["matches"]], [0, 1])
        merged = alignment([0, 0, 0, 0], embeddings, guidance, seeds)
        self.assertEqual(sum(item["topic"] is None for item in merged["matches"]), 1)
        self.assertAlmostEqual(merged["score"], np.sqrt(.5) / 2)
        self.assertEqual(alignment([-1] * 4, embeddings, guidance, seeds)["score"], 0.)

    def test_outliers_do_not_move_alignment_centroids(self):
        guidance = guidance_fixture()
        result = alignment([0, -1, 1], np.array([[1., 0.], [100., 100.], [0., 1.]]), guidance, np.eye(2))
        self.assertEqual(result["score"], 1.)


class NativeGuidanceTests(unittest.TestCase):
    def test_fully_cached_seed_encoding_does_not_load_encoder_weights(self):
        from types import SimpleNamespace
        texts = ["peace conflict", "arms procurement"]
        records = [{"handle": str(i), "abstract": text} for i, text in enumerate(texts)]
        class Tokenizer:
            model_max_length = 32768
            def __call__(self, texts, **kwargs):
                self.assert_no_truncation = kwargs["truncation"] is False
                return {"input_ids": [[1, 2, 3] for text in texts]}
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            (artifacts / "embeddings").mkdir()
            for record, vector in zip(records, np.eye(3)[:2], strict=True):
                np.save(artifacts / "embeddings" / (embedding_key(record["abstract"], "revision") + ".npy"), vector)
            with (patch("transformers.AutoConfig.from_pretrained", return_value=SimpleNamespace(hidden_size=3, max_position_embeddings=32768)),
                  patch("transformers.AutoTokenizer.from_pretrained", return_value=Tokenizer()),
                  patch("sentence_transformers.SentenceTransformer", side_effect=AssertionError("Encoder weights loaded"))):
                vectors, coverage = encode_abstracts(records, "revision", artifacts, "cpu", 8)
            np.testing.assert_array_equal(vectors, np.eye(3)[:2])
            self.assertEqual(coverage["truncated_abstracts"], 0)

    def test_cached_backend_embeds_only_missing_texts(self):
        calls = []
        def encode(texts):
            calls.append(texts)
            return np.array([[len(text), 1.] for text in texts])
        backend = cached_embedding_backend(encode)
        backend.embed(["peace", "arms", "peace"])
        backend.embed(["arms", "peace"])
        self.assertEqual(calls, [["peace", "arms"]])

    def test_projection_cache_keys_include_inputs_targets_guidance_params_and_seed(self):
        cache, X, y = {}, np.eye(3), np.array([0, 1, -1])
        params = {"n_neighbors": 2, "n_components": 2}
        guidance = guidance_fixture()
        with patch("app.topics.projection", return_value=np.zeros((3, 2))) as fit:
            CachedProjection(params, 42, cache, guidance).fit_transform(X, y)
            CachedProjection(params, 42, cache, guidance).fit_transform(X.copy(), y.copy())
            self.assertEqual(fit.call_count, 1)
            CachedProjection(params, 42, cache, guidance).fit_transform(X + .1, y)
            CachedProjection(params, 42, cache, guidance).fit_transform(X, y + 1)
            CachedProjection(params, 7, cache, guidance).fit_transform(X, y)
            CachedProjection(params, 42, cache).fit_transform(X, y)
            CachedProjection({**params, "n_neighbors": 3}, 42, cache, guidance).fit_transform(X, y)
            CachedProjection(params, 42, cache, {**guidance, "fingerprint": "different"}).fit_transform(X, y)
            self.assertEqual(fit.call_count, 7)

    def test_native_guidance_changes_umap_inputs_without_mutating_originals_and_restores_timeline(self):
        from app.topic_timeline import build_timeline
        guidance = guidance_fixture()
        rng = np.random.default_rng(42)
        docs = (["The peace treaty and civil conflict end with peaceful negotiations."] * 30 +
                ["The arms trade and military aircraft procurement increase defence costs and R&D in China's arms-trade industry."] * 30)
        vectors = np.vstack([rng.normal([1, 0, 0], .02, (30, 3)), rng.normal([0, 1, 0], .02, (30, 3))])
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        original = vectors.copy()
        vectors.setflags(write=False)
        seeds = np.array([[1., 0., 0.], [0., 1., 0.]])
        def unexpected(texts):
            raise AssertionError(f"Unexpected encoding: {texts}")
        backend = cached_embedding_backend(unexpected, dict(zip([" ".join(t) for t in seed_topic_list(guidance)], seeds)))
        params = {"n_neighbors": 10, "n_components": 2, "min_cluster_size": 10, "min_samples": 1}
        cache = {}
        with patch("app.topics.projection", wraps=projection) as fit:
            model, labels = fit_model(docs, vectors, None, params, guidance, backend, cache)
            inputs = fit.call_args
            self.assertFalse(np.array_equal(inputs.args[0], vectors))
            self.assertEqual(set(inputs.kwargs["y"]), {0, 1})
        np.testing.assert_array_equal(vectors, original)

        expected = np.stack([vectors[np.array(labels) == topic].mean(axis=0) for topic in sorted(set(labels))])
        np.testing.assert_allclose(model.topic_embeddings_, expected)
        self.assertEqual(model.language, "english")
        self.assertEqual(model.seed_topic_list, seed_topic_list(guidance))
        articles = [{"handle": str(i), "abstract": doc, "title": doc, "authors": [], "volume": "21", "issue": "1",
                     "year": "2010", "pages": "1", "doi": ""} for i, doc in enumerate(docs)]
        outliers = {"before": labels.count(-1), "after": labels.count(-1)}
        with patch("app.topics.projection", return_value=np.zeros((60, 2))):
            snapshot = make_snapshot(model, articles, articles, vectors, labels, labels, np.ones(60), params,
                                     {"truncated_abstracts": 0}, "revision", "test-guided", {"outliers": outliers})
        snapshot["metadata"]["guidance"] = guidance
        timeline = build_timeline(snapshot, articles)
        self.assertEqual(timeline["run_id"], "test-guided")
        np.testing.assert_allclose(model.topic_embeddings_, expected)
        np.testing.assert_array_equal(vectors, original)


class PairedSelectionTests(unittest.TestCase):
    def test_both_arms_share_bounds_and_can_select_more_than_twenty_topics(self):
        guidance = guidance_fixture()
        grid = {"n_neighbors": [5, 10], "n_components": [2], "min_cluster_size": [2], "min_samples": [1]}
        def fit(docs, embeddings, reduced, params, mode, backend, cache, seed=42):
            return {"params": params, "guided": mode is not None}, [0, 0, 1, 1]
        class Evaluator:
            def measure(self, model, labels):
                better = model["params"]["n_neighbors"] == 10
                return {"topic_count": 30 if better else 5, "outliers": 0, "coherence": .3 if better else .1,
                        "silhouette": .2 if better else .1, "diversity": .9, "coverage": 1.,
                        "alignment": (.4 if better else .2) + (.1 if model["guided"] else 0)}
        with (tempfile.TemporaryDirectory() as directory,
              patch("app.topics.GRID", grid), patch("app.topics.fit_model", side_effect=fit),
              patch("app.topics.representative_review", return_value=[])):
            _, _, params, report, control = tune(["doc"] * 4, np.eye(4), Evaluator(), [], Path(directory), guidance, object())
            self.assertEqual(params["n_neighbors"], 10)
            self.assertEqual(control[2]["n_neighbors"], 10)
            self.assertEqual(report["metric_bounds"], control[3]["metric_bounds"])
            self.assertEqual(report["metric_weights"]["alignment"], 2)
            self.assertEqual(report["metric_bounds"]["alignment"], [.2, .5])


if __name__ == "__main__":
    unittest.main()
