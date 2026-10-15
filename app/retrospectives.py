"""Read-only comparison of an audited BERTopic run with editorial retrospectives."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path



REFERENCE = Path(__file__).resolve().parent.parent / "data" / "retrospectives.json"
RELATIONSHIPS = {"close", "broader", "narrower", "partial", "not_resolved"}
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def reference_data() -> dict:
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    validate_reference(data)
    return data


def validate_reference(data: dict) -> None:
    topics = {topic["id"] for topic in data["audited_topics"]}
    if len(topics) != len(data["audited_topics"]):
        raise ValueError("Duplicate audited topic IDs")
    for period in data["periods"]:
        if sum(category["count"] for category in period["categories"]) != period["classified_total"]:
            raise ValueError("Editorial counts must sum to the original classified-paper total")
        names = [category["name"] for category in period["categories"]]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate editorial categories")
        for category in period["categories"]:
            if category["count"] < 0 or category["relationship"] not in RELATIONSHIPS:
                raise ValueError("Invalid editorial count or relationship")
            if not category["definition"] or not category["note"]:
                raise ValueError("Crosswalk categories require definitions and interpretation")
            if not set(category["topic_ids"]) <= topics or len(set(category["topic_ids"])) != len(category["topic_ids"]):
                raise ValueError("Crosswalk refers to unknown or duplicate topics")
            if (category["relationship"] == "not_resolved") != (not category["topic_ids"]):
                raise ValueError("Unresolved categories must remain explicit and unmapped")
        if not period["source"]["doi"].startswith("10."):
            raise ValueError("Invalid source DOI")
    handles = [record["handle"] for record in data["missing_abstract_records"]]
    if len(handles) != len(set(handles)):
        raise ValueError("Duplicate missing-abstract records")


def in_period(record: dict, period: dict) -> bool:
    """Publication volume governs the cohort, not dates mentioned in an abstract."""
    volume = str(record.get("volume", ""))
    if not volume.isdigit() or not period["volume_start"] <= int(volume) <= period["volume_end"]:
        return False
    if int(volume) == period["volume_end"] and "last_issue" in period:
        issue = re.fullmatch(r"(\d+)(?:\s*[-–]\s*(\d+))?", str(record.get("issue", "")))
        if not issue:
            return False
        start, end = int(issue[1]), int(issue[2] or issue[1])
        return 1 <= start <= end <= period["last_issue"]
    return True


def validate_audited_snapshot(snapshot: dict, reference: dict) -> None:
    binding, metadata = reference["audited_model"], snapshot["metadata"]
    if any(metadata[key] != binding[key] for key in ("run_id", "corpus_fingerprint")):
        raise ValueError("Comparison must use its audited run and corpus")
    expected = {topic["id"]: topic["keywords"] for topic in reference["audited_topics"]}
    actual = {topic["id"]: [word["term"] for word in topic["keywords"]] for topic in snapshot["topics"]}
    if expected != actual:
        raise ValueError("Topic meanings differ from the reviewed crosswalk")
    documents = snapshot["documents"]
    handles = [document["handle"] for document in documents]
    if len(set(handles)) != len(handles) or len(handles) != metadata["abstract_count"]:
        raise ValueError("Audited abstract identities are inconsistent")
    missing_handles = {record["handle"] for record in reference["missing_abstract_records"]}
    if set(handles) & missing_handles or len(missing_handles) != metadata["missing_abstract_count"]:
        raise ValueError("Missing-abstract evidence does not match the audited run")
    if len(handles) + len(missing_handles) != metadata["article_count"]:
        raise ValueError("Audited catalog coverage is inconsistent")
    if not {document["topic"] for document in documents} <= set(expected) | {-1}:
        raise ValueError("Unknown audited topic assignment")


def period_comparison(snapshot: dict, reference: dict, period: dict) -> dict:
    documents = [document for document in snapshot["documents"] if in_period(document, period)]
    missing = [record for record in reference["missing_abstract_records"] if in_period(record, period)]
    counts = Counter(document["topic"] for document in documents)
    denominator = len(documents)
    by_handle = {document["handle"]: document for document in documents}
    model_topics = []
    for topic in reference["audited_topics"]:
        examples = []
        for handle in reference["reviewed_examples"].get(period["id"], {}).get(str(topic["id"]), []):
            document = by_handle.get(handle)
            if document is None or document["topic"] != topic["id"]:
                raise ValueError("Reviewed example falls outside its period or topic")
            examples.append({key: document[key] for key in ("handle", "title", "authors", "year", "volume", "issue")})
        count = counts[topic["id"]]
        model_topics.append({"id": topic["id"], "name": topic["name"], "count": count,
                             "share": 100 * count / denominator if denominator else 0.0,
                             "examples": examples})
    # Each editorial row retains its individual overlapping matches. There is
    # deliberately no "matched total": the same topic can recur in several rows.
    categories = [{**category, "share": 100 * category["count"] / period["classified_total"]}
                  for category in period["categories"]]
    volumes = sorted({int(record["volume"]) for record in [*documents, *missing]})
    return {**period, "categories": categories, "model_topics": model_topics,
            "coverage": {"record_count": denominator + len(missing), "abstract_count": denominator,
                         "missing_abstract_count": len(missing), "unassigned_count": counts[-1],
                         "unassigned_share": 100 * counts[-1] / denominator if denominator else 0.0,
                         "assigned_count": denominator - counts[-1], "available_volumes": volumes}}


def build_comparison(snapshot: dict, reference: dict, active: dict) -> dict:
    validate_reference(reference)
    validate_audited_snapshot(snapshot, reference)
    binding = reference["audited_model"]
    same_model = all(active["metadata"].get(key) == binding[key] for key in ("run_id", "corpus_fingerprint"))
    return {"status": "ready" if same_model else "review_required",
            "links_enabled": same_model,
            "audited_model": binding, "active_run_id": active["metadata"]["run_id"],
            "method": reference["method"],
            "periods": [period_comparison(snapshot, reference, period) for period in reference["periods"]]}


def load_comparison(active: dict, client=None) -> dict:
    if active.get("status") != "ready":
        return {"status": "not_built", "links_enabled": False}
    try:
        reference = reference_data()
        binding = reference["audited_model"]
        same_model = all(active["metadata"].get(key) == binding[key] for key in ("run_id", "corpus_fingerprint"))
        if same_model:
            snapshot = active
        else:
            path = Path("data/topic-artifacts/review") / binding["run_id"] / "snapshot.json"
            raw = client.get("dpe:topics:run:" + binding["run_id"]) if client else (path.read_text() if path.exists() else None)
            if not raw:
                return {"status": "unavailable", "links_enabled": False, "audited_model": binding,
                        "message": "The archived model for this comparison is unavailable. The current topic explorer remains available."}
            snapshot = json.loads(raw)
        return build_comparison(snapshot, reference, active)
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Retrospective comparison evidence could not be validated")
        return {"status": "unavailable", "links_enabled": False,
                "message": "The retrospective comparison is temporarily unavailable. The current topic explorer remains available."}
