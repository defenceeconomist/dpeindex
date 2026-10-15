import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import redis

from app.export_static import capture, export_dataset, replace_directory
from app.store import article_key, catalog_for
from app.topic_store import CURRENT, FINGERPRINT, corpus_fingerprint
from app.citation_store import CURRENT as CITATIONS_CURRENT, RUN_PREFIX, DOWNLOADS
from app.topic_timeline import WIDTHS
from test_topics import snapshot_fixture


def fixture():
    articles = [{"handle": name, "title": "Peace " + name, "authors": ["Researcher"], "abstract": "Complete abstract." if name != "d" else "",
                 "keywords": "peace", "volume": "1", "issue": "1", "year": "2000", "pages": "1", "doi": ""} for name in "abcd"]
    fingerprint = corpus_fingerprint(articles)
    topics = snapshot_fixture()
    topics["metadata"]["corpus_fingerprint"] = fingerprint
    topics["metadata"]["built_at"] = "2026-10-15T12:00:00Z"
    for topic in topics["topics"]: topic["label"] = f'Topic {topic["id"]}'
    for document in topics["documents"]: document["year"] = "2000"
    timeline = {"status": "ready", "run_id": "test-run", "corpus_fingerprint": fingerprint,
                "undated_abstract_count": 0, "undated_missing_count": 0, "groups": {}}
    for name, width in WIDTHS.items():
        timeline["groups"][name] = {"width": width, "periods": [{"start": 2000, "end": 2000 + width - 1,
            "abstract_count": 3, "missing_abstract_count": 1, "record_count": 4}], "cells": [
                {"start": 2000, "topic": topic, "count": 1, "share": 100 / 3} for topic in (0, 1, -1)]}
    topics["timeline"] = timeline
    run = "20261015T120000Z-abcd1234"
    citations = {"metadata": {"run_id": run, "eligible_count": 4, "excluded_count": 0, "article_count": 4,
        "edge_count": 0, "baseline_edge_count": 0, "corpus_fingerprint": fingerprint},
        "nodes": [{"handle": a["handle"], "in_degree": 0, "out_degree": 0, "degree": 0, "community": -1, "x": 0., "y": 0.} for a in articles],
        "edges": [], "communities": [], "flows": [], "coverage": [{"eligible": 4}],
        "comparison": {"topic_run_id": "test-run"}, "review": {"pending_count": 0, "doi_sample": ["reviewed"]}, "downloads": {}}
    files = {name: name.encode() for name in DOWNLOADS}
    citations["downloads"] = {name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)} for name, payload in files.items()}
    values = {"dpe:catalog": json.dumps(catalog_for(articles)), "dpe:source": "{}", FINGERPRINT: fingerprint,
              CURRENT: json.dumps(topics), CITATIONS_CURRENT: run, RUN_PREFIX + run: json.dumps(citations),
              **{article_key(a["handle"]): json.dumps(a) for a in articles}}
    return values, {RUN_PREFIX + run: {name: base64.b64encode(payload).decode() for name, payload in files.items()}}


class ExportTests(unittest.TestCase):
    def test_complete_export_preserves_records_bindings_downloads_and_fallback_fingerprint(self):
        values, archives = fixture()
        values[FINGERPRINT] = None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = export_dataset(root, values, archives)
            self.assertEqual(result["article_count"], 4)
            issues = json.loads((root / result["issues"]["1:1"]).read_text())
            self.assertEqual(len(issues["articles"]), 4)
            self.assertEqual(issues["articles"][-1]["abstract"], "")
            self.assertEqual(json.loads((root / "data/topics.json").read_text())["timeline"]["status"], "ready")
            for path, expected in result["files"].items():
                self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), expected["sha256"])

    def test_missing_records_fingerprint_mismatch_and_corrupt_downloads_fail(self):
        for fault in ("article", "fingerprint", "download", "timeline"):
            values, archives = fixture()
            if fault == "article": values.pop(article_key("a"))
            if fault == "fingerprint": values[FINGERPRINT] = "changed"
            if fault == "download": next(iter(archives.values()))["bundle.zip"] = base64.b64encode(b"wrong").decode()
            if fault == "timeline":
                snapshot = json.loads(values[CURRENT]); snapshot.pop("timeline"); values[CURRENT] = json.dumps(snapshot)
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                export_dataset(Path(temporary), values, archives)

    def test_capture_retries_changed_pointers_and_never_writes_to_redis(self):
        values, archives = fixture()
        class Client:
            calls = 0
            def scan_iter(self, pattern): return iter(archives)
            def pipeline(self):
                client = self
                class Pipeline:
                    watching = True
                    def __enter__(self): self.commands = []; return self
                    def __exit__(self, *args): pass
                    def watch(self, *keys): pass
                    def multi(self): self.watching = False
                    def mget(self, keys):
                        if self.watching: return [values.get(key) for key in keys]
                        self.commands.append(("mget", keys))
                    def hgetall(self, key): self.commands.append(("hgetall", key))
                    def execute(self):
                        client.calls += 1
                        if client.calls == 1: raise redis.WatchError()
                        return [[values.get(key) for key in args] if name == "mget" else archives[args.removesuffix(":downloads")] for name, args in self.commands]
                return Pipeline()
        client = Client()
        captured, files = capture(client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(captured[CURRENT], values[CURRENT])
        self.assertEqual(files, archives)

    def test_failed_replacement_restores_the_previous_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = root / "output"; output.mkdir()
            (output / "last-successful").write_text("keep")
            with self.assertRaises(FileNotFoundError): replace_directory(root / "missing-stage", output)
            self.assertEqual((output / "last-successful").read_text(), "keep")
