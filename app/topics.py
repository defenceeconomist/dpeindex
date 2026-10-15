"""Explicit, local-only BERTopic builds. No hosted inference or generative labels."""
from __future__ import annotations

import argparse
import gc
import hashlib
import itertools
import json
import os
import signal
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .topic_store import SUMMARY_FIELDS, corpus_fingerprint, publish_snapshot, read_corpus, validate_snapshot
from .topic_guidance import (GUIDED_WEIGHTS, QUALITY_WEIGHTS, alignment, cached_embedding_backend,
                             load_guidance, period_alignment, saved_settings, seed_topic_list,
                             validate_guidance)

MODEL = "Qwen/Qwen3-Embedding-0.6B"
SEEDS = [42, 7, 21]
METRICS = QUALITY_WEIGHTS
GRID = {"n_neighbors": [10, 20, 40], "n_components": [5, 10],
        "min_cluster_size": [20, 35, 50], "min_samples": [1, 3, 5]}


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}", flush=True)


def embedding_key(abstract: str, revision: str) -> str:
    return hashlib.sha256(json.dumps([MODEL, revision, abstract], ensure_ascii=False).encode()).hexdigest()


def check_token_coverage(articles: list[dict], tokenizer, sequence_limit: int) -> dict:
    """The exact texts later given to encode(), with special tokens and no truncation."""
    counts = []
    for offset in range(0, len(articles), 64):
        encoded = tokenizer([a["abstract"] for a in articles[offset:offset + 64]],
                            truncation=False, padding=False, add_special_tokens=True)
        counts.extend(len(ids) for ids in encoded["input_ids"])
    oversized = [f"{a['handle']} ({count} tokens)" for a, count in zip(articles, counts, strict=True)
                 if count > sequence_limit]
    if oversized:
        raise ValueError(f"Abstracts exceed the {sequence_limit}-token encoder limit; nothing was truncated: "
                         + "; ".join(oversized))
    return {"sequence_limit": sequence_limit, "max_tokens": max(counts, default=0),
            "total_tokens": sum(counts), "truncated_abstracts": 0,
            "checked_abstracts": len(articles),
            "per_article": [{"handle": a["handle"], "tokens": count}
                            for a, count in zip(articles, counts, strict=True)]}


def encode_abstracts(articles: list[dict], revision: str, artifacts: Path,
                     device: str, batch_size: int) -> tuple:
    import numpy as np
    from transformers import AutoConfig, AutoTokenizer

    config = AutoConfig.from_pretrained(MODEL, revision=revision)
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=revision)
    limits = [32768, tokenizer.model_max_length, config.max_position_embeddings]
    limit = min(int(value) for value in limits if value and 0 < int(value) < 10**8)
    coverage = check_token_coverage(articles, tokenizer, limit)
    cache = artifacts / "embeddings"
    cache.mkdir(parents=True, exist_ok=True)
    paths = [cache / (embedding_key(a["abstract"], revision) + ".npy") for a in articles]
    vectors = [None] * len(articles)
    missing = []
    dimension = config.hidden_size
    for i, path in enumerate(paths):
        try:
            vector = np.load(path, allow_pickle=False)
            if vector.shape != (dimension,) or not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-3):
                raise ValueError("Invalid cached embedding")
            vectors[i] = vector
        except (OSError, ValueError):
            missing.append(i)
    log(f"Token coverage checked: {coverage['max_tokens']} maximum / {limit} limit; "
        f"{len(missing)} embeddings to compute, {len(articles) - len(missing)} cached")
    if not missing:
        return np.stack(vectors), coverage
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    log(f"Loading {MODEL} at {revision} on {device}")
    encoder = SentenceTransformer(MODEL, revision=revision, device=device,
                                  model_kwargs={"dtype": torch.float32})
    encoder.max_seq_length = limit
    # Disable model-supplied default prompts: coverage and encoding see identical text.
    encoder.default_prompt_name = None
    last_log = time.monotonic()
    for start in range(0, len(missing), batch_size):
        indices = missing[start:start + batch_size]
        batch = encoder.encode([articles[i]["abstract"] for i in indices],
                               batch_size=batch_size, normalize_embeddings=True,
                               show_progress_bar=False, convert_to_numpy=True, prompt="")
        for i, vector in zip(indices, batch, strict=True):
            vector = np.asarray(vector, dtype=np.float32)
            temporary = paths[i].with_suffix(".tmp.npy")
            np.save(temporary, vector, allow_pickle=False)
            temporary.replace(paths[i])
            vectors[i] = vector
        if start == 0 or time.monotonic() - last_log >= 20 or start + batch_size >= len(missing):
            log(f"Embedded {min(start + batch_size, len(missing))}/{len(missing)} abstracts")
            last_log = time.monotonic()
    embeddings = np.stack(vectors)
    del encoder
    gc.collect()
    if device.startswith("mps"):
        torch.mps.empty_cache()
    elif device.startswith("cuda"):
        torch.cuda.empty_cache()
    return embeddings, coverage


class FrozenProjection:
    """BERTopic adapter: reuse one UMAP fit while retaining original topic embeddings."""
    def __init__(self, reduced):
        self.reduced = reduced

    def fit(self, X, y=None):
        if len(X) != len(self.reduced):
            raise ValueError("Projection and corpus differ")
        return self

    def transform(self, X):
        if len(X) != len(self.reduced):
            raise ValueError("This training projection cannot transform unseen documents")
        return self.reduced


def projection(embeddings, params: dict, seed: int, dimensions=None, y=None):
    from umap import UMAP
    return UMAP(n_neighbors=params["n_neighbors"], n_components=dimensions or params["n_components"],
                min_dist=0.0, metric="cosine", random_state=seed,
                n_jobs=1, low_memory=True).fit_transform(embeddings, y=y)


class CachedProjection:
    """Cache the actual native guided inputs, including UMAP supervision."""
    def __init__(self, params, seed, cache, guidance=None):
        self.params, self.seed, self.cache = params, seed, cache
        self.guidance = guidance["fingerprint"] if guidance else None

    def fit_transform(self, X, y=None):
        import numpy as np
        values = np.ascontiguousarray(X)
        targets = np.ascontiguousarray(y) if y is not None else None
        key = (self.params["n_neighbors"], self.params["n_components"], self.seed, self.guidance,
               values.shape, values.dtype.str, hashlib.sha256(values.tobytes()).hexdigest(),
               None if targets is None else (targets.shape, targets.dtype.str,
                                             hashlib.sha256(targets.tobytes()).hexdigest()))
        if key not in self.cache:
            self.cache[key] = projection(X, self.params, self.seed, y=y)
        return self.cache[key]


def reset_centroids(model, labels, embeddings):
    import numpy as np
    labels = np.asarray(labels)
    model.topic_embeddings_ = np.stack([embeddings[labels == topic].mean(axis=0)
                                        for topic in sorted(set(labels.tolist()))])


def refresh_topics(model, docs, labels, embeddings):
    # Refresh keywords without encoding keyword strings into surrogate centroids.
    backend, model.embedding_model = model.embedding_model, None
    try:
        model.update_topics(docs, topics=labels, vectorizer_model=model.vectorizer_model,
                            ctfidf_model=model.ctfidf_model, n_gram_range=(1, 2), top_n_words=model.top_n_words)
    finally:
        model.embedding_model = backend
    reset_centroids(model, labels, embeddings)


def fit_model(docs, embeddings, reduced, params, guidance=None, backend=None, cache=None, seed=42):
    from bertopic import BERTopic
    from bertopic.vectorizers import ClassTfidfTransformer
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    if guidance and backend is None:
        raise ValueError("Native seed guidance requires its embedding backend")
    reducer = (FrozenProjection(reduced) if reduced is not None and not guidance else
               CachedProjection(params, seed, cache if cache is not None else {}, guidance))
    model = BERTopic(embedding_model=backend if guidance else None, umap_model=reducer,
                     seed_topic_list=seed_topic_list(guidance),
                     hdbscan_model=HDBSCAN(min_cluster_size=params["min_cluster_size"],
                                           min_samples=params["min_samples"], metric="euclidean",
                                           cluster_selection_method="eom", prediction_data=True),
                     vectorizer_model=CountVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1),
                     ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True),
                     top_n_words=10, calculate_probabilities=False)
    # BERTopic sets language=None for custom embedding backends, which otherwise
    # changes punctuation preprocessing relative to the unseeded/control model.
    model.language = "english"
    # BERTopic's native guidance mutates its input array in place.
    labels, _ = model.fit_transform(docs, embeddings.copy())
    reset_centroids(model, labels, embeddings)
    return model, labels


class Evaluator:
    def __init__(self, docs, embeddings, guidance=None, seed_embeddings=None):
        from sklearn.feature_extraction.text import CountVectorizer
        self.embeddings = embeddings
        self.count = len(docs)
        self.guidance, self.seed_embeddings = guidance, seed_embeddings
        analyzer = CountVectorizer(stop_words="english", ngram_range=(1, 2)).build_analyzer()
        self.occurrences = defaultdict(set)
        for i, doc in enumerate(docs):
            for term in set(analyzer(doc)):
                self.occurrences[term].add(i)
        self.pairs = {}

    def npmi(self, first, second):
        import math
        pair = tuple(sorted((first, second)))
        if pair not in self.pairs:
            left, right = self.occurrences[first], self.occurrences[second]
            joint = len(left & right) / self.count
            self.pairs[pair] = (-1.0 if joint == 0 else 1.0 if joint == 1 else
                                math.log(joint / ((len(left) / self.count) * (len(right) / self.count))) / -math.log(joint))
        return self.pairs[pair]

    def measure(self, model, labels):
        import numpy as np
        from sklearn.metrics import silhouette_score
        ids = sorted(set(labels) - {-1})
        words = [[word for word, _ in model.get_topic(topic) if word][:10] for topic in ids]
        coherence = [self.npmi(a, b) for topic in words for a, b in itertools.combinations(topic, 2)]
        selected = np.array(labels) != -1
        separation = -1.0
        if len(ids) >= 2 and selected.sum() > len(ids):
            separation = float(silhouette_score(self.embeddings[selected], np.asarray(labels)[selected], metric="cosine"))
        result = {"topic_count": len(ids), "outliers": int((~selected).sum()),
                "coherence": float(np.mean(coherence)) if coherence else -1.0,
                "silhouette": separation,
                "diversity": len(set(itertools.chain.from_iterable(words))) / max(1, sum(map(len, words))),
                "coverage": float(selected.mean())}
        if self.guidance:
            result["alignment"] = alignment(labels, self.embeddings, self.guidance, self.seed_embeddings)["score"]
        return result


def score(metrics, bounds, weights=METRICS):
    return sum(weight * max(0, min(1, (metrics[key] - bounds[key][0]) / max(1e-9, bounds[key][1] - bounds[key][0])))
               for key, weight in weights.items()) / sum(weights.values())


def representative_review(model, labels, embeddings, articles):
    import numpy as np
    review = []
    labels = np.asarray(labels)
    for topic in sorted(set(labels) - {-1}):
        indices = np.flatnonzero(labels == topic)
        center = embeddings[indices].mean(axis=0)
        center /= np.linalg.norm(center)
        nearest = indices[np.argsort(-(embeddings[indices] @ center))[:3]]
        review.append({"topic": int(topic), "keywords": [w for w, _ in model.get_topic(int(topic))],
                       "count": len(indices), "representatives": [
                           {"handle": articles[i]["handle"], "title": articles[i]["title"], "abstract": articles[i]["abstract"]}
                           for i in nearest]})
    return review


def tune(docs, embeddings, evaluator, articles, run_dir, guidance=None, backend=None, cache=None):
    import numpy as np
    from sklearn.metrics import adjusted_mutual_info_score
    cache = cache if cache is not None else {}
    modes = {"unseeded": None, "seeded": guidance} if guidance else {"unseeded": None}
    weights = GUIDED_WEIGHTS if guidance else METRICS
    trials = {name: [] for name in modes}
    keys = list(GRID)
    for name, mode in modes.items():
        for values in itertools.product(*(GRID[key] for key in keys)):
            params = dict(zip(keys, values, strict=True))
            model, labels = fit_model(docs, embeddings, None, params, mode, backend, cache)
            metrics = evaluator.measure(model, labels)
            trials[name].append({"params": params, "seed": 42, "metrics": metrics})
            log(f"{name} trial {len(trials[name])}/54: {metrics['topic_count']} topics, "
                f"{metrics['outliers']} outliers, coherence {metrics['coherence']:.3f}, "
                f"silhouette {metrics['silhouette']:.3f}" +
                (f", alignment {metrics['alignment']:.3f}" if guidance else ""))
            write_json(run_dir / ("trials.json" if name == "seeded" or not guidance else "control-trials.json"), trials[name])
    valid = [trial for mode in trials.values() for trial in mode if trial["metrics"]["topic_count"] >= 2]
    if not valid:
        raise RuntimeError("Tuning found no multi-topic model; the previous published run is unchanged.")
    bounds = {key: [min(t["metrics"][key] for t in valid), max(t["metrics"][key] for t in valid)] for key in weights}
    winners = {}
    for name, mode in modes.items():
        eligible = [trial for trial in trials[name] if trial["metrics"]["topic_count"] >= 2]
        if not eligible:
            raise RuntimeError(f"No multi-topic model in the {name} comparison arm")
        for trial in eligible:
            trial["score"] = score(trial["metrics"], bounds, weights)
        validation = []
        for trial in sorted(eligible, key=lambda item: item["score"], reverse=True)[:3]:
            params, seed_metrics, seed_labels = trial["params"], [], []
            for seed in SEEDS:
                model, labels = fit_model(docs, embeddings, None, params, mode, backend, cache, seed)
                seed_metrics.append({"seed": seed, "metrics": evaluator.measure(model, labels)})
                seed_labels.append(labels)
            stability = float(np.mean([adjusted_mutual_info_score(a, b) for a, b in itertools.combinations(seed_labels, 2)]))
            validation.append({"params": params, "seeds": seed_metrics, "stability_ami": stability,
                               "mean_score": float(np.mean([score(item["metrics"], bounds, weights) for item in seed_metrics])),
                               "review": representative_review(model, labels, embeddings, articles)})
            log(f"{name} finalist {params}: score {validation[-1]['mean_score']:.3f}, stability {stability:.3f}")
        selected = max(validation, key=lambda item: (item["mean_score"], item["stability_ami"]))
        params = selected["params"]
        model, labels = fit_model(docs, embeddings, None, params, mode, backend, cache)
        report = {"trials": trials[name], "finalists": validation, "metric_weights": weights, "metric_bounds": bounds,
                  "selection": "Mean weighted normalised quality and alignment across three seeds; stability breaks ties. No topic-count preference."
                  if guidance else "Mean weighted normalised quality across three seeds; stability breaks ties. No topic-count preference."}
        winners[name] = (model, labels, params, report)
        write_json(run_dir / ("trials.json" if name == "seeded" or not guidance else "control-trials.json"), trials[name])
    chosen = winners["seeded" if guidance else "unseeded"]
    return (*chosen, winners.get("unseeded") if guidance else None)


def calibrate_outliers(model, labels, embeddings, docs):
    """Hold out confident members from centroids; require 95% label agreement."""
    import numpy as np
    labels = np.asarray(labels)
    ids = sorted(set(labels) - {-1})
    if len(ids) < 2:
        raise ValueError("Outlier calibration needs at least two topics")
    reset_centroids(model, labels, embeddings)
    strengths = np.asarray(model.probabilities_)
    rng = np.random.default_rng(42)
    held_out, centers = [], []
    for topic in ids:
        members = np.flatnonzero(labels == topic)
        confident = members[strengths[members] >= .8]
        size = min(max(1, int(len(confident) * .2)), max(0, len(members) - 3))
        held = rng.choice(confident, size=size, replace=False) if size else np.array([], dtype=int)
        held_out.extend(held.tolist())
        remaining = np.setdiff1d(members, held)
        center = embeddings[remaining].mean(axis=0)
        centers.append(center / np.linalg.norm(center))
    report = {"minimum_agreement": .95, "validation_count": len(held_out),
              "threshold": 1.0, "margin": 1.0, "agreement": None, "accepted_validation": 0}
    if len(held_out) >= 30:
        sims = embeddings[held_out] @ np.stack(centers).T
        order = np.argsort(sims, axis=1)
        best = sims[np.arange(len(sims)), order[:, -1]]
        margins = best - sims[np.arange(len(sims)), order[:, -2]]
        correct = np.asarray(ids)[order[:, -1]] == labels[held_out]
        choices = []
        for threshold, margin in itertools.product(np.arange(.3, .951, .025), [.02, .04, .06, .08, .10]):
            accepted = (best >= threshold) & (margins >= margin)
            count = int(accepted.sum())
            agreement = float(correct[accepted].mean()) if count else 0.0
            if count >= 20 and agreement >= .95:
                choices.append((count, agreement, float(threshold), margin))
        if choices:
            count, agreement, threshold, margin = max(choices, key=lambda c: (c[0], c[1], c[2], c[3]))
            report.update(threshold=threshold, margin=margin, agreement=agreement, accepted_validation=count)
    # Recalculate full original-member centroids for reassignment, excluding topic -1.
    centers = []
    for topic in ids:
        center = embeddings[labels == topic].mean(axis=0)
        centers.append(center / np.linalg.norm(center))
    sims = embeddings @ np.stack(centers).T
    order = np.argsort(sims, axis=1)
    best = sims[np.arange(len(sims)), order[:, -1]]
    margins = best - sims[np.arange(len(sims)), order[:, -2]]
    updated = labels.copy()
    if (labels == -1).any() and report["accepted_validation"]:
        proposed = model.reduce_outliers(docs, labels.tolist(), strategy="embeddings", embeddings=embeddings,
                                        threshold=report["threshold"])
        for i in np.flatnonzero(labels == -1):
            if best[i] >= report["threshold"] and margins[i] >= report["margin"]:
                updated[i] = proposed[i]
    report.update(before=int((labels == -1).sum()), after=int((updated == -1).sum()))
    return updated.tolist(), best, report


def topic_label(words):
    chosen = []
    for word in words:
        if word and not any(word in previous or previous in word for previous in chosen):
            chosen.append(word)
        if len(chosen) == 3:
            break
    return " · ".join(chosen)


def make_snapshot(model, articles, all_articles, embeddings, original, final, similarities,
                  params, token_coverage, revision, run_id, report):
    import numpy as np
    docs = [a["abstract"] for a in articles]
    refresh_topics(model, docs, final, embeddings)
    # A distinct two-dimensional fit is only for display, never for clustering/scoring.
    map_params = {"n_neighbors": 20, "n_components": 2}
    coords = projection(embeddings, map_params, 42, dimensions=2)
    labels = np.asarray(final)
    topics, hierarchy = [], []
    for topic in sorted(set(final) - {-1}):
        indices = np.flatnonzero(labels == topic)
        keywords = [{"term": term, "weight": float(weight)} for term, weight in model.get_topic(topic)]
        label = topic_label([item["term"] for item in keywords])
        center = embeddings[indices].mean(axis=0)
        center /= np.linalg.norm(center)
        nearest = indices[np.argsort(-(embeddings[indices] @ center))[:3]]
        topics.append({"id": topic, "label": label, "keywords": keywords, "count": len(indices),
                       "representatives": [articles[i]["handle"] for i in nearest]})
        hierarchy.append({"id": str(topic), "label": label, "topics": [topic], "children": [], "distance": 0.0})
    hierarchical = model.hierarchical_topics(docs)
    for row in hierarchical.to_dict("records"):
        hierarchy.append({"id": str(row["Parent_ID"]), "label": row["Parent_Name"].replace("_", " · "),
                          "topics": [int(value) for value in row["Topics"]],
                          "children": [str(row["Child_Left_ID"]), str(row["Child_Right_ID"])],
                          "distance": float(row["Distance"])})
    documents = [{**{key: article[key] for key in SUMMARY_FIELDS},
                  "topic": int(final[i]), "original_topic": int(original[i]),
                  "reassigned": original[i] == -1 and final[i] != -1,
                  "similarity": float(similarities[i]), "x": float(coords[i, 0]), "y": float(coords[i, 1])}
                 for i, article in enumerate(articles)]
    return {"metadata": {"run_id": run_id, "built_at": datetime.now(timezone.utc).isoformat(),
                         "model": MODEL, "model_revision": revision, "corpus_fingerprint": corpus_fingerprint(all_articles),
                         "article_count": len(all_articles), "abstract_count": len(articles),
                         "missing_abstract_count": len(all_articles) - len(articles), "topic_count": len(topics),
                         "params": params, "seeds": SEEDS if report.get("finalists") else [42], "clustering_seed": 42,
                         "map_params": {**map_params, "seed": 42, "metric": "cosine"},
                         "token_coverage": {k: v for k, v in token_coverage.items() if k != "per_article"},
                         "outliers": report["outliers"]},
            "topics": topics, "documents": documents, "hierarchy": hierarchy,
            "report": {k: v for k, v in report.items() if k not in ("token_coverage", "review")}}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def compare_runs(candidate, control, docs, embeddings, evaluator, articles, guidance, backend,
                 cache, run_dir, metadata, audited=None):
    """Complete both selected arms across all validation seeds, without publishing."""
    import numpy as np
    from .topic_guidance import REFERENCE
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    bounds = candidate[3]["metric_bounds"]

    def diagnostics(labels):
        return {"alignment": alignment(labels, embeddings, guidance, evaluator.seed_embeddings),
                "periods": period_alignment(articles, labels, embeddings, guidance, evaluator.seed_embeddings, reference)}

    comparison = {"guidance_fingerprint": guidance["fingerprint"], "metric_bounds": bounds,
                  "quality_weights": METRICS, "selection_weights": GUIDED_WEIGHTS,
                  "method": "One-to-one seed/centroid cosine matching in original embeddings; unmatched seeds score zero. "
                            "Seed alignment is a diagnostic, not independently verified classification accuracy. "
                            "Periods use the original retrospective volume/issue boundaries; first-period coverage is incomplete."}
    for name, selected, mode in (("seeded", candidate, guidance), ("unseeded", control, None)):
        records = []
        for seed in SEEDS:
            model, original = fit_model(docs, embeddings, None, selected[2], mode, backend, cache, seed)
            before = evaluator.measure(model, original)
            before_diagnostics = diagnostics(original)
            final, _, outliers = calibrate_outliers(model, original, embeddings, docs)
            refresh_topics(model, docs, final, embeddings)
            records.append({"seed": seed, "before": before, "after": evaluator.measure(model, final),
                            "before_diagnostics": before_diagnostics, "after_diagnostics": diagnostics(final),
                            "outliers": outliers, "review": representative_review(model, final, embeddings, articles)})
            if name == "unseeded" and seed == 42:
                write_json(run_dir / "control-assignments.json", [
                    {"handle": article["handle"], "original_topic": int(original[i]), "topic": int(final[i])}
                    for i, article in enumerate(articles)])
        comparison[name] = {"params": selected[2], "runs": records,
                            "mean_after": {key: float(np.mean([record["after"][key] for record in records]))
                                           for key in records[0]["after"]},
                            "mean_quality_score": float(np.mean([score(record["after"], bounds) for record in records]))}
        if name == "unseeded":
            write_json(run_dir / "control-report.json", selected[3])
    seeded, unseeded = comparison["seeded"], comparison["unseeded"]
    comparison["improved"] = (seeded["mean_after"]["alignment"] > unseeded["mean_after"]["alignment"] and
                              seeded["mean_quality_score"] >= unseeded["mean_quality_score"])
    comparison["audited"] = {"status": "unavailable_or_different_corpus"}
    if audited and all(audited["metadata"].get(key) == metadata[key]
                       for key in ("corpus_fingerprint", "model_revision")):
        documents = {document["handle"]: document for document in audited["documents"]}
        if set(documents) == {article["handle"] for article in articles}:
            class PublishedKeywords:
                def get_topic(self, topic):
                    return [(word["term"], word["weight"]) for item in audited["topics"] if item["id"] == topic
                            for word in item["keywords"]]
            labels = [documents[article["handle"]]["topic"] for article in articles]
            metrics = evaluator.measure(PublishedKeywords(), labels)
            comparison["audited"] = {"status": "comparable", "run_id": audited["metadata"]["run_id"],
                                     "metrics": metrics, "quality_score": score(metrics, bounds), **diagnostics(labels)}
    write_json(run_dir / "comparison.json", comparison)
    return {"improved": comparison["improved"],
            **{name: {key: value for key, value in comparison[name].items() if key != "runs"}
               for name in ("seeded", "unseeded")}, "audited": comparison["audited"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--tune", action="store_true", help="Search 54 configurations per arm and publish unless --no-publish is set")
    operation.add_argument("--rebuild", action="store_true", help="Refit the last successful parameters and model revision")
    operation.add_argument("--publish-run", help="Publish a completed, reviewed run from the artifact volume")
    operation.add_argument("--timeline", action="store_true", help="Calculate topics over time for the current assignments without rebuilding the model")
    parser.add_argument("--no-publish", action="store_true", help="Save a complete run for review without replacing the live model")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seed-topics", type=Path, help="Editorial seed manifest; --tune also builds an unseeded comparison")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.seed_topics and not args.tune:
        parser.error("--seed-topics requires --tune; --rebuild uses its frozen saved guidance")
    from .store import redis_client
    client = redis_client()
    lock = client.lock("dpe:topics:build-lock", timeout=300, blocking=False, thread_local=False)
    if not lock.acquire(blocking=False):
        raise RuntimeError("Another topic build is already running.")
    stop_renewal, lost_lock = threading.Event(), threading.Event()

    def renew_lock():
        while not stop_renewal.wait(60):
            try:
                lock.reacquire()
            except Exception as exc:
                lost_lock.set()
                log(f"Build lease lost ({exc}); this process will not replace the live model.")
                return

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    renewal = threading.Thread(target=renew_lock, daemon=True)
    renewal.start()
    try:
        artifacts = Path(os.getenv("TOPIC_ARTIFACTS_DIR", "data/topic-artifacts"))
        artifacts.mkdir(parents=True, exist_ok=True)
        config_path = artifacts / "selected.json"
        if args.timeline:
            from .topic_store import load_topics
            from .topic_timeline import build_timeline, publish_timeline
            snapshot = load_topics(client)
            if snapshot.get("status") != "ready":
                raise ValueError("Publish a topic model before building its timeline.")
            timeline = build_timeline(snapshot, read_corpus(client))
            run_dir = artifacts / "runs" / snapshot["metadata"]["run_id"]
            run_dir.mkdir(parents=True, exist_ok=True)
            write_json(run_dir / "timeline.json", timeline)
            if not args.no_publish:
                if lost_lock.is_set() or not lock.owned():
                    raise RuntimeError("Build lease lost; the timeline was not published.")
                publish_timeline(timeline, snapshot, client)
            log(f"{'Saved' if args.no_publish else 'Published'} topics over time for {timeline['run_id']}")
            return
        if args.publish_run:
            import re
            if args.no_publish or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", args.publish_run):
                raise ValueError("Invalid publish-run ID or conflicting --no-publish option")
            snapshot = json.loads((artifacts / "runs" / args.publish_run / "snapshot.json").read_text())
            publish_snapshot(snapshot, client)
            meta = snapshot["metadata"]
            write_json(config_path, saved_settings(meta))
            log(f"Published reviewed run {meta['run_id']}")
            return
        from huggingface_hub import HfApi
        saved = json.loads(config_path.read_text()) if args.rebuild and config_path.exists() else None
        if args.rebuild and not saved:
            raise RuntimeError("No saved settings. Run --tune first.")
        guidance = load_guidance(args.seed_topics) if args.seed_topics else validate_guidance((saved or {}).get("guidance"))
        revision = saved["revision"] if saved else os.getenv("TOPIC_MODEL_REVISION") or HfApi().model_info(MODEL).sha
        all_articles = read_corpus(client)
        articles = [a for a in all_articles if a["abstract"].strip()]
        if len(articles) < 60:
            raise RuntimeError("At least 60 abstracts are required for this tuning grid.")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        run_dir = artifacts / "runs" / run_id
        run_dir.mkdir(parents=True)
        log(f"Run {run_id}: {len(articles)} full abstracts; {len(all_articles) - len(articles)} records excluded without abstracts")
        started = time.monotonic()
        embeddings, token_coverage = encode_abstracts(articles, revision, artifacts, args.device, args.batch_size)
        write_json(run_dir / "token-coverage.json", token_coverage)
        docs = [a["abstract"] for a in articles]
        backend, seed_embeddings = None, None
        if guidance:
            write_json(run_dir / "seeds.json", guidance)

            def encode_seed_texts(texts):
                records = [{"handle": f"seed-text-{i}", "abstract": text} for i, text in enumerate(texts)]
                vectors, coverage = encode_abstracts(records, revision, artifacts, args.device, args.batch_size)
                write_json(run_dir / "seed-token-coverage.json", coverage)
                return vectors

            backend = cached_embedding_backend(encode_seed_texts)
            seed_embeddings = backend.embed([" ".join(terms) for terms in seed_topic_list(guidance)])
        evaluator = Evaluator(docs, embeddings, guidance, seed_embeddings)
        projection_cache = {}
        # Hold keyword processing constant: compare parameter tuning, not stopword policies.
        baseline_params = {"n_neighbors": 15, "n_components": 5, "min_cluster_size": 10, "min_samples": 10}
        baseline, baseline_labels = fit_model(docs, embeddings, None, baseline_params, guidance, backend, projection_cache)
        baseline_metrics = evaluator.measure(baseline, baseline_labels)
        log(f"Untuned baseline: {baseline_metrics}")
        del baseline
        control = None
        if args.tune:
            model, original, params, tuning_report, control = tune(
                docs, embeddings, evaluator, articles, run_dir, guidance, backend, projection_cache)
        else:
            params = saved["params"]
            model, original = fit_model(docs, embeddings, None, params, guidance, backend, projection_cache)
            tuning_report = {"rebuild_from": saved["run_id"], "trials": [], "finalists": []}
        before_metrics = evaluator.measure(model, original)
        final, similarities, outliers = calibrate_outliers(model, original, embeddings, docs)
        if len(set(final) - {-1}) < 2:
            raise RuntimeError("The selected model has fewer than two topics.")
        review = representative_review(model, final, embeddings, articles)
        report = {**tuning_report, "baseline": baseline_metrics, "selected_before_reassignment": before_metrics,
                  "outliers": outliers, "token_coverage": token_coverage, "review": review}
        if guidance:
            report["guidance"] = guidance
        from importlib.metadata import version
        report["library_versions"] = {name: version(name) for name in
                                      ("bertopic", "sentence-transformers", "transformers", "torch", "umap-learn", "hdbscan", "scikit-learn", "numpy")}
        report["encoding"] = {"device": args.device, "dtype": "float32", "normalised": True, "batch_size": args.batch_size}
        snapshot = make_snapshot(model, articles, all_articles, embeddings, original, final, similarities,
                                 params, token_coverage, revision, run_id, report)
        if guidance:
            snapshot["metadata"]["guidance"] = guidance
        report["review"] = representative_review(model, final, embeddings, articles)
        report["selected_after_reassignment"] = evaluator.measure(model, final)
        if control:
            binding = json.loads((Path(__file__).resolve().parent.parent / "data" / "retrospectives.json").read_text())["audited_model"]
            archived = client.get("dpe:topics:run:" + binding["run_id"])
            audited = json.loads(archived) if archived else None
            report["comparison"] = compare_runs(
                (model, original, params, tuning_report), control, docs, embeddings, evaluator, articles,
                guidance, backend, projection_cache, run_dir, snapshot["metadata"], audited)
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        from .topic_timeline import build_timeline
        snapshot["timeline"] = build_timeline(snapshot, all_articles)
        # Compact API report; full audit with representative abstracts stays in the artifact volume.
        snapshot["report"].update(selected_after_reassignment=report["selected_after_reassignment"], elapsed_seconds=report["elapsed_seconds"])
        if control:
            snapshot["report"]["comparison"] = report["comparison"]
        validate_snapshot(snapshot)
        write_json(run_dir / "report.json", report)
        write_json(run_dir / "snapshot.json", snapshot)
        write_json(run_dir / "timeline.json", snapshot["timeline"])
        model.save(str(run_dir / "model"), serialization="safetensors", save_ctfidf=True, save_embedding_model=MODEL)
        if not args.no_publish:
            if lost_lock.is_set() or not lock.owned():
                raise RuntimeError("Build lease lost. Complete artifacts were saved for review; the live model is unchanged.")
            publish_snapshot(snapshot, client)
            write_json(config_path, saved_settings(snapshot["metadata"]))
        log(f"{'Saved for review' if args.no_publish else 'Published'} {run_id}: {len(snapshot['topics'])} topics; outliers {outliers['before']} -> {outliers['after']}; "
            f"{report['elapsed_seconds']} seconds. Audit: {run_dir}")
    finally:
        stop_renewal.set()
        renewal.join(timeout=1)
        try:
            lock.release()
        except Exception:
            pass


if __name__ == "__main__":
    main()
