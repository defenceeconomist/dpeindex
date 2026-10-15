"""Readable display names bound to a specific model; never used for training."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path

LABELS = Path(__file__).resolve().parent.parent / "data/topic-labels.json"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def label_manifest():
    try:
        return json.loads(LABELS.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Cannot read topic display labels; using extracted keywords.", exc_info=True)
        return None


def apply_display_labels(snapshot):
    manifest = label_manifest()
    metadata = snapshot["metadata"]
    if not manifest or any(metadata.get(key) != manifest.get(key) for key in ("run_id", "corpus_fingerprint")):
        return snapshot
    topics = {item["id"]: item for item in manifest["topics"]}
    branches = {tuple(sorted(item["topics"])): item["label"] for item in manifest["hierarchy"]}
    if (set(topics) != {item["id"] for item in snapshot["topics"]}
            or any(topics[item["id"]]["keywords"] != [word["term"] for word in item["keywords"]]
                   or topics[item["id"]]["representatives"] != item["representatives"] for item in snapshot["topics"])
            or set(branches) != {tuple(sorted(node["topics"])) for node in snapshot["hierarchy"] if node["children"]}):
        logger.warning("Topic display labels do not match the model; using extracted keywords.")
        return snapshot
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**snapshot,
            "metadata": {**metadata, "display_labels": {"source": LABELS.name, "basis": manifest["basis"], "fingerprint": fingerprint}},
            "topics": [{**item, "keyword_label": item["label"], "label": topics[item["id"]]["label"]} for item in snapshot["topics"]],
            "hierarchy": [{**node, "keyword_label": node["label"],
                           "label": branches[tuple(sorted(node["topics"]))] if node["children"] else topics[node["topics"][0]]["label"]}
                          for node in snapshot["hierarchy"]]}
