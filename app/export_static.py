"""Capture published Redis data and export a complete, immutable website dataset.

This module deliberately imports no modelling or embedding dependencies.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime, timezone

import redis

from .citation_store import (CURRENT as CITATIONS_CURRENT, DOWNLOADS, RUN_PATTERN,
                             RUN_PREFIX, load_citations, validate_snapshot as validate_citations)
from .store import article_key, embed_text
from .theme_filter import theme_options
from .topic_store import CURRENT, FINGERPRINT, corpus_fingerprint, load_topics, validate_snapshot
from .topic_timeline import PREFIX as TIMELINE_PREFIX, load_timeline


class FrozenRedis:
    """The read-only interface used by the existing published-payload loaders."""
    def __init__(self, values):
        self.values = values

    def get(self, key):
        return self.values.get(key)

    def pipeline(self, **kwargs):
        frozen = self
        class Pipeline:
            def __init__(self): self.keys = []
            def get(self, key): self.keys.append(key); return self
            def execute(self): return [frozen.get(key) for key in self.keys]
        return Pipeline()


def capture(client, attempts=5):
    """WATCH all independently published pointers while atomically reading inputs."""
    roots = ["dpe:catalog", "dpe:source", FINGERPRINT, CURRENT, CITATIONS_CURRENT]
    for _ in range(attempts):
        with client.pipeline() as pipe:
            try:
                pipe.watch(*roots)
                initial = dict(zip(roots, pipe.mget(roots), strict=True))
                catalog = json.loads(initial["dpe:catalog"] or "null")
                topics = json.loads(initial[CURRENT] or "null")
                if not catalog or not topics or not initial[CITATIONS_CURRENT]:
                    raise ValueError("Sync the catalogue and publish topics and citations before exporting.")
                timeline_key = TIMELINE_PREFIX + topics["metadata"]["run_id"]
                archive_keys = sorted(key for key in client.scan_iter(RUN_PREFIX + "*")
                                      if re.fullmatch(re.escape(RUN_PREFIX) + RUN_PATTERN, key))
                active_key = RUN_PREFIX + initial[CITATIONS_CURRENT]
                if active_key not in archive_keys:
                    raise ValueError("Published citation archive is missing.")
                hashes = [key + ":downloads" for key in archive_keys]
                keys = list(dict.fromkeys(roots + [timeline_key] + archive_keys +
                                          [article_key(a["handle"]) for a in catalog["articles"]]))
                pipe.watch(timeline_key, *archive_keys, *hashes)
                pipe.multi()
                pipe.mget(keys)
                for key in hashes:
                    pipe.hgetall(key)
                result = pipe.execute()
                values = dict(zip(keys, result[0], strict=True))
                return values, dict(zip(archive_keys, result[1:], strict=True))
            except redis.WatchError:
                continue
    raise RuntimeError("Published data kept changing during export. Retry once updates finish.")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def file_hashes(directory):
    return {str(path.relative_to(directory)).replace(os.sep, "/"):
            {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            for path in sorted(directory.rglob("*")) if path.is_file() and path.name != "manifest.json"}


def export_dataset(directory, values, archives):
    frozen = FrozenRedis(values)
    catalog = json.loads(frozen.get("dpe:catalog"))
    if catalog["count"] != len(catalog["articles"]):
        raise ValueError("Catalogue counts are inconsistent.")
    articles = []
    for summary in catalog["articles"]:
        raw = frozen.get(article_key(summary["handle"]))
        if not raw:
            raise ValueError("Catalogue contains a missing article.")
        article = json.loads(raw)
        if any(article.get(key) != value for key, value in summary.items()):
            raise ValueError("Article metadata differs from the catalogue.")
        articles.append(article)
    if len({a["handle"] for a in articles}) != len(articles):
        raise ValueError("Duplicate article handles.")
    fingerprint = corpus_fingerprint(articles)
    if frozen.get(FINGERPRINT) and fingerprint != frozen.get(FINGERPRINT):
        raise ValueError("Canonical corpus fingerprint does not match its articles.")
    # Older catalogues predate the stored fingerprint; the live loaders use the
    # same computed fallback. Freeze it here rather than reading live Redis.
    values[FINGERPRINT] = fingerprint
    topics = load_topics(frozen)
    validate_snapshot(topics)
    topics["timeline"] = load_timeline(topics, frozen)
    if topics["timeline"]["status"] != "ready":
        raise ValueError("Publish a valid timeline for the current topic model before exporting.")
    citations = load_citations(frozen)
    validate_citations(citations)
    by_handle = {a["handle"]: a for a in articles}
    if any(d["handle"] not in by_handle for d in topics["documents"]):
        raise ValueError("Topic model references articles outside this catalogue.")
    data = directory / "data"
    write_json(data / "catalog.json", catalog)
    write_json(data / "source.json", json.loads(frozen.get("dpe:source") or "{}"))
    write_json(data / "themes.json", theme_options(topics, catalog))
    write_json(data / "topics.json", topics)
    write_json(data / "citations.json", citations)
    issues = {}
    for volume in catalog["volumes"]:
        for issue in volume["issues"]:
            key = f'{volume["number"]}:{issue["number"]}'
            name = "data/issues/" + hashlib.sha256(key.encode()).hexdigest()[:20] + ".json"
            selected = [a for a in articles if a["volume"] == volume["number"] and a["issue"] == issue["number"]]
            if len(selected) != issue["count"]:
                raise ValueError("Issue count differs from its article records.")
            write_json(directory / name, {"volume": volume["number"], "issue": issue["number"], "articles": selected})
            issues[key] = name
    write_json(data / "search.json", [{**{key: a[key] for key in (
        "handle", "title", "authors", "abstract", "volume", "issue", "year", "pages", "doi")},
        "text": embed_text(a)} for a in articles])
    for key, files in archives.items():
        snapshot = json.loads(frozen.get(key))
        validate_citations(snapshot)
        if snapshot["review"]["pending_count"] or not snapshot["review"].get("doi_sample"):
            raise ValueError("A citation archive has not completed publication review.")
        if set(files) != set(DOWNLOADS):
            raise ValueError("Published citation downloads are incomplete.")
        for name, encoded in files.items():
            payload = base64.b64decode(encoded, validate=True)
            expected = snapshot["downloads"][name]
            if hashlib.sha256(payload).hexdigest() != expected["sha256"] or len(payload) != expected["bytes"]:
                raise ValueError("Published citation download hash or size does not match.")
            path = directory / "api/citations" / snapshot["metadata"]["run_id"] / "download" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    manifest = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                "corpus_fingerprint": fingerprint, "article_count": len(articles),
                "topic_run_id": topics["metadata"]["run_id"],
                "citation_run_id": citations["metadata"]["run_id"], "issues": issues,
                "files": file_hashes(directory)}
    manifest["downloads"] = {path: info for path, info in manifest["files"].items() if path.startswith("api/citations/")}
    manifest["release_id"] = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()[:20]
    write_json(data / "manifest.json", manifest)
    return manifest


def replace_directory(staged, output):
    """Only a complete staged build can replace the last successful export."""
    backup = None
    if output.is_symlink():
        raise ValueError("Output directory must not be a symbolic link.")
    if output.exists():
        backup = Path(tempfile.mkdtemp(prefix=output.name + ".previous-", dir=output.parent))
        backup.rmdir()
        output.rename(backup)
    try:
        staged.rename(output)
    except BaseException:
        if backup:
            backup.rename(output)
        raise
    if backup:
        shutil.rmtree(backup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    values, archives = capture(redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/4"), decode_responses=True))
    staged = Path(tempfile.mkdtemp(prefix=output.name + ".building-", dir=output.parent))
    try:
        manifest = export_dataset(staged, values, archives)
        replace_directory(staged, output)
    finally:
        if staged.exists(): shutil.rmtree(staged)
    print(json.dumps({"output": str(output), "articles": manifest["article_count"], "release": manifest["release_id"]}))


if __name__ == "__main__":
    main()
