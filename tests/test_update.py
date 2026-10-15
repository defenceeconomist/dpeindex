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

from app.update import citation_decisions_match, ARTIFACTS, ROOT, diff_articles, hashes, lease, run, stage
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



    def test_changed_citation_labels_invalidate_reuse_without_corpus_changes(self):
        from app.citation_data import digest
        previous = self.directory / 'release'
        previous.mkdir()
        decisions = self.directory / 'decisions.json'
        original = {'communities': {'membership': {'label': 'Old label'}}}
        decisions.write_text(json.dumps(original))
        manifest = previous / 'release-manifest.json'
        manifest.write_text(json.dumps({'citation_decisions_sha256': digest(original)}))
        self.assertTrue(citation_decisions_match(previous, decisions))
        decisions.write_text(json.dumps({'communities': {'membership': {'label': 'Reviewed label'}}}))
        self.assertFalse(citation_decisions_match(previous, decisions))
        manifest.write_text('{}')
        self.assertFalse(citation_decisions_match(previous, decisions))
        inputs = self.directory / 'inputs'
        inputs.mkdir()
        (inputs / 'decisions.json').write_text(json.dumps(original))
        self.assertFalse(citation_decisions_match(previous, decisions))
        decisions.write_text(json.dumps(original))
        self.assertTrue(citation_decisions_match(previous, decisions))

    def test_workspace_lock_prevents_concurrent_updates(self):
        with lease(self.directory), self.assertRaisesRegex(RuntimeError, "Another update"):
            with lease(self.directory): pass



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
        self.assertEqual(classify(np.array([[2**-.5, 2**-.5]]), centers, calibration)[0], [-1])

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
