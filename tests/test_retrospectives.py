import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.main import app
from app.retrospectives import (build_comparison, in_period, load_comparison,
                               reference_data, validate_reference)

ROOT = Path(__file__).resolve().parents[1]
AUDITED = Path(os.environ.get("DPE_AUDITED_SNAPSHOT", ROOT / "data/topic-artifacts/runs/20261015T154322Z-6505198a/snapshot.json"))


def fixture():
    reference = copy.deepcopy(reference_data())
    reference["missing_abstract_records"] = [
        {"handle": "missing-1", "volume": "9", "issue": "1-2"},
        {"handle": "missing-excluded-10", "volume": "10", "issue": "4"},
        {"handle": "missing-2", "volume": "20", "issue": "6"},
        {"handle": "missing-3", "volume": "21", "issue": "1"},
        {"handle": "missing-excluded-31", "volume": "31", "issue": "1"},
    ]
    reference["reviewed_examples"] = {"first": {"7": ["paper-0"]}, "second": {"1": ["paper-3"]}, "third": {"2": ["paper-5"]}}
    reference["additional_reviewed_examples"] = {}
    records = [(8, 1, 7), (10, 2, -1), (10, 3, 1), (11, 1, 1), (20, 6, -1), (21, 1, 2), (30, 6, 0), (31, 1, 9)]
    snapshot = {"status": "ready", "metadata": {**reference["audited_model"], "abstract_count": len(records),
                 "missing_abstract_count": 5, "article_count": len(records) + 5,
                 "guidance": {"fingerprint": reference["audited_model"]["guidance_fingerprint"]}},
                "topics": [{"id": item["id"], "keywords": [{"term": term} for term in item["keywords"]]} for item in reference["audited_topics"]],
                "documents": [{"handle": f"paper-{index}", "volume": str(volume), "issue": str(issue), "topic": topic,
                               "title": f"Paper {index}", "authors": ["A Researcher"], "year": "2025"}
                              for index, (volume, issue, topic) in enumerate(records)]}
    return snapshot, reference


class Archive:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.keys = []

    def get(self, key):
        self.keys.append(key)
        return json.dumps(self.snapshot) if self.snapshot else None


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.reference = fixture()

    def test_additional_review_evidence_must_belong_to_its_period_and_topic(self):
        reference = copy.deepcopy(self.reference)
        reference["additional_reviewed_examples"] = {"first": {"7": ["paper-0"]}}
        self.assertEqual(build_comparison(self.snapshot, reference, self.snapshot)["status"], "ready")
        reference["additional_reviewed_examples"]["first"]["7"] = ["paper-3"]
        with self.assertRaises(ValueError): build_comparison(self.snapshot, reference, self.snapshot)

    def test_interpretation_keys_are_distinct(self):
        reference = copy.deepcopy(self.reference)
        reference["audited_topics"][1]["key"] = reference["audited_topics"][0]["key"]
        with self.assertRaises(ValueError): validate_reference(reference)

    def test_period_boundaries_and_combined_issues(self):
        first, second, third = self.reference["periods"]
        for issue in ("1", "2", "1-2", "1–2"):
            self.assertTrue(in_period({"volume": "10", "issue": issue}, first))
        for issue in ("3", "4", "2-3", "3-4", "2-1", "0", "", "unknown"):
            self.assertFalse(in_period({"volume": "10", "issue": issue}, first))
        self.assertTrue(in_period({"volume": "9", "issue": "4"}, first))
        self.assertFalse(in_period({"volume": "11", "issue": "1"}, first))
        for volume in (11, 20):
            self.assertTrue(in_period({"volume": str(volume), "year": "1900"}, second))
        for volume in (10, 21, 30, 31):
            self.assertFalse(in_period({"volume": str(volume)}, second))
        for volume in (21, 30):
            self.assertTrue(in_period({"volume": str(volume)}, third))
        for volume in (20, 31, 35):
            self.assertFalse(in_period({"volume": str(volume)}, third))

    def test_counts_and_separate_denominators_include_unassigned(self):
        comparison = build_comparison(self.snapshot, self.reference, self.snapshot)
        first, second, third = comparison["periods"]
        for period in (first, second, third):
            coverage = period["coverage"]
            self.assertEqual(coverage["abstract_count"], 2)
            self.assertEqual(coverage["missing_abstract_count"], 1)
            self.assertEqual(coverage["record_count"], 3)
            self.assertEqual(sum(row["count"] for row in period["model_topics"]) + coverage["unassigned_count"], 2)
            self.assertAlmostEqual(sum(row["share"] for row in period["model_topics"]) + coverage["unassigned_share"], 100)
            self.assertAlmostEqual(sum(row["share"] for row in period["categories"]), 100)
        self.assertEqual(first["coverage"]["unassigned_share"], 50)
        self.assertEqual(first["model_topics"][7]["share"], 50)
        self.assertAlmostEqual(first["categories"][0]["share"], 100 * 29 / 207)
        self.assertEqual(second["coverage"]["unassigned_count"], 1)
        self.assertEqual(third["coverage"]["unassigned_count"], 0)
        self.assertEqual(first["model_topics"][7]["examples"][0]["handle"], "paper-0")

    def test_overlapping_mappings_are_retained_without_combined_totals(self):
        first = build_comparison(self.snapshot, self.reference, self.snapshot)["periods"][0]
        industry_rows = [row for row in first["categories"] if 7 in row["topic_ids"]]
        self.assertGreater(len(industry_rows), 2)
        self.assertEqual(first["model_topics"][7]["count"], 1)
        self.assertNotIn("matched_total", first)
        self.assertTrue(any(not row["topic_ids"] for row in first["categories"]))

    def test_model_binding_keywords_and_example_membership_are_checked(self):
        for mutation in ("run", "corpus", "keywords", "example", "coverage", "guidance"):
            snapshot, reference = copy.deepcopy(self.snapshot), copy.deepcopy(self.reference)
            if mutation == "run": snapshot["metadata"]["run_id"] = "wrong-run"
            if mutation == "corpus": snapshot["metadata"]["corpus_fingerprint"] = "wrong-corpus"
            if mutation == "keywords": snapshot["topics"][0]["keywords"][0]["term"] = "different meaning"
            if mutation == "example": reference["reviewed_examples"]["first"]["7"] = ["paper-3"]
            if mutation == "coverage": snapshot["metadata"]["article_count"] += 1
            if mutation == "guidance": snapshot["metadata"]["guidance"]["fingerprint"] = "different-seeds"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_comparison(snapshot, reference, self.snapshot)

    def test_new_active_model_retains_audited_counts_and_disables_links(self):
        active = copy.deepcopy(self.snapshot)
        active["metadata"]["run_id"] = "new-model"
        active["documents"] = []
        archive = Archive(self.snapshot)
        with patch("app.retrospectives.reference_data", return_value=self.reference):
            comparison = load_comparison(active, archive)
        self.assertEqual(archive.keys, ["dpe:topics:run:" + self.reference["audited_model"]["run_id"]])
        self.assertEqual(comparison["status"], "review_required")
        self.assertFalse(comparison["links_enabled"])
        self.assertEqual(comparison["active_run_id"], "new-model")
        self.assertEqual(comparison["periods"][0]["coverage"]["abstract_count"], 2)

    def test_catalog_changes_and_failed_rebuild_leave_comparison_bound_to_current(self):
        before = copy.deepcopy(self.snapshot)
        with patch("app.retrospectives.reference_data", return_value=self.reference):
            for stale in (False, True):
                active = {**self.snapshot, "stale": stale}
                result = load_comparison(active, Archive())
                self.assertEqual(result["status"], "ready")
                self.assertTrue(result["links_enabled"])
        self.assertEqual(self.snapshot, before)

    def test_missing_or_invalid_archive_does_not_remove_explorer(self):
        active = copy.deepcopy(self.snapshot)
        active["metadata"]["run_id"] = "new-model"
        with patch("app.retrospectives.reference_data", return_value=self.reference):
            missing = load_comparison(active, Archive())
            self.assertEqual(missing["status"], "unavailable")
            invalid = copy.deepcopy(self.snapshot)
            invalid["metadata"]["corpus_fingerprint"] = "wrong-corpus"
            with self.assertLogs("app.retrospectives", level="ERROR"):
                damaged = load_comparison(active, Archive(invalid))
            self.assertEqual(damaged["status"], "unavailable")
            archive = Archive()
            with patch.object(archive, "get", side_effect=RedisConnectionError("archive read failed")), self.assertLogs("app.retrospectives", level="ERROR"):
                self.assertEqual(load_comparison(active, archive)["status"], "unavailable")
            with patch("app.main.load_topics", return_value=active), patch("app.main.load_comparison", return_value=missing), patch("app.main.load_timeline", return_value={"status": "not_built"}):
                response = TestClient(app).get("/api/topics")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ready")
            self.assertEqual(response.json()["comparison"]["status"], "unavailable")
        self.assertEqual(load_comparison({"status": "not_built"})["status"], "not_built")


class SourceTranscriptionTests(unittest.TestCase):
    def test_previous_crosswalk_is_archived_and_editorial_evidence_is_unchanged(self):
        archive = json.loads((ROOT / "data/retrospectives-archive/20261015T112751Z-f48f8afb.json").read_text())
        validate_reference(archive)
        self.assertEqual(len(archive["audited_topics"]), 10)
        for previous, current in zip(archive["periods"], reference_data()["periods"], strict=True):
            self.assertEqual(previous["source"], current["source"])
            self.assertEqual([(c["name"], c["count"], c["definition"]) for c in previous["categories"]],
                             [(c["name"], c["count"], c["definition"]) for c in current["categories"]])

    def test_original_tables_and_discrepancy_are_preserved(self):
        reference = reference_data()
        # Independent transcription in source table order: original 2000 Table 1,
        # 2010 Table I, and the 2020 Table 1 third-decade column.
        expected = [
            [29, 21, 18, 17, 16, 14, 11, 11, 9, 8, 7, 6, 6, 6, 5, 5, 5, 4, 4, 3, 2],
            [56, 37, 30, 29, 25, 23, 22, 20, 17, 16, 14, 12, 10, 9, 7, 4],
            [65, 55, 70, 33, 15, 7, 12, 9, 10, 15, 29, 29, 6, 6, 3, 21, 2, 2],
        ]
        for period, counts, total, reviews in zip(reference["periods"], expected, (207, 331, 389), (21, 14, 6)):
            self.assertEqual([row["count"] for row in period["categories"]], counts)
            self.assertEqual(period["classified_total"], total)
            self.assertEqual(period["excluded_book_reviews"], reviews)
            self.assertTrue(period["source"]["locator"])
            local = ROOT / "docs" / period["source"]["local_file"]
            if local.exists():
                self.assertEqual(hashlib.sha256(local.read_bytes()).hexdigest(), period["source"]["sha256"])
        first = reference["periods"][0]
        self.assertEqual(first["last_issue"], 2)
        self.assertIn("208", first["source_note"])
        self.assertIn("six arms-trade", first["source_note"])
        invalid = copy.deepcopy(reference)
        invalid["periods"][0]["categories"][14]["count"] = 6
        with self.assertRaises(ValueError): validate_reference(invalid)


@unittest.skipUnless(AUDITED.exists(), "Set DPE_AUDITED_SNAPSHOT to validate the immutable published model")
class PublishedRunTests(unittest.TestCase):
    def test_audited_period_totals_assignments_and_reviewed_abstracts(self):
        snapshot = json.loads(AUDITED.read_text())
        result = build_comparison(snapshot, reference_data(), snapshot)
        expected_counts = [[8, 0, 1, 0, 3, 1, 5, 3, 8, 0, 1, 0, 4, 0, 0], [60, 37, 23, 20, 27, 18, 17, 17, 11, 0, 17, 8, 18, 12, 3], [68, 56, 48, 33, 15, 22, 12, 16, 5, 5, 12, 17, 12, 8, 12]]
        for period, abstracts, missing, outliers, counts in zip(result["periods"], (45, 330, 381), (3, 31, 26), (11, 42, 40), expected_counts):
            self.assertEqual(period["coverage"]["abstract_count"], abstracts)
            self.assertEqual(period["coverage"]["missing_abstract_count"], missing)
            self.assertEqual(period["coverage"]["unassigned_count"], outliers)
            self.assertEqual([topic["count"] for topic in period["model_topics"]], counts)
            for topic in period["model_topics"]:
                self.assertEqual(bool(topic["examples"]), topic["count"] > 0)
            self.assertAlmostEqual(sum(topic["share"] for topic in period["model_topics"]) + period["coverage"]["unassigned_share"], 100)
        self.assertEqual(result["audited_model"]["run_id"], "20261015T154322Z-6505198a")
        self.assertTrue(result["links_enabled"])
        self.assertIn("not independent validation", result["method"]["independence"])


if __name__ == "__main__":
    unittest.main()
