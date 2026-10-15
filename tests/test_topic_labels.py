import copy
import unittest
from unittest.mock import patch

from app.topic_labels import apply_display_labels


class TopicLabelTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "metadata": {"run_id": "reviewed-run", "corpus_fingerprint": "reviewed-corpus"},
            "topics": [
                {"id": 0, "label": "war · peace", "keywords": [{"term": "war", "weight": .3}], "representatives": ["a"], "count": 1},
                {"id": 1, "label": "arms · trade", "keywords": [{"term": "trade", "weight": .2}], "representatives": ["b"], "count": 1}],
            "hierarchy": [
                {"id": "0", "label": "war · peace", "topics": [0], "children": [], "distance": 0},
                {"id": "1", "label": "arms · trade", "topics": [1], "children": [], "distance": 0},
                {"id": "2", "label": "war · arms", "topics": [0, 1], "children": ["0", "1"], "distance": .8}],
            "documents": [{"handle": "a", "topic": 0, "x": 1., "y": 2.}, {"handle": "b", "topic": 1, "x": 3., "y": 4.}],
            "report": {"coherence": .5}}
        self.manifest = {**self.snapshot["metadata"], "basis": "Keywords and representative abstracts",
                         "topics": [{"id": t["id"], "label": label, "keywords": [w["term"] for w in t["keywords"]],
                                     "representatives": t["representatives"]}
                                    for t, label in zip(self.snapshot["topics"], ["Conflict and peace", "Arms trade"])],
                         "hierarchy": [{"topics": [0, 1], "label": "All research themes"}]}

    def test_names_are_consistent_without_changing_the_model(self):
        before = copy.deepcopy(self.snapshot)
        with patch("app.topic_labels.label_manifest", return_value=self.manifest):
            labelled = apply_display_labels(self.snapshot)
        self.assertEqual(self.snapshot, before)
        self.assertEqual(labelled["documents"], before["documents"])
        self.assertEqual(labelled["report"], before["report"])
        for topic, node, original in zip(labelled["topics"], labelled["hierarchy"], before["topics"]):
            self.assertEqual(topic["label"], node["label"])
            self.assertEqual(topic["keyword_label"], original["label"])
            self.assertEqual(topic["keywords"], original["keywords"])
            self.assertEqual(topic["count"], original["count"])
        self.assertEqual(labelled["hierarchy"][-1]["label"], "All research themes")
        self.assertEqual([n["distance"] for n in labelled["hierarchy"]], [n["distance"] for n in before["hierarchy"]])
        self.assertRegex(labelled["metadata"]["display_labels"]["fingerprint"], r"^[0-9a-f]{64}$")

    def test_other_runs_and_changed_evidence_keep_keyword_labels(self):
        for change in ("run_id", "corpus_fingerprint", "keywords", "representatives", "hierarchy"):
            snapshot = copy.deepcopy(self.snapshot)
            if change in ("run_id", "corpus_fingerprint"):
                snapshot["metadata"][change] = "different"
            elif change == "hierarchy":
                snapshot["hierarchy"][-1]["topics"] = [0]
            else:
                snapshot["topics"][0][change] = []
            with self.subTest(change=change), patch("app.topic_labels.label_manifest", return_value=self.manifest):
                self.assertIs(apply_display_labels(snapshot), snapshot)
        with patch("app.topic_labels.label_manifest", return_value=None):
            self.assertIs(apply_display_labels(self.snapshot), self.snapshot)
