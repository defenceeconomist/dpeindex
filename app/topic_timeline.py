"""Offline BERTopic topics-over-time representations for published assignments."""
from __future__ import annotations

import json
import logging
import math
from collections import Counter
from datetime import datetime, timezone

from redis.exceptions import RedisError

from .topic_store import CURRENT, corpus_fingerprint

PREFIX = "dpe:topics:timeline:"
WIDTHS = {"year": 1, "five_years": 5, "decade": 10}
logger = logging.getLogger(__name__)


def publication_year(record):
    value = str(record.get("year", ""))
    return int(value) if value.isdigit() and 1500 <= int(value) <= 2500 else None


def build_timeline(snapshot, articles):
    """Restore the final assignment representation, without fitting any clusters or encoder."""
    if corpus_fingerprint(articles) != snapshot["metadata"]["corpus_fingerprint"]:
        raise ValueError("The canonical corpus differs from the model. Rebuild the model before its timeline.")
    from bertopic import BERTopic
    from bertopic.vectorizers import ClassTfidfTransformer
    from sklearn.feature_extraction.text import CountVectorizer

    by_handle = {article["handle"]: article for article in articles}
    documents = snapshot["documents"]
    docs = [by_handle[document["handle"]]["abstract"] for document in documents]
    if any(not text.strip() for text in docs):
        raise ValueError("Every modelled document must retain its complete abstract.")
    labels = [document["topic"] for document in documents]
    model = BERTopic(embedding_model=None, top_n_words=10)
    # Restore the already published assignments. update_topics only recalculates
    # their c-TF-IDF representation; it does not run the encoder, UMAP or HDBSCAN.
    model.topics_ = labels
    model.update_topics(docs, vectorizer_model=CountVectorizer(stop_words="english", ngram_range=(1, 2)),
                        ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True))
    for topic in snapshot["topics"]:
        if topic["count"] and [word for word, _ in model.get_topic(topic["id"])] != [word["term"] for word in topic["keywords"]]:
            raise ValueError("Restored keyword representation differs from the published model.")
    dated = [index for index, document in enumerate(documents) if publication_year(document) is not None]
    if not dated:
        raise ValueError("The model has no dated abstracts.")
    # BERTopic indexes global rows by the topics present in the supplied dated
    # subset. Keep those rows aligned even if a topic has only undated papers.
    global_topics = sorted(set(labels))
    dated_topics = sorted({labels[index] for index in dated})
    model.c_tf_idf_ = model.c_tf_idf_[[global_topics.index(topic) for topic in dated_topics]]
    years = [publication_year(article) for article in articles if publication_year(article) is not None]
    first, last = min(years), max(years)
    if last - first >= 100:
        raise ValueError("Annual topic analysis supports fewer than 100 publication years.")
    topic_ids = sorted([topic["id"] for topic in snapshot["topics"]] + [-1])
    groups = {}
    for name, width in WIDTHS.items():
        timestamps = [(publication_year(documents[index]) // width) * width for index in dated]
        temporal = model.topics_over_time([docs[index] for index in dated], timestamps,
                                          topics=[labels[index] for index in dated],
                                          evolution_tuning=False, global_tuning=True)
        representations = {(int(row.Timestamp), int(row.Topic)): row for row in temporal.itertuples()}
        periods, cells = [], []
        for start in range(first // width * width, last // width * width + 1, width):
            end = start + width - 1
            records = [article for article in articles if (year := publication_year(article)) is not None and start <= year <= end]
            abstract_count = sum(bool(article["abstract"].strip()) for article in records)
            period = {"start": start, "end": end, "label": str(start) if width == 1 else f"{start}–{end}",
                      "abstract_count": abstract_count, "record_count": len(records),
                      "missing_abstract_count": len(records) - abstract_count,
                      "observed_years": sorted({publication_year(article) for article in records}),
                      "partial": start < first or end > last}
            periods.append(period)
            for topic_id in topic_ids:
                row = representations.get((start, topic_id))
                count = int(row.Frequency) if row is not None else 0
                words = list(dict.fromkeys(word.strip() for word in row.Words.split(",") if word.strip())) if row is not None else []
                cells.append({"start": start, "topic": topic_id, "count": count,
                              "share": 100 * count / abstract_count if abstract_count else None, "words": words})
        groups[name] = {"width": width, "periods": periods, "cells": cells}
    result = {"status": "ready", "run_id": snapshot["metadata"]["run_id"],
              "corpus_fingerprint": snapshot["metadata"]["corpus_fingerprint"],
              "built_at": datetime.now(timezone.utc).isoformat(), "first_year": first, "last_year": last,
              "undated_abstract_count": len(documents) - len(dated),
              "undated_missing_count": sum(publication_year(article) is None and not article["abstract"].strip() for article in articles),
              "method": {"name": "BERTopic topics_over_time", "global_tuning": True, "evolution_tuning": False,
                         "denominator": "All usable abstracts in each period, including unassigned abstracts.",
                         "keywords": "Period c-TF-IDF keywords smoothed with the global topic representation. Terms may originate outside the selected period; no smoothing between consecutive periods is applied.",
                         "assignments": "Published topic identities and article assignments are fixed across time; clusters are not refitted by year."},
              "groups": groups}
    validate_timeline(result, snapshot)
    return result


def validate_timeline(timeline, snapshot):
    if any(timeline[key] != snapshot["metadata"][key] for key in ("run_id", "corpus_fingerprint")):
        raise ValueError("Timeline belongs to a different model run or corpus.")
    documents = snapshot["documents"]
    undated = sum(publication_year(document) is None for document in documents)
    if timeline["undated_abstract_count"] != undated:
        raise ValueError("Undated abstract coverage differs from the model.")
    topic_ids = {topic["id"] for topic in snapshot["topics"]} | {-1}
    for name, width in WIDTHS.items():
        group = timeline["groups"][name]
        periods = group["periods"]
        starts = [period["start"] for period in periods]
        if group["width"] != width or starts != list(range(starts[0], starts[-1] + 1, width)):
            raise ValueError("Timeline periods overlap or omit intervals.")
        cells = {(cell["start"], cell["topic"]): cell for cell in group["cells"]}
        if len(cells) != len(group["cells"]) or set(cells) != {(start, topic) for start in starts for topic in topic_ids}:
            raise ValueError("Timeline needs exactly one cell per period and topic.")
        for period in periods:
            if period["end"] != period["start"] + width - 1:
                raise ValueError("Incorrect period boundary.")
            selected = [document for document in documents if (year := publication_year(document)) is not None
                        and period["start"] <= year <= period["end"]]
            counts = Counter(document["topic"] for document in selected)
            if period["abstract_count"] != len(selected) or period["missing_abstract_count"] < 0 or period["record_count"] != len(selected) + period["missing_abstract_count"]:
                raise ValueError("Timeline coverage does not match its denominator.")
            for topic in topic_ids:
                cell = cells[(period["start"], topic)]
                if cell["count"] != counts[topic]:
                    raise ValueError("Timeline counts differ from published assignments.")
                expected = 100 * counts[topic] / len(selected) if selected else None
                if (cell["share"] is None) != (expected is None) or expected is not None and not math.isclose(cell["share"], expected):
                    raise ValueError("Timeline share excludes usable abstracts or has an invalid denominator.")
        if sum(period["abstract_count"] for period in periods) + undated != snapshot["metadata"]["abstract_count"]:
            raise ValueError("Timeline omits modelled abstracts.")
        if sum(period["missing_abstract_count"] for period in periods) + timeline["undated_missing_count"] != snapshot["metadata"]["missing_abstract_count"]:
            raise ValueError("Timeline omits missing-abstract records.")


def publish_timeline(timeline, snapshot, client):
    validate_timeline(timeline, snapshot)
    encoded = json.dumps(timeline, ensure_ascii=False, allow_nan=False)
    with client.pipeline() as pipe:
        pipe.watch(CURRENT)
        active = json.loads(pipe.get(CURRENT) or "null")
        if not active or any(active["metadata"][key] != timeline[key] for key in ("run_id", "corpus_fingerprint")):
            raise ValueError("The model changed during timeline analysis; the live timeline was not replaced.")
        pipe.multi()
        pipe.set(PREFIX + timeline["run_id"], encoded)
        pipe.execute()


def load_timeline(active, client=None):
    if active.get("status") != "ready":
        return {"status": "not_built"}
    try:
        from .store import redis_client
        client = client or redis_client()
        raw = client.get(PREFIX + active["metadata"]["run_id"])
        # A separately refreshed analysis supersedes the one bundled at model
        # publication, while retaining the same model and assignment binding.
        timeline = json.loads(raw) if raw else active.get("timeline")
        if timeline is None:
            return {"status": "not_built", "message": "The topics-over-time analysis has not been built for this model yet."}
        validate_timeline(timeline, active)
        return timeline
    except (OSError, RedisError, ValueError, KeyError, TypeError, IndexError):
        logger.exception("Topics-over-time analysis could not be validated")
        return {"status": "unavailable", "message": "The topics-over-time analysis is temporarily unavailable."}
