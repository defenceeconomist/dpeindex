"""Isolated Redis catalog and Qdrant semantic index for DPE articles."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/4")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
COLLECTION = "dpe_articles"
MODEL = "BAAI/bge-small-en-v1.5"
PREFIX = "dpe:"


def redis_client() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def qdrant(method: str, path: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{QDRANT_URL}{path}", data=payload, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Qdrant {error.code}: {error.read().decode()[:400]}") from error


def point_id(handle: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, handle))


def article_key(handle: str) -> str:
    return PREFIX + "article:" + point_id(handle)


def embed_text(article: dict) -> str:
    return "\n".join(filter(None, [
        article["title"], "Authors: " + ", ".join(article["authors"]),
        article["abstract"], "Keywords: " + article["keywords"] if article["keywords"] else "",
    ]))


@lru_cache(maxsize=1)
def embedding_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL)


def _sort_number(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return -1


def catalog_for(articles: list[dict]) -> dict:
    volumes: dict[str, dict] = {}
    summaries = []
    for article in articles:
        summary = {key: article[key] for key in (
            "handle", "title", "authors", "volume", "issue", "year", "pages", "doi",
        )}
        summaries.append(summary)
        volume = volumes.setdefault(article["volume"], {
            "number": article["volume"], "years": set(), "count": 0, "issues": {},
        })
        volume["years"].add(article["year"])
        volume["count"] += 1
        issue = volume["issues"].setdefault(article["issue"], {
            "number": article["issue"], "count": 0,
        })
        issue["count"] += 1
    result = []
    for volume in sorted(volumes.values(), key=lambda value: _sort_number(value["number"]), reverse=True):
        volume["years"] = sorted(volume["years"])
        volume["issues"] = sorted(volume["issues"].values(), key=lambda value: _sort_number(value["number"]))
        result.append(volume)
    summaries.sort(key=lambda a: (
        -_sort_number(a["volume"]), _sort_number(a["issue"]),
        _sort_number(a["pages"].split("-")[0]), a["title"],
    ))
    return {"volumes": result, "articles": summaries, "count": len(articles)}


def sync_articles(articles: list[dict], *, source_url: str) -> dict:
    if len(articles) != len({a["handle"] for a in articles}):
        raise ValueError("Duplicate RePEc handles in source file")
    client = redis_client()
    qdrant("PUT", f"/collections/{COLLECTION}", {
        "vectors": {"size": 384, "distance": "Cosine"},
    }) if not collection_exists() else None
    old_ids = set(client.smembers(PREFIX + "ids"))
    new_ids = {point_id(a["handle"]) for a in articles}
    stale = old_ids - new_ids
    if stale:
        qdrant("POST", f"/collections/{COLLECTION}/points/delete?wait=true", {"points": list(stale)})

    model = embedding_model()
    changed = []
    for article in articles:
        digest = hashlib.sha256(embed_text(article).encode()).hexdigest()
        if client.get(PREFIX + "digest:" + point_id(article["handle"])) != digest:
            changed.append((article, digest))
    for offset in range(0, len(changed), 48):
        batch = changed[offset:offset + 48]
        vectors = model.passage_embed([embed_text(article) for article, _ in batch])
        points = [{
            "id": point_id(article["handle"]),
            "vector": [float(value) for value in vector],
            "payload": {"handle": article["handle"], "volume": article["volume"], "issue": article["issue"]},
        } for (article, _), vector in zip(batch, vectors, strict=True)]
        qdrant("PUT", f"/collections/{COLLECTION}/points?wait=true", {"points": points})

    pipeline = client.pipeline(transaction=True)
    for old_id in stale:
        pipeline.delete(PREFIX + "article:" + old_id, PREFIX + "digest:" + old_id)
    for article in articles:
        article_id = point_id(article["handle"])
        pipeline.set(PREFIX + "article:" + article_id, json.dumps(article, ensure_ascii=False))
    for article, digest in changed:
        pipeline.set(PREFIX + "digest:" + point_id(article["handle"]), digest)
    pipeline.delete(PREFIX + "ids")
    if new_ids:
        pipeline.sadd(PREFIX + "ids", *new_ids)
    catalog = catalog_for(articles)
    pipeline.set(PREFIX + "catalog", json.dumps(catalog, ensure_ascii=False))
    pipeline.set(PREFIX + "source", json.dumps({
        "url": source_url, "synced_at": datetime.now(timezone.utc).isoformat(),
        "count": len(articles),
    }))
    pipeline.execute()
    return {"count": len(articles), "embedded": len(changed), "removed": len(stale)}


def collection_exists() -> bool:
    try:
        qdrant("GET", f"/collections/{COLLECTION}")
        return True
    except RuntimeError as error:
        if "Qdrant 404:" in str(error):
            return False
        raise


def load_catalog() -> dict:
    raw = redis_client().get(PREFIX + "catalog")
    return json.loads(raw) if raw else {"volumes": [], "articles": [], "count": 0}


def load_article(handle: str) -> dict | None:
    raw = redis_client().get(article_key(handle))
    return json.loads(raw) if raw else None


def semantic_search(query: str, limit: int = 30) -> list[dict]:
    model = embedding_model()
    vector = [float(value) for value in next(iter(model.query_embed(query)))]
    results = qdrant("POST", f"/collections/{COLLECTION}/points/query", {
        "query": vector, "limit": limit, "with_payload": True,
    })["result"]["points"]
    client = redis_client()
    pipeline = client.pipeline()
    for item in results:
        pipeline.get(article_key(item["payload"]["handle"]))
    records = pipeline.execute()
    return [{**json.loads(raw), "score": round(item["score"], 4)}
            for item, raw in zip(results, records, strict=True) if raw]
