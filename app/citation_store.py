"""Immutable citation snapshots and downloads; no analysis or network calls on reads."""
from __future__ import annotations

import json
import math
import re



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
