"""Explicit, local-only BERTopic builds. No hosted inference or generative labels."""
from __future__ import annotations

import gc
import hashlib
import itertools
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .topic_store import SUMMARY_FIELDS, corpus_fingerprint

MODEL = "Qwen/Qwen3-Embedding-0.6B"
SEEDS = [42, 7, 21]
METRICS = {"coherence": 2, "silhouette": 2, "diversity": 1, "coverage": 1}
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
    coverage.update(encoded_count=len(missing), cache_hits=len(articles) - len(missing))
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


def projection(embeddings, params: dict, seed: int, dimensions=None):
    from umap import UMAP
    return UMAP(n_neighbors=params["n_neighbors"], n_components=dimensions or params["n_components"],
                min_dist=0.0, metric="cosine", random_state=seed,
                n_jobs=1, low_memory=True).fit_transform(embeddings)


def fit_model(docs, embeddings, reduced, params):
    from bertopic import BERTopic
    from bertopic.vectorizers import ClassTfidfTransformer
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    model = BERTopic(embedding_model=None, umap_model=FrozenProjection(reduced),
                     hdbscan_model=HDBSCAN(min_cluster_size=params["min_cluster_size"],
                                           min_samples=params["min_samples"], metric="euclidean",
                                           cluster_selection_method="eom", prediction_data=True),
                     vectorizer_model=CountVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1),
                     ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True),
                     top_n_words=10, calculate_probabilities=False)
    labels, _ = model.fit_transform(docs, embeddings)
    return model, labels


class Evaluator:
    def __init__(self, docs, embeddings):
        from sklearn.feature_extraction.text import CountVectorizer
        self.embeddings = embeddings
        self.count = len(docs)
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
        return {"topic_count": len(ids), "outliers": int((~selected).sum()),
                "coherence": float(np.mean(coherence)) if coherence else -1.0,
                "silhouette": separation,
                "diversity": len(set(itertools.chain.from_iterable(words))) / max(1, sum(map(len, words))),
                "coverage": float(selected.mean())}


def score(metrics, bounds):
    return sum(weight * max(0, min(1, (metrics[key] - bounds[key][0]) / max(1e-9, bounds[key][1] - bounds[key][0])))
               for key, weight in METRICS.items()) / sum(METRICS.values())


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


def tune(docs, embeddings, evaluator, articles, run_dir):
    import numpy as np
    from sklearn.metrics import adjusted_mutual_info_score
    trials = []
    keys = list(GRID)
    reduced_cache = {}
    for values in itertools.product(*(GRID[key] for key in keys)):
        params = dict(zip(keys, values, strict=True))
        projection_key = (params["n_neighbors"], params["n_components"])
        if projection_key not in reduced_cache:
            reduced_cache[projection_key] = projection(embeddings, params, 42)
        model, labels = fit_model(docs, embeddings, reduced_cache[projection_key], params)
        metrics = evaluator.measure(model, labels)
        trials.append({"params": params, "seed": 42, "metrics": metrics})
        log(f"Trial {len(trials)}/54: {params} -> {metrics['topic_count']} topics, "
            f"{metrics['outliers']} outliers, coherence {metrics['coherence']:.3f}, silhouette {metrics['silhouette']:.3f}")
        write_json(run_dir / "trials.json", trials)
    valid = [trial for trial in trials if trial["metrics"]["topic_count"] >= 2]
    if not valid:
        raise RuntimeError("Tuning found no multi-topic model; the previous published run is unchanged.")
    bounds = {key: [min(t["metrics"][key] for t in valid), max(t["metrics"][key] for t in valid)] for key in METRICS}
    for trial in valid:
        trial["score"] = score(trial["metrics"], bounds)
    finalists = sorted(valid, key=lambda t: t["score"], reverse=True)[:3]
    validation = []
    for trial in finalists:
        params, seed_metrics, seed_labels = trial["params"], [], []
        for seed in SEEDS:
            reduced = (reduced_cache[(params["n_neighbors"], params["n_components"])] if seed == 42
                       else projection(embeddings, params, seed))
            model, labels = fit_model(docs, embeddings, reduced, params)
            seed_metrics.append({"seed": seed, "metrics": evaluator.measure(model, labels)})
            seed_labels.append(labels)
        stability = float(np.mean([adjusted_mutual_info_score(a, b) for a, b in itertools.combinations(seed_labels, 2)]))
        validation.append({"params": params, "seeds": seed_metrics, "stability_ami": stability,
                           "mean_score": float(np.mean([score(item["metrics"], bounds) for item in seed_metrics])),
                           "review": representative_review(model, labels, embeddings, articles)})
        log(f"Finalist {params}: mean score {validation[-1]['mean_score']:.3f}, stability {stability:.3f}")
    selected = max(validation, key=lambda item: (item["mean_score"], item["stability_ami"]))
    params = selected["params"]
    model, labels = fit_model(docs, embeddings, reduced_cache[(params["n_neighbors"], params["n_components"])], params)
    return model, labels, params, {"trials": trials, "finalists": validation,
                                  "metric_weights": METRICS, "metric_bounds": bounds,
                                  "selection": "Mean weighted normalised score across three seeds; stability breaks ties."}


def calibrate_outliers(model, labels, embeddings, docs):
    """Hold out confident members from centroids; require 95% label agreement."""
    import numpy as np
    labels = np.asarray(labels)
    ids = sorted(set(labels) - {-1})
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
                  params, token_coverage, revision, run_id, report, map_projection=None):
    import numpy as np
    docs = [a["abstract"] for a in articles]
    model.update_topics(docs, topics=final, vectorizer_model=model.vectorizer_model,
                        ctfidf_model=model.ctfidf_model, n_gram_range=(1, 2), top_n_words=model.top_n_words)
    # update_topics() refreshes c-TF-IDF but cannot rebuild document centroids without
    # an embedding backend. Rebuild them explicitly from the final full-text members.
    model.topic_embeddings_ = np.stack([embeddings[np.asarray(final) == topic].mean(axis=0)
                                        for topic in sorted(set(final))])
    # A distinct two-dimensional fit is only for display, never for clustering/scoring.
    map_params = {"n_neighbors": 20, "n_components": 2}
    coords = (map_projection.embedding_ if map_projection is not None else
              projection(embeddings, map_params, 42, dimensions=2))
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
