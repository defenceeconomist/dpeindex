import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.store import point_id, semantic_search
from app.theme_filter import selection_handles, theme_options


ARTICLES = [{"handle": "article-" + name, "title": "Peace " + name, "authors": ["A Researcher"], "year": year,
             "volume": volume, "issue": "1", "pages": "1", "doi": ""}
            for name, year, volume in (("a", "1999", "10"), ("b", "2000", "11"), ("c", "2010", "21"), ("d", "2010", "21"))]
CATALOG = {"articles": ARTICLES, "count": 4}
SNAPSHOT = {"status": "ready", "stale": False, "metadata": {"run_id": "run-a", "built_at": "2026-10-15"},
            "topics": [{"id": 0, "label": "peace"}, {"id": 1, "label": "spending"}],
            "documents": [{"handle": article["handle"], "topic": topic} for article, topic in zip(ARTICLES, (0, 1, -1))]}


class ThemeFilterTests(unittest.TestCase):
    def test_intersect_theme_year_and_current_catalog(self):
        self.assertIsNone(selection_handles(CATALOG))
        self.assertEqual(selection_handles(CATALOG, 0, "run-a", snapshot=SNAPSHOT), {"article-a"})
        self.assertEqual(selection_handles(CATALOG, -1, "run-a", snapshot=SNAPSHOT), {"article-c"})
        self.assertEqual(selection_handles(CATALOG, 0, "run-a", 2000, 2010, SNAPSHOT), set())
        self.assertEqual(selection_handles(CATALOG, year_from=2010, year_to=2010), {"article-c", "article-d"})
        self.assertEqual(selection_handles({"articles": ARTICLES[1:]}, 0, "run-a", snapshot=SNAPSHOT), set())
        options = theme_options(SNAPSHOT, CATALOG)
        self.assertIsNone(options["articles"][-1]["topic"])
        self.assertEqual([topic["count"] for topic in options["topics"]], [1, 1])
        self.assertEqual(theme_options({"status": "not_built"}, CATALOG)["status"], "not_built")

    def test_stale_run_ids_and_invalid_filters_cannot_reinterpret_a_theme(self):
        for theme, run, status in ((0, "old-run", 409), (0, None, 409), (7, "run-a", 404)):
            with self.subTest(theme=theme, run=run), self.assertRaises(HTTPException) as error:
                selection_handles(CATALOG, theme, run, snapshot=SNAPSHOT)
            self.assertEqual(error.exception.status_code, status)
        with self.assertRaises(HTTPException): selection_handles(CATALOG, year_from=2010, year_to=2000)

    def test_issue_and_exact_search_paths_respect_the_same_filter(self):
        client = TestClient(app)
        with patch("app.main.load_catalog", return_value=CATALOG), patch("app.theme_filter.load_topics", return_value=SNAPSHOT):
            with patch("app.main.issue_from_catalog", return_value=ARTICLES):
                response = client.get("/api/issue?volume=21&number=1&theme=-1&run=run-a")
                self.assertEqual([article["handle"] for article in response.json()["articles"]], ["article-c"])
            with patch("app.main.semantic_search", return_value=[]) as semantic, patch("app.main.load_article", side_effect=lambda handle: next(a for a in ARTICLES if a["handle"] == handle)):
                response = client.get("/api/search?q=Peace&theme=0&run=run-a")
                self.assertEqual([article["handle"] for article in response.json()["results"]], ["article-a"])
                semantic.assert_called_once_with("Peace", 40, allowed_handles={"article-a"})
            self.assertEqual(client.get("/api/issue?volume=21&number=1&theme=0&run=old").status_code, 409)

    def test_qdrant_filters_before_ranking_and_empty_filters_skip_encoding(self):
        model, client = MagicMock(), MagicMock()
        model.query_embed.return_value = iter([[1, 0]])
        client.pipeline.return_value.execute.return_value = []
        with patch("app.store.embedding_model", return_value=model) as encoder, patch("app.store.redis_client", return_value=client), patch("app.store.qdrant", return_value={"result": {"points": []}}) as query:
            self.assertEqual(semantic_search("peace", 40, set()), [])
            encoder.assert_not_called()
            semantic_search("peace", 40, {"article-c", "article-a"})
            payload = query.call_args.args[2]
            self.assertEqual(payload["limit"], 40)
            self.assertEqual(payload["filter"], {"must": [{"has_id": [point_id("article-a"), point_id("article-c")]}]})


if __name__ == "__main__": unittest.main()
