"""Retrospective seed provenance and diagnostics; ML imports stay offline."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent.parent / "data" / "retrospectives.json"
QUALITY_WEIGHTS = {"coherence": 2, "silhouette": 2, "diversity": 1, "coverage": 1}
GUIDED_WEIGHTS = {**QUALITY_WEIGHTS, "alignment": 2}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def seed_topic_list(guidance):
    return [topic["terms"] for topic in guidance["manifest"]["topics"]] if guidance else None


def load_guidance(path, reference=None):
    """Resolve only editorial evidence, never counts or the old model crosswalk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    reference = reference if reference is not None else json.loads(REFERENCE.read_text(encoding="utf-8"))
    periods = {period["id"]: period for period in reference["periods"]}
    if data.get("schema_version") != 1 or not data.get("topics"):
        raise ValueError("Unsupported or empty topic seed manifest")
    resolved, identifiers, covered = [], set(), set()
    for topic in data["topics"]:
        identifier, terms = topic["id"], topic["terms"]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", identifier) or identifier in identifiers:
            raise ValueError("Invalid or duplicate seed identifier")
        identifiers.add(identifier)
        if (not topic["name"].strip() or len(terms) < 2 or
                any(not isinstance(term, str) or not term.strip() or term != term.strip() for term in terms) or
                len({term.casefold() for term in terms}) != len(terms)):
            raise ValueError("Seed themes require a name and distinct nonempty terms")
        sources = []
        for source in topic["sources"]:
            period = periods[source["period"]]
            category = next((item for item in period["categories"] if item["name"] == source["category"]), None)
            if category is None:
                raise ValueError("Seed refers to an unknown retrospective category")
            covered.add((period["id"], category["name"]))
            sources.append({"period": period["id"], "category": category["name"],
                            "definition": category["definition"],
                            "source": {key: period["source"][key] for key in ("doi", "locator", "sha256")}})
        if not sources:
            raise ValueError("Every seed theme needs editorial provenance")
        resolved.append({"id": identifier, "name": topic["name"], "terms": list(terms), "sources": sources})
    excluded = data.get("excluded_categories", [])
    allowed_exclusions = {"Others", "Country surveys", "Company surveys"}
    if set(excluded) != allowed_exclusions:
        raise ValueError("Exclude only the residual and country/company survey formats")
    expected = {(period["id"], category["name"]) for period in periods.values()
                for category in period["categories"] if category["name"] not in excluded}
    if covered != expected:
        raise ValueError("Every substantive retrospective category must have seed provenance")
    manifest = {"schema_version": 1, "topics": resolved, "excluded_categories": excluded}
    return {"method": "retrospective_seeds", "fingerprint": fingerprint(manifest), "manifest": manifest}


def validate_guidance(guidance):
    if guidance is not None and (guidance.get("method") != "retrospective_seeds" or
                                 fingerprint(guidance["manifest"]) != guidance["fingerprint"]):
        raise ValueError("Saved seed guidance is inconsistent")
    return guidance


def saved_settings(metadata):
    settings = {"params": metadata["params"], "revision": metadata["model_revision"], "run_id": metadata["run_id"]}
    if metadata.get("guidance"):
        settings["guidance"] = validate_guidance(metadata["guidance"])
    return settings


def cached_embedding_backend(encode, initial=None):
    """A native BERTopic backend with the same revision/text cache as abstracts."""
    import numpy as np
    from bertopic.backend import BaseEmbedder

    class CachedBackend(BaseEmbedder):
        def __init__(self):
            super().__init__()
            self.cache = dict(initial or {})

        def embed(self, documents, verbose=False):
            missing = list(dict.fromkeys(text for text in documents if text not in self.cache))
            if missing:
                vectors = encode(missing)
                self.cache.update(zip(missing, vectors, strict=True))
            return np.stack([self.cache[text] for text in documents])

    return CachedBackend()


def alignment(labels, embeddings, guidance, seed_embeddings):
    """One-to-one matching, using unmodified document embeddings throughout."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from sklearn.metrics.pairwise import cosine_similarity

    labels = np.asarray(labels)
    ids = sorted(set(labels.tolist()) - {-1})
    matches = [{"seed_id": topic["id"], "name": topic["name"], "topic": None,
                "similarity": 0.0, "count": 0} for topic in guidance["manifest"]["topics"]]
    if ids:
        centers = np.stack([embeddings[labels == topic].mean(axis=0) for topic in ids])
        similarities = cosine_similarity(seed_embeddings, centers)
        rows, columns = linear_sum_assignment(-similarities)
        for row, column in zip(rows, columns, strict=True):
            matches[row].update(topic=int(ids[column]), similarity=float(similarities[row, column]),
                                count=int((labels == ids[column]).sum()))
    return {"score": sum(item["similarity"] for item in matches) / len(matches), "matches": matches}


def period_alignment(articles, labels, embeddings, guidance, seed_embeddings, reference):
    import numpy as np
    from .retrospectives import in_period

    labels = np.asarray(labels)
    result = []
    for period in reference["periods"]:
        indices = [i for i, article in enumerate(articles) if in_period(article, period)]
        assigned = labels[indices]
        result.append({"period": period["id"], "abstract_count": len(indices),
                       "unassigned_count": int((assigned == -1).sum()),
                       **alignment(assigned, embeddings[indices], guidance, seed_embeddings)})
    return result
