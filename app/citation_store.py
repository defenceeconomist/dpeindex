"""Immutable citation snapshots and downloads; no analysis or network calls on reads."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re

from .topic_store import FINGERPRINT, corpus_fingerprint, read_corpus

CURRENT = "dpe:citations:current"
RUN_PREFIX = "dpe:citations:run:"
RUN_PATTERN = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}"
DOWNLOADS = {
    "nodes.csv": "text/csv", "edges.csv": "text/csv", "baseline-edges.csv": "text/csv",
    "coverage.csv": "text/csv", "network.gexf": "application/xml",
    "network.svg": "image/svg+xml", "network.png": "image/png", "degree-10.svg": "image/svg+xml",
    "report.html": "text/html", "evidence.json": "application/json", "review.json": "application/json",
    "bundle.zip": "application/zip",
}


def validate_snapshot(snapshot):
    meta = snapshot["metadata"]
    if not re.fullmatch(RUN_PATTERN, meta["run_id"]):
        raise ValueError("Invalid citation run ID")
    nodes, edges, communities = snapshot["nodes"], snapshot["edges"], snapshot["communities"]
    by_id = {n["handle"]: n for n in nodes}
    if len(by_id) != len(nodes) or len(nodes) != meta["eligible_count"]:
        raise ValueError("Citation node identities or counts are inconsistent")
    community_ids = {c["id"] for c in communities}
    if len(community_ids) != len(communities):
        raise ValueError("Duplicate citation communities")
    incoming, outgoing, seen = dict.fromkeys(by_id, 0), dict.fromkeys(by_id, 0), set()
    for e in edges:
        pair = (e["source"], e["target"])
        if pair in seen or pair[0] == pair[1] or any(h not in by_id for h in pair):
            raise ValueError("Invalid, duplicate or self citation")
        seen.add(pair)
        outgoing[pair[0]] += 1
        incoming[pair[1]] += 1
    for n in nodes:
        if n["in_degree"] != incoming[n["handle"]] or n["out_degree"] != outgoing[n["handle"]]:
            raise ValueError("Citation degree does not match edges")
        if n["degree"] != n["in_degree"] + n["out_degree"]:
            raise ValueError("Invalid total degree")
        if n["community"] not in community_ids | {-1} or (n["community"] == -1) != (n["degree"] == 0):
            raise ValueError("Invalid citation community assignment")
        if not all(math.isfinite(n[k]) for k in ("x", "y")):
            raise ValueError("Invalid graph coordinates")
    for c in communities:
        if c["count"] != sum(n["community"] == c["id"] for n in nodes):
            raise ValueError("Invalid community size")
    if meta["edge_count"] != len(edges) or meta["baseline_edge_count"] != sum(e["baseline"] for e in edges):
        raise ValueError("Invalid citation edge totals")
    if meta["article_count"] != meta["eligible_count"] + meta["excluded_count"]:
        raise ValueError("Invalid citation coverage totals")
    if sum(f["count"] for f in snapshot["flows"]) != len(edges):
        raise ValueError("Citation flows do not reconcile")
    if sum(row["eligible"] for row in snapshot["coverage"]) != len(nodes):
        raise ValueError("Coverage years do not reconcile")
    json.dumps(snapshot, allow_nan=False)


def publish_snapshot(snapshot, files, client=None, *, lock=None):
    from .store import redis_client
    validate_snapshot(snapshot)
    if snapshot["review"]["pending_count"] or not snapshot["review"].get("doi_sample"):
        raise ValueError("Review recovered links, identities, eligibility and DOI samples before publication")
    if set(files) != set(DOWNLOADS):
        raise ValueError("Citation exports are incomplete")
    for name, payload in files.items():
        if hashlib.sha256(payload).hexdigest() != snapshot["downloads"][name]["sha256"]:
            raise ValueError("Citation export hash mismatch")
    client = client or redis_client()
    run_key = RUN_PREFIX + snapshot["metadata"]["run_id"]
    encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    with client.pipeline() as pipe:
        watched = [run_key, FINGERPRINT, "dpe:catalog"]
        if lock is not None:
            watched.append(lock.name)
        pipe.watch(*watched)
        if pipe.exists(run_key):
            raise ValueError("Citation runs are immutable; create a new run")
        fingerprint = pipe.get(FINGERPRINT)
        if not fingerprint:
            fingerprint = corpus_fingerprint(read_corpus(client))
        if fingerprint != snapshot["metadata"]["corpus_fingerprint"]:
            raise ValueError("Catalogue changed during analysis; rebuild before publishing")
        if lock is not None and not lock.owned():
            raise RuntimeError("Citation build lease lost")
        pipe.multi()
        pipe.set(run_key, encoded)
        pipe.hset(run_key + ":downloads", mapping={name: base64.b64encode(payload).decode() for name, payload in files.items()})
        pipe.set(CURRENT, snapshot["metadata"]["run_id"])
        pipe.execute()


def load_citations(client=None):
    from .store import redis_client
    client = client or redis_client()
    pipe = client.pipeline(transaction=True)
    pipe.get(CURRENT)
    pipe.get(FINGERPRINT)
    pipe.get("dpe:topics:current")
    run, fingerprint, topic_raw = pipe.execute()
    if not run:
        return {"status": "not_built", "message": "The citation analysis is being prepared."}
    raw = client.get(RUN_PREFIX + run)
    if not raw:
        raise ValueError("Published citation archive is missing")
    snapshot = json.loads(raw)
    if not fingerprint:
        fingerprint = corpus_fingerprint(read_corpus(client))
    topic_run = json.loads(topic_raw)["metadata"]["run_id"] if topic_raw else None
    return {**snapshot, "status": "ready", "stale": fingerprint != snapshot["metadata"]["corpus_fingerprint"],
            "topic_changed": topic_run != snapshot["comparison"].get("topic_run_id")}


def load_download(run, name, client=None):
    from .store import redis_client
    if not re.fullmatch(RUN_PATTERN, run) or name not in DOWNLOADS:
        return None
    raw = (client or redis_client()).hget(RUN_PREFIX + run + ":downloads", name)
    return base64.b64decode(raw) if raw else None
