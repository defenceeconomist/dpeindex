import copy
import argparse
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import numpy as np

from app.update import ARTIFACTS, ROOT, diff_articles, hashes, initialise, lease, run, stages, step
from app.update_topics import centroid_classifier, classify, incremental


class SavedMap:
    def transform(self, vectors):
        return vectors[:, :2] + 10


def article(handle, abstract="peace", title=None):
    return {"handle": handle, "title": title or handle, "abstract": abstract, "authors": ["A"],
            "volume": "37", "issue": "4", "year": "2026", "pages": "1", "doi": "", "keywords": ""}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ARTIFACTS)
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_diff_corrections_removals_and_duplicates(self):
        a, b = article("a"), article("b")
        changed = {**a, "keywords": "corrected"}
        self.assertEqual(diff_articles([a, b], [changed, article("c")]),
                         {"added": ["c"], "removed": ["b"], "changed": ["a"], "unchanged": 0})
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            diff_articles([a], [a, a])

    def test_simulation_withholds_both_then_adds_in_arrival_order(self):
        inputs = self.directory / "inputs"; inputs.mkdir()
        rows = [article("a"), {**article("b"), "issue": "5"}, {**article("c"), "issue": "6"}]
        (inputs / "desired.json").write_text(json.dumps(rows))
        state = {"directory": str(self.directory.relative_to(ROOT)), "operation": "simulate", "issues": ["37:5", "37:6"]}
        groups = stages(state)
        self.assertEqual([len(g[1]) for g in groups], [1, 2, 2, 3, 3])
        self.assertEqual([a["handle"] for a in groups[0][1]], ["a"])
        self.assertEqual(groups[3][3], "01-incremental")

    def test_checkpoint_resume_verifies_outputs_and_failure_preserves_previous(self):
        state = {"directory": str(self.directory.relative_to(ROOT)), "steps": {}}
        output = self.directory / "output.json"
        def execute(command, log, env): output.write_text("complete")
        with patch("app.update.execute", side_effect=execute) as execute_mock:
            step(state, "stage", ["ignored"], [output])
            step(state, "stage", ["ignored"], [output])
            self.assertEqual(execute_mock.call_count, 1)
        output.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "changed or missing"):
            step(state, "stage", ["ignored"], [output])
        with patch("app.update.execute", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                step(state, "next", ["ignored"], [self.directory / "next"])
        self.assertEqual(state["steps"]["next"]["status"], "failed")
        self.assertTrue(output.exists())

    def test_workspace_lock_prevents_concurrent_updates(self):
        with lease(self.directory), self.assertRaisesRegex(RuntimeError, "Another update"):
            with lease(self.directory): pass

    def test_removal_guard_and_failed_preparation_are_retryable_without_source_changes(self):
        source = self.directory / "complete.redif"
        source.write_text("\n".join(f"Template-Type: ReDIF-Article 1.0\nHandle: h{i}\nTitle: Paper {i}\nAbstract: Peace and security\nVolume: 37\nIssue: 1\nYear: 2026\n" for i in range(1000)))
        original = source.read_bytes()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("inputs/records.json", "{}")
            zipped.writestr("inputs/decisions.json", "{}")
        frozen = {"articles": [article(f"h{i}") for i in range(1001)], "topics": {"metadata": {}},
                  "citations": {}, "points": {}, "proof": {}, "bundle": base64.b64encode(archive.getvalue()).decode()}
        output = self.directory / "run"
        args = argparse.Namespace(output=output, command="refresh", source=source, labels=None,
            previous=None, fetch=False, allow_removals=False, refresh_references=False, mode="rebuild",
            device="cpu", topic_python="python3")
        def checked(command, **kwargs):
            if command[:3] == ["docker", "image", "inspect"]: return "[]"
            return "test-version"
        with lease(output), patch("app.update.capture_production", return_value=frozen), patch("app.update.subprocess.check_output", side_effect=checked):
            with self.assertRaisesRegex(ValueError, "Source removes 1 articles"):
                initialise(args)
            self.assertEqual([p.name for p in output.iterdir()], [".lock"])
            args.allow_removals = True
            state = initialise(args)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(hashes([ROOT / p for p in state["input_hashes"]]), state["input_hashes"])

    def test_review_gate_cannot_export_or_build_site(self):
        inputs = self.directory / "inputs"; inputs.mkdir()
        for name, value in [("desired", [article("a")]), ("decisions", {}), ("topics", {"metadata": {}})]:
            (inputs / (name + ".json")).write_text(json.dumps(value))
        state = {"directory": str(self.directory.relative_to(ROOT)), "operation": "refresh", "mode": "rebuild",
                 "input_hashes": {}, "steps": {}, "releases": [], "device": "cpu", "topic_python": "python", "project": "test", "status": "prepared"}
        called = []
        def fake_step(state, name, command, outputs, env=None):
            called.append(name)
            if name.endswith("/graph"):
                (self.directory / "release/graph.json").write_text(json.dumps({"review": {"pending_count": 1, "doi_sample": []}}))
        with patch("app.update.bootstrap"), patch("app.update.step", side_effect=fake_step):
            run(state)
        self.assertEqual(state["status"], "needs_review")
        self.assertFalse(any(n.endswith(("/package", "/export", "/site")) for n in called))

    def test_calibration_abstains_when_evidence_is_inadequate(self):
        points = np.array([[1., 0.]] * 8 + [[0., 1.]] * 8)
        calibration, centers = centroid_classifier(points, [0]*8 + [1]*8, [1.]*16)
        self.assertFalse(calibration["ready"])
        self.assertEqual(classify(points[:2], centers, calibration)[0], [-1, -1])
        points = np.array([[1., 0.]] * 80 + [[0., 1.]] * 80)
        calibration, centers = centroid_classifier(points, [0]*80 + [1]*80, [1.]*160)
        self.assertTrue(calibration["ready"])
        self.assertGreaterEqual(calibration["agreement"], .95)
        self.assertEqual(classify(np.array([[1., 0.], [0., 1.]]), centers, calibration)[0], [0, 1])

    def test_incremental_preserves_existing_points_and_reclassifies_changed_abstracts(self):
        import joblib
        model = self.directory / "model"; model.mkdir()
        calibration = {"topic_ids": [0, 1], "threshold": .9, "margin": .1, "ready": True}
        definition = {"model_id": "baseline", "classifier": calibration}
        (model / "definition.json").write_text(json.dumps(definition))
        np.save(model / "centroids.npy", np.eye(2))
        joblib.dump(SavedMap(), model / "map.joblib")
        old = [article("a"), article("b"), article("missing", "")]
        previous = {"metadata": {"run_id": "old-snapshot", "model_id": "baseline", "model_revision": "pinned",
                                  "outliers": {"before": 0, "after": 0}},
            "documents": [{**a, "topic": i, "original_topic": i, "reassigned": False, "similarity": 1., "x": i+2., "y": i+3.}
                          for i, a in enumerate(old[:2])], "topics": [{"id": 0, "keywords": []}, {"id": 1, "keywords": []}], "hierarchy": [], "_articles": old}
        current = [{**old[0], "title": "corrected metadata"}, {**old[1], "abstract": "changed"},
                   article("missing", "new abstract"), article("still-missing", "")]
        coverage = {"per_article": []}
        with patch("app.update_topics.encode_abstracts", return_value=(np.array([[1.,0.],[1.,0.],[0.,1.]]), coverage)), \
             patch("app.update_topics.current_representation"):
            snapshot, _ = incremental(current, copy.deepcopy(previous), model, self.directory, "cpu")
        self.assertEqual(snapshot["documents"][0]["x"], previous["documents"][0]["x"])
        self.assertEqual(snapshot["documents"][0]["topic"], 0)
        self.assertEqual(snapshot["documents"][0]["title"], "corrected metadata")
        self.assertEqual(snapshot["documents"][1]["topic"], 0)
        self.assertEqual(snapshot["documents"][2]["topic"], 1)
        self.assertEqual(snapshot["metadata"]["model_id"], "baseline")
        self.assertNotEqual(snapshot["metadata"]["run_id"], "old-snapshot")
        self.assertEqual(snapshot["metadata"]["missing_abstract_count"], 1)
        self.assertEqual(snapshot["assignment_update"]["classified_count"], 2)


if __name__ == "__main__": unittest.main()
