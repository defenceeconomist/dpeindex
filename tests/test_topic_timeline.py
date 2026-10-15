import copy
import importlib.util
import json
from pathlib import Path
import unittest

from app.topic_store import CURRENT
from app.topic_timeline import (PREFIX, WIDTHS, build_timeline, load_timeline,
                                publication_year, publish_timeline, validate_timeline)
from test_topics import MemoryClient

ROOT = Path(__file__).resolve().parents[1]
AUDITED = ROOT / "data/topic-artifacts/review/20261015T112751Z-f48f8afb/snapshot.json"


def fixture():
    snapshot = {"status": "ready", "metadata": {"run_id": "run-a", "corpus_fingerprint": "corpus-a", "abstract_count": 4, "missing_abstract_count": 1},
                "topics": [{"id": 0}, {"id": 1}],
                "documents": [{"handle": str(i), "year": year, "topic": topic} for i, (year, topic) in enumerate((("1999", 0), ("2001", 1), ("2001", -1), ("unknown", 0)))]}
    timeline = {"status": "ready", "run_id": "run-a", "corpus_fingerprint": "corpus-a", "undated_abstract_count": 1, "undated_missing_count": 0, "groups": {}}
    for name, width in WIDTHS.items():
        periods, cells = [], []
        for start in range(1999 // width * width, 2001 // width * width + 1, width):
            end = start + width - 1
            counts = {0: int(start <= 1999 <= end), 1: int(start <= 2001 <= end), -1: int(start <= 2001 <= end)}
            denominator = sum(counts.values())
            missing = int(start <= 2000 <= end)
            periods.append({"start": start, "end": end, "abstract_count": denominator, "missing_abstract_count": missing, "record_count": denominator + missing})
            for topic, count in counts.items():
                cells.append({"start": start, "topic": topic, "count": count, "share": 100 * count / denominator if denominator else None, "words": ["sample"] if count else []})
        timeline["groups"][name] = {"width": width, "periods": periods, "cells": cells}
    return snapshot, timeline


class TimelineTests(unittest.TestCase):
    def test_gap_periods_unknown_years_and_denominators(self):
        snapshot, timeline = fixture()
        validate_timeline(timeline, snapshot)
        self.assertIsNone(publication_year({"year": "unknown"}))
        self.assertIsNone(publication_year({"year": "9999"}))
        self.assertEqual(timeline["groups"]["year"]["periods"][1]["abstract_count"], 0)
        cells = {(cell["start"], cell["topic"]): cell for cell in timeline["groups"]["year"]["cells"]}
        self.assertIsNone(cells[2000, 0]["share"])
        self.assertEqual(cells[2001, 0]["share"], 0)
        self.assertEqual(cells[2001, 1]["share"], 50)
        self.assertEqual(cells[2001, -1]["share"], 50)

    def test_bad_binding_counts_duplicates_and_denominators_are_rejected(self):
        snapshot, original = fixture()
        for mutation in ("run", "corpus", "counts", "shares", "missing", "duplicates", "interval", "undated"):
            timeline = copy.deepcopy(original)
            if mutation == "run": timeline["run_id"] = "new-model"
            if mutation == "corpus": timeline["corpus_fingerprint"] = "changed"
            if mutation == "counts": timeline["groups"]["year"]["cells"][0]["count"] = 0
            if mutation == "shares": timeline["groups"]["year"]["cells"][-1]["share"] = 100
            if mutation == "missing": timeline["groups"]["year"]["periods"][1]["missing_abstract_count"] = 0
            if mutation == "duplicates": timeline["groups"]["year"]["cells"].append(timeline["groups"]["year"]["cells"][0])
            if mutation == "interval": timeline["groups"]["five_years"]["periods"][0]["end"] += 1
            if mutation == "undated": timeline["undated_abstract_count"] = 0
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate_timeline(timeline, snapshot)

    def test_atomic_timeline_publication_does_not_replace_the_model(self):
        snapshot, timeline = fixture()
        client = MemoryClient()
        raw = json.dumps(snapshot)
        client.data[CURRENT] = raw
        publish_timeline(timeline, snapshot, client)
        self.assertEqual(client.data[CURRENT], raw)
        saved = client.data[PREFIX + "run-a"]
        client.fail = True
        with self.assertRaises(RuntimeError): publish_timeline(timeline, snapshot, client)
        self.assertEqual(client.data[PREFIX + "run-a"], saved)
        client.fail = False
        changed = copy.deepcopy(snapshot); changed["metadata"]["run_id"] = "new-run"
        client.data[CURRENT] = json.dumps(changed)
        with self.assertRaises(ValueError): publish_timeline(timeline, snapshot, client)
        self.assertEqual(client.data[PREFIX + "run-a"], saved)
        self.assertEqual(client.data[CURRENT], json.dumps(changed))

    def test_api_loader_handles_missing_invalid_and_embedded_analysis(self):
        snapshot, timeline = fixture()
        class Archive:
            def __init__(self, raw): self.raw = raw
            def get(self, key): return self.raw
        self.assertEqual(load_timeline(snapshot, Archive(None))["status"], "not_built")
        self.assertEqual(load_timeline({**snapshot, "timeline": timeline}, Archive(None))["status"], "ready")
        refreshed = {**timeline, "built_at": "a later analysis"}
        self.assertEqual(load_timeline({**snapshot, "timeline": timeline}, Archive(json.dumps(refreshed)))["built_at"], "a later analysis")
        timeline["run_id"] = "old-run"
        with self.assertLogs("app.topic_timeline", level="ERROR"):
            self.assertEqual(load_timeline(snapshot, Archive(json.dumps(timeline)))["status"], "unavailable")
        self.assertEqual(load_timeline({"status": "not_built"})["status"], "not_built")


@unittest.skipUnless(AUDITED.exists() and importlib.util.find_spec("bertopic"), "Requires the audited snapshot and modelling environment")
class PublishedTimelineTests(unittest.TestCase):
    def test_full_abstract_temporal_representation_preserves_the_published_model(self):
        from app.redif import parse_file
        snapshot = json.loads(AUDITED.read_text())
        original = copy.deepcopy(snapshot)
        articles = parse_file(ROOT / "data/defpea.redif")
        timeline = build_timeline(snapshot, articles)
        self.assertEqual(snapshot, original)
        for group in timeline["groups"].values():
            self.assertEqual(sum(period["abstract_count"] for period in group["periods"]), 1152)
            self.assertEqual(sum(period["missing_abstract_count"] for period in group["periods"]), 69)
            self.assertEqual(sum(cell["count"] for cell in group["cells"] if cell["topic"] == -1), 87)
            self.assertTrue(any(cell["words"] for cell in group["cells"]))
        self.assertEqual(timeline["first_year"], 1997)
        changed = copy.deepcopy(articles); changed[0]["abstract"] += " Changed final sentence."
        with self.assertRaisesRegex(ValueError, "canonical corpus differs"): build_timeline(snapshot, changed)


if __name__ == "__main__": unittest.main()
