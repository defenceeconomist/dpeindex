"""File-based model work for staged updates; never connects to production stores."""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import uuid
from datetime import datetime, timezone
from importlib.metadata import version

from .topic_store import SUMMARY_FIELDS, corpus_fingerprint
from .topics import (Evaluator, calibrate_outliers, encode_abstracts, fit_model,
                     make_snapshot, projection, representative_review, write_json, tune)


def new_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]


def now():
    return datetime.now(timezone.utc).isoformat()


def centroid_classifier(embeddings, labels, strengths):
    """Calibrate without letting held-out papers contribute to validation centroids."""
    import numpy as np
    labels, strengths = np.asarray(labels), np.asarray(strengths)
    ids = sorted(set(labels) - {-1})
    rng = np.random.default_rng(42)
    held, centers = [], []
    for topic in ids:
        members = np.flatnonzero(labels == topic)
        confident = members[strengths[members] >= .8]
        size = min(max(1, int(len(confident) * .2)), max(0, len(members) - 3))
        selected = rng.choice(confident, size, replace=False) if size else np.array([], dtype=int)
        held.extend(selected.tolist())
        center = embeddings[np.setdiff1d(members, selected)].mean(axis=0)
        centers.append(center / np.linalg.norm(center))
    result = {"topic_ids": [int(x) for x in ids], "threshold": 1.0, "margin": 1.0,
              "minimum_agreement": .95, "validation_count": len(held),
              "accepted_validation": 0, "agreement": None, "ready": False,
              "basis": "Held-out agreement with discovered baseline assignments; not independent accuracy."}
    if len(held) >= 30 and len(ids) >= 2:
        scores = embeddings[held] @ np.stack(centers).T
        order = np.argsort(scores, axis=1)
        best = scores[np.arange(len(held)), order[:, -1]]
        margins = best - scores[np.arange(len(held)), order[:, -2]]
        correct = np.asarray(ids)[order[:, -1]] == labels[held]
        choices = []
        for threshold, margin in itertools.product(np.arange(.3, .951, .025), [.02, .04, .06, .08, .10]):
            accepted = (best >= threshold) & (margins >= margin)
            count = int(accepted.sum())
            agreement = float(correct[accepted].mean()) if count else 0.
            if count >= 20 and agreement >= .95:
                choices.append((count, agreement, float(threshold), float(margin)))
        if choices:
            count, agreement, threshold, margin = max(choices)
            result.update(ready=True, threshold=threshold, margin=margin,
                          accepted_validation=count, agreement=agreement)
    full = []
    for topic in ids:
        center = embeddings[labels == topic].mean(axis=0)
        full.append(center / np.linalg.norm(center))
    return result, np.stack(full).astype("float32")


def classify(embeddings, centers, calibration):
    import numpy as np
    scores = embeddings @ centers.T
    order = np.argsort(scores, axis=1)
    best = scores[np.arange(len(scores)), order[:, -1]]
    margins = best - scores[np.arange(len(scores)), order[:, -2]]
    accepted = (best >= calibration["threshold"]) & (margins >= calibration["margin"]) & calibration["ready"]
    labels = np.where(accepted, np.asarray(calibration["topic_ids"])[order[:, -1]], -1)
    return labels.tolist(), best.tolist(), margins.tolist()


def current_representation(snapshot, articles):
    """Recompute descriptive keywords without refitting or renumbering topics."""
    from bertopic import BERTopic
    from bertopic.vectorizers import ClassTfidfTransformer
    from sklearn.feature_extraction.text import CountVectorizer
    lookup = {a["handle"]: a for a in articles}
    labels = [d["topic"] for d in snapshot["documents"]]
    docs = [lookup[d["handle"]]["abstract"] for d in snapshot["documents"]]
    model = BERTopic(embedding_model=None, top_n_words=10)
    model.topics_ = labels
    model.update_topics(docs, vectorizer_model=CountVectorizer(stop_words="english", ngram_range=(1, 2)),
                        ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True))
    for topic in snapshot["topics"]:
        topic["count"] = labels.count(topic["id"])
        topic["keywords"] = ([{"term": word, "weight": float(weight)} for word, weight in model.get_topic(topic["id"])]
                             if topic["count"] else [])
    return model


def train(articles, settings, directory, cache, device, label_seed=None):
    import joblib
    import numpy as np
    from umap import UMAP
    usable = [a for a in articles if a["abstract"].strip()]
    if len(usable) < 60:
        raise ValueError("At least 60 complete abstracts are needed for a baseline rebuild.")
    directory.mkdir(parents=True, exist_ok=True)
    revision, params = settings["model_revision"], settings.get("params")
    embeddings, coverage = encode_abstracts(usable, revision, cache, device, 8)
    docs = [a["abstract"] for a in usable]
    evaluator = Evaluator(docs, embeddings)
    default = {"n_neighbors": 15, "n_components": 5, "min_cluster_size": 10, "min_samples": 10}
    untuned, untuned_labels = fit_model(docs, embeddings, projection(embeddings, default, 42), default)
    baseline_metrics = evaluator.measure(untuned, untuned_labels)
    del untuned
    tuning = {}
    if params is None:
        model, original, params, tuning = tune(docs, embeddings, evaluator, usable, directory)
    else:
        model, original = fit_model(docs, embeddings, projection(embeddings, params, 42), params)
    strengths = np.asarray(model.probabilities_).copy()
    before = evaluator.measure(model, original)
    final, similarities, outliers = calibrate_outliers(model, original, embeddings, docs)
    calibration, centers = centroid_classifier(embeddings, final, strengths)
    mapper = UMAP(n_neighbors=20, n_components=2, min_dist=0., metric="cosine", random_state=42,
                  transform_seed=42, n_jobs=1, low_memory=True).fit(embeddings)
    report = {"trials": [], "finalists": [], "baseline": baseline_metrics,
              "selected_before_reassignment": before, "outliers": outliers,
              "rebuild_from": settings.get("run_id"), **tuning}
    run_id = new_id()
    snapshot = make_snapshot(model, usable, articles, embeddings, original, final, similarities,
                             params, coverage, revision, run_id, report, map_projection=mapper)
    snapshot["report"]["selected_after_reassignment"] = evaluator.measure(model, final)
    snapshot["metadata"].update(model_id=run_id, trained_at=snapshot["metadata"]["built_at"],
        training_corpus_fingerprint=corpus_fingerprint(articles), training_abstract_count=len(usable),
        update_mode="rebuild", classifier=calibration)
    if label_seed and label_seed.get("corpus_fingerprint") == snapshot["metadata"]["corpus_fingerprint"]:
        from .topic_labels import apply_display_labels
        manifest = {**label_seed, "run_id": run_id}
        labelled = apply_display_labels(snapshot, manifest, source="inputs/labels.json")
        if "display_labels" not in labelled["metadata"]:
            raise ValueError("Reviewed label seed does not match baseline keywords, representatives and hierarchy")
        snapshot = labelled
    definition = {"model_id": run_id, "trained_at": snapshot["metadata"]["trained_at"],
                  "training_corpus_fingerprint": corpus_fingerprint(articles),
                  "classifier": calibration, "topics": copy.deepcopy(snapshot["topics"]),
                  "hierarchy": copy.deepcopy(snapshot["hierarchy"]),
                  "review": representative_review(model, final, embeddings, usable),
                  "versions": {name: version(name) for name in ["bertopic", "sentence-transformers", "transformers", "umap-learn", "hdbscan", "numpy", "scikit-learn", "torch"]}}
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / "centroids.npy", centers, allow_pickle=False)
    joblib.dump(mapper, directory / "map.joblib")
    write_json(directory / "definition.json", definition)
    write_json(directory / "training-coverage.json", coverage)
    model.save(str(directory / "bertopic"), serialization="safetensors", save_ctfidf=True,
               save_embedding_model=snapshot["metadata"]["model"])
    return snapshot, coverage


def incremental(articles, previous, model_dir, cache, device):
    import joblib
    import numpy as np
    definition = json.loads((model_dir / "definition.json").read_text())
    if definition["model_id"] != previous["metadata"].get("model_id", previous["metadata"]["run_id"]):
        raise ValueError("Previous assignments and frozen model differ.")
    previous = copy.deepcopy(previous)
    prior_articles = {a["handle"]: a for a in previous.pop("_articles")}
    prior_docs = {d["handle"]: d for d in previous["documents"]}
    usable = [a for a in articles if a["abstract"].strip()]
    embeddings, coverage = encode_abstracts(usable, previous["metadata"]["model_revision"], cache, device, 8)
    changed = [i for i, a in enumerate(usable) if a["handle"] not in prior_docs or
               a["abstract"] != prior_articles.get(a["handle"], {}).get("abstract")]
    assignments = {}
    if changed:
        centers = np.load(model_dir / "centroids.npy", allow_pickle=False)
        labels, best, margins = classify(embeddings[changed], centers, definition["classifier"])
        # Only load this trusted local artifact after its checkpoint hashes have been verified.
        mapper = joblib.load(model_dir / "map.joblib")
        positions = mapper.transform(embeddings[changed])
        for row, index in enumerate(changed):
            assignments[index] = {"topic": int(labels[row]), "original_topic": -1,
                "reassigned": labels[row] != -1, "similarity": float(best[row]),
                "margin": float(margins[row]), "assignment_method": "frozen_centroid",
                "x": float(positions[row, 0]), "y": float(positions[row, 1])}
    snapshot = copy.deepcopy(previous)
    snapshot["documents"] = []
    for i, article in enumerate(usable):
        document = copy.deepcopy(prior_docs.get(article["handle"], {}))
        document.update({key: article[key] for key in SUMMARY_FIELDS})
        document.update(assignments.get(i, {}))
        snapshot["documents"].append(document)
    meta = snapshot["metadata"]
    meta.update(run_id=new_id(), parent_run_id=previous["metadata"]["run_id"], built_at=now(),
                update_mode="incremental", corpus_fingerprint=corpus_fingerprint(articles),
                article_count=len(articles), abstract_count=len(usable), missing_abstract_count=len(articles)-len(usable),
                token_coverage={k: v for k, v in coverage.items() if k != "per_article"})
    meta["outliers"] = {**meta["outliers"], "before": sum(d["original_topic"] == -1 for d in snapshot["documents"]),
                        "after": sum(d["topic"] == -1 for d in snapshot["documents"])}
    current_representation(snapshot, articles)
    meta["representation_sha256"] = hashlib.sha256(json.dumps([t["keywords"] for t in snapshot["topics"]], sort_keys=True).encode()).hexdigest()
    snapshot["assignment_update"] = {"classified_count": len(changed), "preserved_count": len(usable)-len(changed),
                                      "classifier": definition["classifier"]}
    return snapshot, coverage
