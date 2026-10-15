"""Small workers run only against the update project's private datastores."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import time

from .citation_data import digest, write_json


def read(path):
    return json.loads(Path(path).read_text())


def sync(directory, seed=None):
    from .store import (COLLECTION, PREFIX, collection_exists, embed_text, point_id,
                        qdrant, redis_client, sync_articles)
    articles = read(directory / "corpus.json")
    client = redis_client()
    seeded = 0
    if seed:
        if not collection_exists():
            qdrant("PUT", f"/collections/{COLLECTION}", {"vectors": {"size": 384, "distance": "Cosine"}})
        existing = set(client.smembers(PREFIX + "ids"))
        points, pipeline = [], client.pipeline()
        for a in articles:
            item = seed.get(a["handle"])
            text_hash = hashlib.sha256(embed_text(a).encode()).hexdigest()
            if item and item["digest"] == text_hash and point_id(a["handle"]) not in existing:
                points.append(item["point"])
                pipeline.set(PREFIX + "digest:" + point_id(a["handle"]), text_hash)
                seeded += 1
        for start in range(0, len(points), 64):
            qdrant("PUT", f"/collections/{COLLECTION}/points?wait=true", {"points": points[start:start+64]})
        pipeline.execute()
    start = time.monotonic()
    result = sync_articles(articles, source_url="Frozen staged update")
    points, offset = [], None
    while True:
        body = {"limit": 256, "with_payload": True, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        response = qdrant("POST", f"/collections/{COLLECTION}/points/scroll", body)["result"]
        points.extend(response["points"])
        offset = response.get("next_page_offset")
        if offset is None:
            break
    lookup = {p["payload"]["handle"]: p for p in points}
    write_json(directory / "points.json", {a["handle"]: {"point": lookup[a["handle"]],
        "digest": hashlib.sha256(embed_text(a).encode()).hexdigest()} for a in articles})
    result.update(seeded=seeded, elapsed_seconds=time.monotonic()-start)
    write_json(directory / "sync-metrics.json", result)


def graph(directory, records_path, decisions_path, topics_path):
    from .citations import build_snapshot
    from .update_topics import new_id
    articles, frozen = read(directory / "corpus.json"), read(records_path)
    records = {a["handle"]: frozen[a["handle"]] for a in articles}
    if any(r.get("sha256") != digest(r.get("message")) or r.get("status") not in {"ok", "not_found"} for r in records.values()):
        raise ValueError("Missing, unsuccessful or corrupt citation records; collect before building.")
    decisions = copy.deepcopy(read(decisions_path))
    included = {a["handle"] for a in articles}
    decisions["outside_catalogue_handles"] = sorted(set(frozen) - included)
    samples = decisions.get("audit", {}).get("doi_sample", [])
    applicable = [s for s in samples if s["source"] in included and s["target"] in included]
    decisions.setdefault("audit", {})["doi_sample"] = applicable
    start = time.monotonic()
    snapshot, evidence, review = build_snapshot(articles, records, decisions, read(topics_path), new_id())
    review["outside_catalogue_audit_samples"] = [s for s in samples if s not in applicable]
    for name, value in [("graph", snapshot), ("evidence", evidence), ("review", review), ("decisions", decisions)]:
        write_json(directory / (name + ".json"), value)
    write_json(directory / "citation-metrics.json", {"elapsed_seconds": time.monotonic()-start,
        "edges": len(snapshot["edges"]), "communities": len(snapshot["communities"]),
        "isolates": snapshot["metadata"]["isolated_count"], "pending": snapshot["review"]["pending_count"],
        "replayed_records": len(records), "external_requests": 0})


def package(directory, graph_dir, records_path):
    from .citations import exports, theme_comparison
    from .update_topics import new_id
    snapshot = copy.deepcopy(read(graph_dir / "graph.json"))
    topics = read(directory / "topics.json")
    articles = read(directory / "corpus.json")
    decisions = read(graph_dir / "decisions.json")
    snapshot["metadata"]["run_id"] = new_id()
    snapshot["comparison"] = theme_comparison(snapshot["nodes"], topics, snapshot["metadata"]["corpus_fingerprint"])
    evidence, review = read(graph_dir / "evidence.json"), read(graph_dir / "review.json")
    records = read(records_path)
    inputs = {"corpus": articles, "records": {a["handle"]: records[a["handle"]] for a in articles},
              "topics": topics, "decisions": decisions}
    start = time.monotonic()
    files = exports(snapshot, evidence, review, inputs)
    snapshot["downloads"] = {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in files.items()}
    target = directory / "citation-downloads"
    target.mkdir(exist_ok=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    write_json(directory / "citations.json", snapshot)
    write_json(directory / "citation-package-metrics.json", {"elapsed_seconds": time.monotonic()-start})


def export(directory):
    from .citation_store import DOWNLOADS, publish_snapshot as publish_citations
    from .export_static import capture, export_dataset
    from .store import redis_client
    from .topic_store import publish_snapshot as publish_topics
    client = redis_client()
    topics, citations = read(directory / "topics.json"), read(directory / "citations.json")
    publish_topics(topics, client)
    # Resume may encounter an already published immutable run.
    archive_key = "dpe:citations:run:" + citations["metadata"]["run_id"]
    if not client.exists(archive_key):
        files = {name: (directory / "citation-downloads" / name).read_bytes() for name in DOWNLOADS}
        publish_citations(citations, files, client)
    else:
        if json.loads(client.get(archive_key)) != citations:
            raise ValueError("Immutable citation run changed")
        client.set("dpe:citations:current", citations["metadata"]["run_id"])
    values, archives = capture(client)
    output = directory / "dataset"
    output.mkdir(exist_ok=True)
    manifest = export_dataset(output, values, archives)
    if topics.get("stale") or citations.get("stale") or manifest["corpus_fingerprint"] != topics["metadata"]["corpus_fingerprint"]:
        raise ValueError("A complete staged release must have matching analyses.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["sync", "graph", "package", "export"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--topics", type=Path)
    parser.add_argument("--graph", type=Path)
    args = parser.parse_args()
    if args.operation == "sync":
        sync(args.directory, read(args.seed) if args.seed else None)
    elif args.operation == "graph":
        graph(args.directory, args.records, args.decisions, args.topics)
    elif args.operation == "package":
        package(args.directory, args.graph, args.records)
    else:
        export(args.directory)


if __name__ == "__main__":
    main()
