"""Topic snapshots and publication, with no ML imports on the web request path."""
from __future__ import annotations

import hashlib
import json

SUMMARY_FIELDS = ("handle", "title", "authors", "volume", "issue", "year", "pages", "doi")
CURRENT = "dpe:topics:current"
FINGERPRINT = "dpe:topics:catalog-fingerprint"


def corpus_fingerprint(articles: list[dict]) -> str:
    records = [{key: article.get(key, "") for key in (*SUMMARY_FIELDS, "abstract")}
               for article in sorted(articles, key=lambda item: item["handle"])]
    return hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_corpus(client=None) -> list[dict]:
    """WATCH the catalog, then atomically read every full record from that catalog."""
    import redis
    from .store import article_key, redis_client
    client = client or redis_client()
    for _ in range(5):
        with client.pipeline() as pipe:
            try:
                pipe.watch("dpe:catalog")
                raw = pipe.get("dpe:catalog")
                if not raw:
                    raise RuntimeError("Sync the article catalog before modelling topics.")
                catalog = json.loads(raw)
                keys = [article_key(item["handle"]) for item in catalog["articles"]]
                pipe.multi()
                pipe.mget(keys)
                rows = pipe.execute()[0]
                if any(row is None for row in rows):
                    raise RuntimeError("Catalog contains missing article records; run app.sync first.")
                return sorted([json.loads(row) for row in rows], key=lambda item: item["handle"])
            except redis.WatchError:
                continue
    raise RuntimeError("The catalog kept changing while reading the modelling snapshot. Retry the rebuild.")


def validate_snapshot(snapshot: dict) -> None:
    import math
    documents, topics, hierarchy = snapshot["documents"], snapshot["topics"], snapshot["hierarchy"]
    metadata = snapshot["metadata"]
    if metadata["abstract_count"] + metadata["missing_abstract_count"] != metadata["article_count"]:
        raise ValueError("Corpus coverage counts do not match.")
    if metadata["token_coverage"]["truncated_abstracts"] != 0:
        raise ValueError("Truncated abstracts cannot be published.")
    if metadata["outliers"]["after"] != sum(item["topic"] == -1 for item in documents):
        raise ValueError("Outlier counts do not match.")
    ids = {item["id"] for item in topics}
    if len(ids) < 2 or len(ids) != len(topics):
        raise ValueError("A topic run needs at least two distinct topics.")
    handles = [item["handle"] for item in documents]
    if len(set(handles)) != len(handles) or len(handles) != snapshot["metadata"]["abstract_count"]:
        raise ValueError("Each abstract must have exactly one map point.")
    for document in documents:
        if document["topic"] not in ids | {-1}:
            raise ValueError("Unknown document topic.")
        if not all(math.isfinite(document[key]) for key in ("x", "y")):
            raise ValueError("Map coordinates must be finite.")
    for topic in topics:
        if topic["count"] != sum(item["topic"] == topic["id"] for item in documents):
            raise ValueError("Topic counts do not match final assignments.")
    nodes = {node["id"]: node for node in hierarchy}
    if len(nodes) != len(hierarchy) or len(hierarchy) != 2 * len(ids) - 1:
        raise ValueError("Hierarchy must be a complete binary topic tree.")
    parents = set()
    for node in hierarchy:
        children = node.get("children", [])
        if children:
            if len(children) != 2 or any(child not in nodes for child in children):
                raise ValueError("Invalid hierarchy children.")
            if any(child in parents for child in children):
                raise ValueError("Hierarchy node has multiple parents.")
            parents.update(children)
            descendants = set().union(*(set(nodes[child]["topics"]) for child in children))
            if descendants != set(node["topics"]) or any(nodes[child]["distance"] > node["distance"] + 1e-8 for child in children):
                raise ValueError("Inconsistent hierarchy descendants or distances.")
        elif len(node["topics"]) != 1 or node["topics"][0] not in ids:
            raise ValueError("Invalid hierarchy leaf.")
    roots = set(nodes) - parents
    if len(roots) != 1 or set(nodes[roots.pop()]["topics"]) != ids:
        raise ValueError("Hierarchy does not cover every topic.")


def publish_snapshot(snapshot: dict, client=None) -> None:
    from .store import redis_client
    validate_snapshot(snapshot)
    # Serialise before MULTI: incomplete or non-finite runs never replace the current run.
    encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    client = client or redis_client()
    pipe = client.pipeline(transaction=True)
    pipe.set("dpe:topics:run:" + snapshot["metadata"]["run_id"], encoded)
    pipe.set(CURRENT, encoded)
    pipe.execute()


def load_topics(client=None) -> dict:
    from .store import redis_client
    client = client or redis_client()
    pipe = client.pipeline(transaction=True)
    pipe.get(CURRENT)
    pipe.get(FINGERPRINT)
    raw, fingerprint = pipe.execute()
    if not raw:
        return {"status": "not_built", "message": "The topic model has not been built yet."}
    snapshot = json.loads(raw)
    if not fingerprint:
        fingerprint = corpus_fingerprint(read_corpus(client))
    return {**snapshot, "status": "ready", "stale": fingerprint != snapshot["metadata"]["corpus_fingerprint"]}
