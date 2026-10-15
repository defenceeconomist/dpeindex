"""Export a validated local release to the static website data contract."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from .citation_store import DOWNLOADS, validate_snapshot as validate_citations
from .store import catalog_for, embed_text
from .theme_filter import theme_options
from .topic_store import corpus_fingerprint, validate_snapshot
from .topic_timeline import validate_timeline

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def file_hashes(directory):
    return {str(path.relative_to(directory)).replace(os.sep, "/"):
            {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            for path in sorted(directory.rglob("*")) if path.is_file() and path.name != "manifest.json"}


def export_dataset(directory, articles, topics, citations, source, citation_directory=None):
    catalog = catalog_for(articles)
    fingerprint = corpus_fingerprint(articles)
    if len({a["handle"] for a in articles}) != len(articles):
        raise ValueError("Duplicate article handles")
    validate_snapshot(topics)
    validate_timeline(topics["timeline"], topics)
    if topics["metadata"]["corpus_fingerprint"] != fingerprint:
        raise ValueError("Topic corpus differs from release")
    if {d["handle"] for d in topics["documents"]} != {a["handle"] for a in articles if a["abstract"].strip()}:
        raise ValueError("Topic document identities differ from release")
    if citation_directory and citations["metadata"]["corpus_fingerprint"] != fingerprint:
        raise ValueError("Citation corpus differs from release")
    data = directory / "data"
    write_json(data / "catalog.json", catalog)
    write_json(data / "source.json", source)
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
    if citation_directory:
        validate_citations(citations)
        if citations["review"]["pending_count"] or not citations["review"].get("doi_sample"):
            raise ValueError("Citation publication review is incomplete")
        for name in DOWNLOADS:
            payload = (citation_directory / name).read_bytes()
            expected = citations["downloads"][name]
            if hashlib.sha256(payload).hexdigest() != expected["sha256"] or len(payload) != expected["bytes"]:
                raise ValueError("Citation download integrity failure")
            path = directory / "api/citations" / citations["metadata"]["run_id"] / "download" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    else:
        (directory / "api").mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                "corpus_fingerprint": fingerprint, "article_count": len(articles),
                "topic_run_id": topics["metadata"]["run_id"],
                "topic_model_id": topics["metadata"].get("model_id", topics["metadata"]["run_id"]),
                "citation_run_id": citations.get("metadata", {}).get("run_id"), "issues": issues,
                "files": file_hashes(directory)}
    manifest["downloads"] = {path: info for path, info in manifest["files"].items() if path.startswith("api/citations/")}
    manifest["release_id"] = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()[:20]
    write_json(data / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from .update import read, verify_release
    release = args.release or Path(read(Path("data/local-artifacts/current.json"))["release"])
    verify_release(release)
    if args.output.exists():
        raise ValueError("Export output already exists; choose a new directory")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=args.output.name + '.building-', dir=args.output.parent))
    try:
        citations_dir = release / "citations"
        export_dataset(staged, read(release / "corpus.json"), read(release / "topics.json"),
                       read(release / "citations.json"), read(release / "source.json"),
                       citations_dir if citations_dir.exists() else None)
        staged.rename(args.output)
    finally:
        if staged.exists(): shutil.rmtree(staged)

if __name__ == "__main__":
    main()
