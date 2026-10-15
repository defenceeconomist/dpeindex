"""Prepare isolated, resumable DPE releases. This command never publishes Pages.

python -m app.update simulate --output data/update-artifacts/two-issue --device mps
python -m app.update refresh --source data/defpea.redif --mode rebuild --output data/update-artifacts/initial
python -m app.update refresh --fetch --previous data/update-artifacts/initial --output data/update-artifacts/next
python -m app.update status data/update-artifacts/two-issue
python -m app.update resume data/update-artifacts/two-issue
"""
from __future__ import annotations

import argparse
import base64
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile

from .citation_data import digest, write_json
from .redif import parse_file
from .sync import SOURCE
from .topic_store import corpus_fingerprint

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "data/update-artifacts"

# Runs inside the existing web container. Only GET/scroll/read operations.
CAPTURE = '''import base64,hashlib,json
from app.store import redis_client,qdrant,COLLECTION,embed_text
from app.topic_store import read_corpus,load_topics
from app.citation_store import load_citations
c=redis_client(); articles=read_corpus(c); topics=load_topics(c); citations=load_citations(c)
points=[]; offset=None
while True:
 body={"limit":256,"with_payload":True,"with_vector":True}
 if offset is not None: body["offset"]=offset
 r=qdrant("POST",f"/collections/{COLLECTION}/points/scroll",body)["result"]
 points.extend(r["points"]); offset=r.get("next_page_offset")
 if offset is None: break
lookup={p["payload"]["handle"]:p for p in points}
seeds={a["handle"]:{"point":lookup[a["handle"]],"digest":hashlib.sha256(embed_text(a).encode()).hexdigest()} for a in articles}
keys=["dpe:catalog","dpe:source","dpe:topics:current","dpe:citations:current","dpe:topics:catalog-fingerprint"]
proof={k:hashlib.sha256((c.get(k) or "").encode()).hexdigest() for k in keys}
proof["qdrant"]=hashlib.sha256(json.dumps(sorted(points,key=lambda p:str(p["id"])),sort_keys=True).encode()).hexdigest()
bundle=c.hget("dpe:citations:run:"+citations["metadata"]["run_id"]+":downloads","bundle.zip")
print(json.dumps({"articles":articles,"topics":topics,"citations":citations,"points":seeds,"proof":proof,"bundle":bundle}))
'''


def read(path):
    return json.loads(Path(path).read_text())


def relative(path):
    return str(Path(path).absolute().relative_to(ROOT))


def hashes(paths):
    result = {}
    for path in paths:
        path = Path(path)
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in files:
            if file.is_file():
                result[relative(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def diff_articles(before, after):
    old, new = {a["handle"]: a for a in before}, {a["handle"]: a for a in after}
    if len(old) != len(before) or len(new) != len(after):
        raise ValueError("Duplicate article handles in source")
    return {"added": sorted(new.keys()-old.keys()), "removed": sorted(old.keys()-new.keys()),
            "changed": sorted(h for h in old.keys() & new.keys() if old[h] != new[h]),
            "unchanged": len([h for h in old.keys() & new.keys() if old[h] == new[h]])}


@contextmanager
def lease(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("w") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another update is using this workspace") from None
        yield


def capture_production():
    raw = subprocess.check_output(["docker", "compose", "exec", "-T", "dpe-index", "python", "-c", CAPTURE], cwd=ROOT)
    result = json.loads(raw)
    remote = subprocess.check_output(["git", "ls-remote", "--heads", "origin", "gh-pages"], cwd=ROOT, text=True).strip()
    result["proof"].update(source_sha256=hashlib.sha256((ROOT / "data/defpea.redif").read_bytes()).hexdigest(),
                           gh_pages=remote)
    return result


def compose(state):
    return ["docker", "compose", "-f", "docker-compose.update.yml", "-p", state["project"]]


def execute(command, log, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as output:
        result = subprocess.run([str(a) for a in command], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-24:])
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}\n{tail}")


def worker(state, kind, *args):
    return compose(state) + ["run", "--rm", "--no-deps", kind, *map(str, args)]


def checkpoint_model(directory):
    if (directory / "model").exists():
        return directory / "model"
    return ROOT / read(directory / "model-link.json")["path"]


def last_checkpoint(workspace):
    direct = (workspace / "topics.json").exists()
    owner = next((p for p in [workspace, *workspace.parents] if (p / "state.json").exists()), None)
    if owner is None or not owner.is_relative_to(ARTIFACTS):
        raise ValueError("Previous model must belong to a verified local update workspace")
    state = read(owner / "state.json")
    if not state.get("releases"):
        raise ValueError("Previous workspace has no complete release")
    for name, entry in state["steps"].items():
        if entry.get("status") == "complete" and hashes([ROOT / p for p in entry["hashes"]]) != entry["hashes"]:
            raise ValueError(f"Previous workspace artifacts changed: {name}")
    candidates = [p for p in state["releases"] if p.endswith("-incremental")]
    selected = workspace if direct else ROOT / (candidates[-1] if candidates else state["releases"][-1])
    if relative(selected) not in state["releases"]:
        raise ValueError("Previous checkpoint has not completed its release")
    model = checkpoint_model(selected)
    known = {p for entry in state["steps"].values() if entry.get("status") == "complete" for p in entry["hashes"]}
    if relative(model / "map.joblib") not in known:
        raise ValueError("Saved projection is outside the verified model artifacts")
    return selected


def initialise(args):
    """Failed preparation leaves a reusable empty workspace, not a half-frozen run."""
    directory = args.output.absolute()
    if not directory.is_relative_to(ARTIFACTS) or directory == ARTIFACTS:
        raise ValueError("Choose an output below data/update-artifacts/")
    if any(p.name != ".lock" for p in directory.iterdir()):
        raise ValueError("Workspace is not empty; use resume or choose a new output")
    with tempfile.TemporaryDirectory(prefix="preparing-", dir=ARTIFACTS) as temporary:
        staged_args = copy.copy(args)
        staged_args.output = Path(temporary)
        state = _initialise(staged_args)
        old_prefix, new_prefix = state["directory"] + "/", relative(directory) + "/"
        state["directory"] = relative(directory)
        state["input_hashes"] = {(new_prefix+k[len(old_prefix):] if k.startswith(old_prefix) else k): v
                                  for k, v in state["input_hashes"].items()}
        for path in Path(temporary).iterdir():
            path.rename(directory / path.name)
        write_json(directory / "state.json", state)
    return state


def _initialise(args):
    directory = args.output.absolute()
    if not directory.is_relative_to(ARTIFACTS) or directory == ARTIFACTS:
        raise ValueError("Choose an output below data/update-artifacts/ so Docker writes remain isolated")
    if (directory / "state.json").exists():
        raise ValueError("Workspace already exists; use resume or choose a new output")
    directory.mkdir(parents=True, exist_ok=True)
    frozen = capture_production()
    inputs = directory / "inputs"
    inputs.mkdir()
    for name in ["articles", "topics", "citations", "points"]:
        write_json(inputs / (name + ".json"), frozen[name])
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(frozen["bundle"]))) as bundle:
        # Resolve the bundle's input paths without depending on archive order.
        for name in ["records", "decisions"]:
            matches = [p for p in bundle.namelist() if p == name + ".json" or p.endswith("/" + name + ".json")]
            if len(matches) != 1:
                raise ValueError(f"Published citation bundle lacks unique {name}.json")
            write_json(inputs / (name + ".json"), json.loads(bundle.read(matches[0])))
    shutil.copyfile(inputs / "decisions.json", inputs / "decisions-original.json")
    if args.labels:
        shutil.copyfile(args.labels, inputs / "labels.json")
    source_files = [p for folder in ["app", "site", "scripts", "tests"] for p in (ROOT / folder).rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts]
    source_files += [p for pattern in ["requirements*.txt", "package*.json", "docker-compose*.yml", "Dockerfile", "playwright.config.mjs"]
                     for p in ROOT.glob(pattern)]
    with zipfile.ZipFile(inputs / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files:
            archive.write(path, relative(path))
    images = json.loads(subprocess.check_output(["docker", "image", "inspect", "dpe-dpe-index:latest", "dpe-dpe-citations:latest",
                                                 "redis:7.4-alpine", "qdrant/qdrant:latest"], text=True))
    write_json(inputs / "environment.json", {"python": sys.version, "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "images": [{"id": image["Id"], "tags": image.get("RepoTags"), "architecture": image["Architecture"]} for image in images],
        "source_files": hashes(source_files)})
    old = frozen["articles"]
    source = args.source if getattr(args, "source", None) else ROOT / "data/defpea.redif"
    if getattr(args, "fetch", False):
        with urllib.request.urlopen(SOURCE, timeout=90) as response:
            content = response.read()
        if b"Template-Type: ReDIF-Article" not in content or len(content) < 100_000:
            raise ValueError("RePEc response is not a complete expected source")
        (inputs / "source.redif").write_bytes(content)
    else:
        shutil.copyfile(source, inputs / "source.redif")
    articles = sorted(parse_file(inputs / "source.redif"), key=lambda a: a["handle"])
    if len(articles) < 1000:
        raise ValueError("A refresh requires a complete catalogue, not an issue-only file")
    previous = last_checkpoint(args.previous.absolute()) if getattr(args, "previous", None) else None
    write_json(inputs / "settings.json", read(previous / "topics.json") if previous else frozen["topics"])
    reference = read(previous / "corpus.json") if previous else old
    changes = diff_articles(reference, articles)
    if args.command == "refresh" and changes["removed"] and not args.allow_removals:
        raise ValueError(f"Source removes {len(changes['removed'])} articles: {changes['removed'][:5]}. Use --allow-removals intentionally.")
    if args.command == "refresh" and args.mode == "incremental" and previous is None:
        raise ValueError("Incremental refresh needs --previous with persisted model artifacts; bootstrap with --mode rebuild")
    write_json(inputs / "desired.json", articles)
    write_json(inputs / "diff.json", changes)
    if args.command == "simulate" and (ROOT / "dist/release.json").exists():
        release = read(ROOT / "dist/release.json")
        manifest = read(ROOT / "dist" / release["manifest"])
        if (manifest["corpus_fingerprint"] == frozen["topics"]["metadata"]["corpus_fingerprint"]
                and manifest["topic_run_id"] == frozen["topics"]["metadata"]["run_id"]):
            shutil.copytree(ROOT / "dist", directory / "reference/site")
    state = {"schema_version": 1, "operation": args.command,
             "project": "dpe-update-" + uuid.uuid4().hex[:10], "status": "prepared",
             "directory": relative(directory), "device": args.device,
             "topic_python": str(Path(args.topic_python).absolute()),
             "previous": relative(previous) if previous else None,
             "mode": getattr(args, "mode", "incremental"), "issues": getattr(args, "issues", None),
             "refresh_references": getattr(args, "refresh_references", False),
             "no_op": args.command == "refresh" and args.mode == "incremental" and not args.refresh_references and not any(changes[k] for k in ["added", "changed", "removed"]),
             "steps": {}, "releases": [], "production_before": frozen["proof"],
             "input_hashes": hashes([inputs / (name + ".json") for name in
                              ["articles", "topics", "settings", "citations", "points", "records", "decisions-original", "desired", "environment"]] + [inputs / "source.redif", inputs / "source.zip"]),
             "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    write_json(directory / "state.json", state)
    if (inputs / "labels.json").exists():
        state["input_hashes"].update(hashes([inputs / "labels.json"]))
        write_json(directory / "state.json", state)
    if (directory / "reference/site").exists():
        state["input_hashes"].update(hashes([directory / "reference/site"]))
        write_json(directory / "state.json", state)
    return state


def step(state, name, command, outputs, env=None):
    directory = ROOT / state["directory"]
    entry = state["steps"].get(name)
    if entry and entry["status"] == "complete":
        if hashes([ROOT / path for path in entry["hashes"]]) != entry["hashes"]:
            raise ValueError(f"Completed stage {name} has changed or missing artifacts; preserve it and start a new workspace")
        return
    print(f"[{time.strftime('%H:%M:%S')}] {name}", flush=True)
    start = time.monotonic()
    state["status"] = "running"
    state["steps"][name] = {"status": "running", "command": list(map(str, command))}
    write_json(directory / "state.json", state)
    try:
        execute(command, directory / "logs" / (name.replace("/", "-") + ".log"), env)
        observed = hashes(outputs)
        if not observed or any(not Path(p).exists() for p in outputs):
            raise ValueError(f"Stage {name} did not produce its required outputs")
        state["steps"][name].update(status="complete", elapsed_seconds=time.monotonic()-start, hashes=observed)
    except Exception as error:
        state["steps"][name].update(status="failed", error=str(error), elapsed_seconds=time.monotonic()-start)
        state["status"] = "failed"
        raise
    finally:
        write_json(directory / "state.json", state)


def bootstrap(state, baseline):
    directory = ROOT / state["directory"]
    cache = directory / "cache"
    cache.mkdir(exist_ok=True)
    if not (cache / "hf").exists():
        hf_source = ROOT / "data/topic-artifacts/hf-cache"
        if hf_source.exists():
            shutil.copytree(hf_source, cache / "hf", symlinks=False)
    embeddings = cache / "topics/embeddings"
    embeddings.mkdir(parents=True, exist_ok=True)
    # Seed baseline-only data. Future issues must exercise actual new-document work.
    from .topics import embedding_key
    revision = read(directory / "inputs/settings.json")["metadata"]["model_revision"]
    for a in baseline:
        if a["abstract"].strip():
            key = embedding_key(a["abstract"], revision) + ".npy"
            source = ROOT / "data/topic-artifacts/embeddings" / key
            if source.exists() and not (embeddings / key).exists():
                shutil.copyfile(source, embeddings / key)
    previous = state.get("previous")
    if previous:
        parent = ROOT / previous
        model = checkpoint_model(parent)
        target = cache / "frozen-model"
        if not target.exists():
            shutil.copytree(model, target)
        parent_cache = next((p for p in [parent, *parent.parents] if (p / "state.json").exists()), None)
        if parent_cache:
            for name in ["topics/embeddings", "static/vectors", "citations"]:
                source = parent_cache / "cache" / name
                if source.exists():
                    shutil.copytree(source, cache / name, dirs_exist_ok=True)
    execute(["node", "scripts/seed-update-cache.mjs", relative(directory), relative(directory / "baseline-corpus.json")],
            directory / "logs/cache-seed.log")
    subprocess.run(compose(state) + ["up", "-d", "--wait", "redis", "qdrant"], cwd=ROOT, check=True)
    volume = state["project"] + "_model-cache"
    subprocess.run(["docker", "volume", "create", volume], check=True, stdout=subprocess.DEVNULL)
    # The source mount is read-only; all runtime cache writes go to our own volume.
    marker = cache / "docker-models-seeded"
    if not marker.exists():
        execute(["docker", "run", "--rm", "-v", "dpe_dpe-model-cache:/source:ro", "-v", volume + ":/dest",
                 "python:3.12-slim", "python", "-c", "import shutil;shutil.copytree('/source','/dest',dirs_exist_ok=True)"],
                directory / "logs/docker-model-seed.log")
        marker.write_text("read-only source copied\n")


def stages(state):
    directory = ROOT / state["directory"]
    articles = read(directory / "inputs/desired.json")
    inputs, cache = directory / "inputs", directory / "cache"
    if state["operation"] == "simulate":
        issues = [tuple(issue.split(":")) for issue in state["issues"]]
        if len(issues) != 2 or len(set(issues)) != 2:
            raise ValueError("Simulation needs two distinct volume:issue identifiers in arrival order")
        baseline = [a for a in articles if (a["volume"], a["issue"]) not in issues]
        if any(not any((a["volume"], a["issue"]) == issue for a in articles) for issue in issues):
            raise ValueError("Requested issue is absent from source")
        groups = [("00-baseline", baseline, "rebuild", None)]
        accumulated = baseline[:]
        prior = "00-baseline"
        for index, issue in enumerate(issues, 1):
            accumulated += [a for a in articles if (a["volume"], a["issue"]) == issue]
            inc, full = f"{index:02d}-incremental", f"{index:02d}-rebuild"
            groups += [(inc, accumulated[:], "incremental", prior), (full, accumulated[:], "rebuild", None)]
            prior = inc
    else:
        baseline = articles
        groups = [("release", articles, state["mode"], state.get("previous"))]
    write_json(directory / "baseline-corpus.json", sorted(baseline, key=lambda a: a["handle"]))
    return groups


def run(state):
    directory = ROOT / state["directory"]
    if hashes([ROOT / p for p in state["input_hashes"]]) != state["input_hashes"]:
        raise ValueError("Frozen inputs changed; use a new workspace")
    if state.get("no_op"):
        state.update(status="complete", releases=[state["previous"]], document_encodings=0,
                     production_after=state["production_before"], production_unchanged=True)
        write_json(directory / "state.json", state)
        print("No-op: source is unchanged; previous release retained, zero document encodings.")
        return
    if state["status"] == "complete":
        for name, entry in state["steps"].items():
            if entry.get("status") == "complete" and hashes([ROOT / p for p in entry["hashes"]]) != entry["hashes"]:
                raise ValueError(f"Completed artifacts changed: {name}")
        if all(state["steps"].get(Path(p).name + "/browser", {}).get("status") == "complete" for p in state["releases"]):
            print("No-op: completed inputs and artifacts verified; zero document encodings.")
            return
    groups = stages(state)
    bootstrap(state, read(directory / "baseline-corpus.json"))
    seed_path = directory / "inputs/points.json"
    if state["operation"] == "simulate":
        baseline_handles = {a["handle"] for a in read(directory / "baseline-corpus.json")}
        write_json(directory / "baseline-points.json", {h: p for h, p in read(seed_path).items() if h in baseline_handles})
        seed_path = directory / "baseline-points.json"
    env = dict(os.environ, HF_HOME=str(directory / "cache/hf"), TOKENIZERS_PARALLELISM="false",
               NUMBA_CACHE_DIR=str(directory / "cache/numba"), OMP_NUM_THREADS="4", NUMBA_NUM_THREADS="4")
    if state["operation"] == "simulate":
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    inputs = directory / "inputs"
    # Decisions are intentionally editable at a review gate; frozen originals remain intact.
    decision_hash = digest(read(inputs / "decisions.json"))
    if state.get("decision_hash") and state["decision_hash"] != decision_hash:
        unfinished = state.get("current_checkpoint")
        for name in list(state["steps"]):
            if unfinished and name.startswith(unfinished + "/") and name.rsplit("/", 1)[-1] in {"collect", "graph", "package", "export", "site", "browser"}:
                del state["steps"][name]
    state["decision_hash"] = decision_hash
    for name, corpus, mode, parent in groups:
        target = directory / name
        target.mkdir(exist_ok=True)
        corpus_path = target / "corpus.json"
        ordered = sorted(corpus, key=lambda a: a["handle"])
        if corpus_path.exists() and read(corpus_path) != ordered:
            raise ValueError("Checkpoint corpus changed")
        write_json(corpus_path, ordered)
        state["current_checkpoint"] = name
        print(f"Checkpoint {name}: {len(corpus)} articles / {sum(bool(a['abstract'].strip()) for a in corpus)} abstracts", flush=True)
        seed = seed_path
        if state.get("previous") and (ROOT / state["previous"] / "points.json").exists():
            seed = ROOT / state["previous"] / "points.json"
        step(state, name+"/sync", worker(state, "sync", "sync", relative(target), "--seed", relative(seed)),
             [target / "sync-metrics.json", target / "points.json"])
        topic_command = [state["topic_python"], "-m", "app.update_topics", "--corpus", relative(corpus_path),
                         "--settings", relative(inputs / "settings.json"), "--output", relative(target),
                         "--cache", relative(directory / "cache/topics"), "--device", state["device"]]
        model = target / "model"
        if mode == "incremental":
            previous = ROOT / parent if state["operation"] == "refresh" else directory / parent
            model = directory / "00-baseline/model" if state["operation"] == "simulate" else directory / "cache/frozen-model"
            write_json(target / "model-link.json", {"path": relative(model)})
            topic_command += ["--previous", relative(previous), "--model", relative(model)]
        step(state, name+"/topics", topic_command,
             [target / "topics.json", target / "coverage.json", target / "topic-metrics.json"] + ([model] if mode == "rebuild" else []), env)
        graph_dir = target if mode != "rebuild" or name == "00-baseline" or state["operation"] == "refresh" else directory / name.replace("rebuild", "incremental")
        if graph_dir == target:
            if state["operation"] == "refresh":
                step(state, name+"/collect", [sys.executable, "-m", "app.update", "_collect", str(directory)], [inputs / "working-records.json", inputs / "collection-metrics.json"])
            records = inputs / ("working-records.json" if state["operation"] == "refresh" else "records.json")
            step(state, name+"/graph", worker(state, "citations", "graph", relative(target), "--records", relative(records),
                    "--decisions", relative(inputs / "decisions.json"), "--topics", relative(target / "topics.json")),
                 [target / (n+".json") for n in ["graph", "evidence", "review", "decisions", "citation-metrics"]])
        else:
            records = inputs / "records.json"
        graph = read(graph_dir / "graph.json")
        if graph["review"]["pending_count"] or not graph["review"].get("doi_sample"):
            state["status"] = "needs_review"
            write_json(directory / "state.json", state)
            print(f"Review {graph_dir / 'review.json'}, edit {inputs / 'decisions.json'}, then resume.")
            return
        step(state, name+"/package", worker(state, "citations", "package", relative(target), "--graph", relative(graph_dir), "--records", relative(records)),
             [target / "citations.json", target / "citation-downloads", target / "citation-package-metrics.json"])
        step(state, name+"/export", worker(state, "sync", "export", relative(target)), [target / "dataset"])
        step(state, name+"/site", ["node", "scripts/build-static.mjs", "--dataset", relative(target / "dataset"),
             "--output", relative(target / "site"), "--cache", relative(directory / "cache/static"),
             "--metrics", relative(target / "site-metrics.json")], [target / "site", target / "site-metrics.json"])
        if relative(target) not in state["releases"]:
            state["releases"].append(relative(target))
        write_json(directory / "state.json", state)
    for release in state["releases"]:
        target = ROOT / release
        step(state, target.name+"/browser", ["node", "scripts/verify-update-site.mjs", relative(target)],
             [target / "browser-results.json", target / "screenshots"])
    state["production_after"] = capture_production()["proof"]
    state["production_unchanged"] = state["production_before"] == state["production_after"]
    if not state["production_unchanged"]:
        raise ValueError("Production changed during the experiment; inspect before/after proof before attributing changes")
    if state["operation"] == "simulate":
        step(state, "report", [state["topic_python"], "-m", "app.update_report", str(directory)],
             [directory / "report.html", directory / "report.json", directory / "changes.csv"])
    state["status"] = "complete"
    subprocess.run(compose(state) + ["stop"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    state["stores_running"] = False
    write_json(directory / "state.json", state)
    print(f"Complete: {directory}", flush=True)


def collect_refresh(directory):
    from .citation_data import collect, normalize_doi, read_records
    articles = read(directory / "inputs/desired.json")
    decisions = read(directory / "inputs/decisions.json")
    cache = directory / "cache/citations"
    for record in read(directory / "inputs/records.json").values():
        from .citation_data import cache_path
        path = cache_path(cache, record["doi"])
        if not path.exists():
            write_json(path, record)
    stats = collect(articles, cache, decisions, refresh=read(directory / "state.json").get("refresh_references", False),
                    contact=os.getenv("CROSSREF_MAILTO", ""))
    write_json(directory / "inputs/collection-metrics.json", stats)
    write_json(directory / "inputs/working-records.json", read_records(articles, cache, decisions))


def main():
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    default_python = os.getenv("DPE_TOPIC_PYTHON", str(ROOT / "data/topic-artifacts/host-venv/bin/python"))
    for name in ["simulate", "refresh"]:
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
        p.add_argument("--source", type=Path)
        p.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
        p.add_argument("--topic-python", default=default_python)
        p.add_argument("--labels", type=Path, help="Reviewed label manifest; applied only to an exact baseline corpus and evidence match")
        if name == "simulate":
            p.add_argument("--issues", nargs=2, default=["37:5", "37:6"])
        else:
            p.add_argument("--previous", type=Path)
            p.add_argument("--mode", choices=["incremental", "rebuild"], default="incremental")
            p.add_argument("--allow-removals", action="store_true")
            p.add_argument("--fetch", action="store_true")
            p.add_argument("--refresh-references", action="store_true", help="Explicitly refresh Crossref deposits; changed evidence requires renewed review")
    for name in ["status", "resume", "_collect"]:
        p = sub.add_parser(name)
        p.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "_collect":
        collect_refresh(args.directory)
        return
    if args.command == "status":
        state = read(args.directory / "state.json")
        print(json.dumps({k: state[k] for k in ["status", "project", "releases", "steps"]}, indent=2))
        return
    directory = (args.directory if args.command == "resume" else args.output).absolute()
    with lease(directory):
        state = read(directory / "state.json") if args.command == "resume" else initialise(args)
        run(state)


if __name__ == "__main__":
    main()
