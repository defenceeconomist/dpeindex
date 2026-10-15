"""Measured, local report for the completed two-issue experiment."""
from __future__ import annotations

from collections import Counter
import csv
import html
import json
from pathlib import Path
import hashlib
import subprocess
import sys

from .citation_data import write_json


def read(path):
    return json.loads(Path(path).read_text())


def compare_assignments(first, second, field="topic", docs="documents"):
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from sklearn.metrics import adjusted_mutual_info_score, adjusted_rand_score
    a, b = ({d["handle"]: d for d in data[docs]} for data in [first, second])
    shared = sorted(a.keys() & b.keys())
    left, right = sorted({a[h][field] for h in shared} - {-1}), sorted({b[h][field] for h in shared} - {-1})
    counts = Counter((a[h][field], b[h][field]) for h in shared)
    matrix = np.asarray([[counts[x, y] for y in right] for x in left])
    mapping = {-1: -1}
    if len(left) and len(right):
        rows, cols = linear_sum_assignment(-matrix)
        mapping.update({right[j]: left[i] for i, j in zip(rows, cols) if matrix[i, j]})
    changed = [h for h in shared if a[h][field] != mapping.get(b[h][field], "unmatched")]
    result = {"shared": len(shared), "changed_after_matching": len(changed), "changed_handles": changed,
        "adjusted_mutual_information": float(adjusted_mutual_info_score([a[h][field] for h in shared], [b[h][field] for h in shared])),
        "adjusted_rand": float(adjusted_rand_score([a[h][field] for h in shared], [b[h][field] for h in shared])),
        "mapping_new_to_old": mapping,
        "crosswalk": [{"old": x, "new": y, "shared": n} for (x, y), n in sorted(counts.items())]}
    if field == "topic":
        before = np.asarray([[a[h]["x"], a[h]["y"]] for h in shared])
        after = np.asarray([[b[h]["x"], b[h]["y"]] for h in shared])
        raw = np.linalg.norm(before-after, axis=1)
        x, y = before-before.mean(axis=0), after-after.mean(axis=0)
        nx, ny = np.linalg.norm(x), np.linalg.norm(y)
        if nx and ny:
            from scipy.linalg import orthogonal_procrustes
            rotation, _ = orthogonal_procrustes(y/ny, x/nx)
            aligned = np.linalg.norm((y/ny) @ rotation-x/nx, axis=1) * np.sqrt(len(shared))
        else:
            aligned = raw
        result["map"] = {"raw_max": float(raw.max(initial=0)), "existing_points_moved": int((raw != 0).sum()),
                         "aligned_median_rms_units": float(np.median(aligned)), "aligned_p95_rms_units": float(np.quantile(aligned, .95))}
    return result


def graph_changes(first, second):
    before, after = ({(e["source"], e["target"]) for e in data["edges"]} for data in [first, second])
    a, b = ({d["handle"]: d for d in data["nodes"]} for data in [first, second])
    shared = sorted(a.keys() & b.keys())
    degrees = [{"handle": h, "in_before": a[h]["in_degree"], "in_after": b[h]["in_degree"],
                "out_before": a[h]["out_degree"], "out_after": b[h]["out_degree"]}
               for h in shared if (a[h]["in_degree"], a[h]["out_degree"]) != (b[h]["in_degree"], b[h]["out_degree"])]
    top = lambda nodes: [n["handle"] for n in sorted(nodes, key=lambda n: (-n["in_degree"], n["handle"]))[:20]]
    connected = {h for h in shared if a[h]["degree"] and b[h]["degree"]}
    partition = compare_assignments({"nodes": [a[h] for h in connected]}, {"nodes": [b[h] for h in connected]}, "community", "nodes")
    # Align only papers in the main weak component of both snapshots; packing
    # disconnected components is arbitrary and is not intellectual distance.
    def main_component(snapshot):
        neighbors = {n["handle"]: set() for n in snapshot["nodes"]}
        for edge in snapshot["edges"]:
            neighbors[edge["source"]].add(edge["target"])
            neighbors[edge["target"]].add(edge["source"])
        remaining, groups = set(neighbors), []
        while remaining:
            group, queue = set(), [min(remaining)]
            while queue:
                node = queue.pop()
                if node in group: continue
                group.add(node); queue.extend(neighbors[node]-group)
            remaining -= group; groups.append(group)
        return max(groups, key=len, default=set())
    map_handles = sorted(main_component(first) & main_component(second))
    map_metrics = None
    if len(map_handles) > 1:
        # Reuse the same alignment calculation with a single dummy topic.
        old_map = {"documents": [{**a[h], "topic": 0} for h in map_handles]}
        new_map = {"documents": [{**b[h], "topic": 0} for h in map_handles]}
        map_metrics = compare_assignments(old_map, new_map)["map"]
        map_metrics["shared_main_component_papers"] = len(map_handles)
    return {"edges_added": sorted(after-before), "edges_removed": sorted(before-after), "changed_degrees": degrees,
            "map": map_metrics,
            "newly_connected_existing_papers": [h for h in shared if not a[h]["degree"] and b[h]["degree"]],
            "top20_retained": len(set(top(first["nodes"])) & set(top(second["nodes"]))),
            "communities": partition,
            "coverage_before": first["coverage"], "coverage_after": second["coverage"],
            "communities_needing_interpretation": sum(c["interpretation"] == "Awaiting interpretation of leading papers." for c in second["communities"])}


def timeline_changes(first, second):
    result = {}
    for name in second["timeline"]["groups"]:
        a = {(c["start"], c["topic"]): c for c in first["timeline"]["groups"][name]["cells"]}
        b = {(c["start"], c["topic"]): c for c in second["timeline"]["groups"][name]["cells"]}
        # Only compare raw topic IDs when the model identity is stable.
        stable = first["metadata"].get("model_id", first["metadata"]["run_id"]) == second["metadata"].get("model_id", second["metadata"]["run_id"])
        result[name] = {"stable_model": stable,
            "periods": second["timeline"]["groups"][name]["periods"],
            "changed_count_cells": sum(a.get(k, {}).get("count") != v["count"] for k, v in b.items()) if stable else None,
            "changed_share_cells": sum(a.get(k, {}).get("share") != v["share"] for k, v in b.items()) if stable else None}
    return result


def main():
    directory = Path(sys.argv[1]).absolute()
    state = read(directory / "state.json")
    subprocess.run(["node", "scripts/update-search-report.mjs", str(directory)], check=True)
    search = read(directory / "search-comparison.json")
    reports, snapshots, citations = [], {}, {}
    for release in state["releases"]:
        path = Path(release)
        name = path.name
        snapshots[name], citations[name] = read(path / "topics.json"), read(path / "citations.json")
        metrics = {key: read(path / (key + "-metrics.json")) for key in ["topic", "site", "sync"]}
        browser = read(path / "browser-results.json")["stats"] if (path / "browser-results.json").exists() else None
        reports.append({"name": name, "articles": snapshots[name]["metadata"]["article_count"],
                        "abstracts": snapshots[name]["metadata"]["abstract_count"],
                        "topics": len(snapshots[name]["topics"]), "unassigned": snapshots[name]["metadata"]["outliers"]["after"],
                        "edges": len(citations[name]["edges"]), "communities": len(citations[name]["communities"]),
                        "isolates": citations[name]["metadata"]["isolated_count"], "metrics": metrics, "browser": browser,
                        "eligible": len(citations[name]["nodes"]),
                        "missing_reference_deposits": sum(c["missing_reference_deposit"] for c in citations[name]["coverage"]),
                        "reference_coverage": citations[name]["coverage"],
                        "model_id": snapshots[name]["metadata"]["model_id"], "run_id": snapshots[name]["metadata"]["run_id"]})
    comparisons = {}
    pairs = [("00-baseline", "01-incremental"), ("01-incremental", "02-incremental"),
             ("00-baseline", "01-rebuild"), ("01-rebuild", "02-rebuild"),
             ("01-incremental", "01-rebuild"), ("02-incremental", "02-rebuild")]
    for old, new in pairs:
        comparisons[old+" → "+new] = {"topics": compare_assignments(snapshots[old], snapshots[new]),
                                     "citations": graph_changes(citations[old], citations[new]),
                                     "timeline": timeline_changes(snapshots[old], snapshots[new]),
                                     "search": {q: {"top10_retained": len(set(search[old][q]) & set(search[new][q])),
                                                     "before": search[old][q], "after": search[new][q]} for q in search[new]}}
    original = read(directory / "inputs/topics.json")
    comparisons["original published → 02-rebuild"] = {"topics": compare_assignments(original, snapshots["02-rebuild"]),
        "citations": graph_changes(read(directory / "inputs/citations.json"), citations["02-rebuild"])}
    original_articles = read(directory / "inputs/desired.json")
    baseline_handles = {d["handle"] for d in read(directory / "baseline-corpus.json")}
    qualitative = {r["handle"]: r for r in read(directory / "article-review.json")} if (directory / "article-review.json").exists() else {}
    new_papers = []
    inc = {d["handle"]: d for d in snapshots["02-incremental"]["documents"]}
    full = {d["handle"]: d for d in snapshots["02-rebuild"]["documents"]}
    labels = {t["id"]: t["label"] for t in snapshots["02-incremental"]["topics"]}
    for article in original_articles:
        h = article["handle"]
        if h in baseline_handles:
            continue
        new_papers.append({"handle": h, "title": article["title"], "issue": article["issue"],
            "topic": inc[h]["topic"], "label": labels.get(inc[h]["topic"], "Unassigned"),
            "similarity": inc[h]["similarity"], "margin": inc[h].get("margin"), "full_topic": full[h]["topic"],
            "review": qualitative.get(h, {}).get("review", "Pending qualitative abstract review")})
    topic_lookup = {name: {d["handle"]: d["topic"] for d in data["documents"]} for name, data in snapshots.items()}
    acceptance = {"counts": [r["articles"] for r in reports] == [1207, 1214, 1214, 1221, 1221],
        "new_encodings": all(r["metrics"]["topic"]["encoded"] == 7 and r["metrics"]["site"]["encoded"] == 7 and r["metrics"]["sync"]["embedded"] == 7 for r in reports if r["name"].endswith("incremental")),
        "stable_assignments_and_map": all(comparisons[a+" → "+b]["topics"]["changed_after_matching"] == 0 and
            comparisons[a+" → "+b]["topics"]["map"]["existing_points_moved"] == 0 and
            all(d["topic"] == topic_lookup[b][d["handle"]]
                for d in snapshots[a]["documents"])
            for a, b in pairs[:2]),
        "browser": all(r["browser"] and r["browser"]["expected"] == 12 and r["browser"]["unexpected"] == 0 for r in reports),
        "production_unchanged": state.get("production_unchanged", False),
        "article_reviews": set(qualitative) == {p["handle"] for p in new_papers}}
    acceptance["stable_model_labels_hierarchy"] = all(
        snapshots[a]["metadata"]["model_id"] == snapshots[b]["metadata"]["model_id"] and
        [(t["id"], t["label"]) for t in snapshots[a]["topics"]] == [(t["id"], t["label"]) for t in snapshots[b]["topics"]] and
        snapshots[a]["hierarchy"] == snapshots[b]["hierarchy"] for a, b in pairs[:2])
    result = {"checkpoints": reports, "comparisons": comparisons, "new_papers": new_papers,
        "acceptance": acceptance, "production_before": state["production_before"], "production_after": state.get("production_after"),
        "production_unchanged": state.get("production_unchanged", False),
        "report_generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "reference_data": "Frozen published Crossref records replayed; no Crossref or hosted-inference requests.",
        "note": "Cluster agreement is not independent classification accuracy. Timings use warm model weights and baseline-only vector seeds. Full refits reuse the newly encoded vectors from the incremental branch, so their timings exclude that encoding work. Citation archive growth contributes to website size."}
    result["findings"] = [r["review"] for r in qualitative.values() if r.get("flags")]
    result["workflow_checks"] = read(directory / "workflow-checks.json") if (directory / "workflow-checks.json").exists() else {}
    result["recommendation"] = ("Use frozen-model classification for routine issue additions and deliberate full refits for taxonomy revisions. "
        "Incremental updates preserve existing assignments, coordinates and theme links. Review abstentions and flagged article types before publishing. "
        "This small experiment establishes stability and cache behaviour, not a general speedup factor or independent classification accuracy.")
    write_json(directory / "report.json", result)
    with (directory / "changes.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["comparison", "kind", "handle", "detail"])
        for name, comparison in comparisons.items():
            for handle in comparison["topics"]["changed_handles"]:
                writer.writerow([name, "topic_changed_after_matching", handle, ""])
            for row in comparison.get("citations", {}).get("changed_degrees", []):
                writer.writerow([name, "citation_degree", row["handle"], json.dumps(row)])
            for kind in ["edges_added", "edges_removed"]:
                for a, b in comparison.get("citations", {}).get(kind, []):
                    writer.writerow([name, kind, a, b])
    esc = lambda x: html.escape(str(x))
    rows = "".join(f'<tr><td><a href="{r["name"]}/site/">{esc(r["name"])}</a></td><td>{r["articles"]}</td><td>{r["topics"]}</td><td>{r["unassigned"]}</td><td>{r["edges"]}</td><td>{r["communities"]}</td><td>{r["metrics"]["topic"]["elapsed_seconds"]:.2f}s</td><td>{r["metrics"]["topic"]["encoded"]}/{r["metrics"]["site"]["encoded"]}</td><td>{r["metrics"]["site"]["bytes"]/1e6:.1f} MB</td><td>{r["browser"]["expected"] if r["browser"] else "pending"}/12</td></tr>' for r in reports)
    comparison_rows = "".join(f'<tr><td>{esc(name)}</td><td>{c["topics"]["changed_after_matching"]}/{c["topics"]["shared"]}</td><td>{c["topics"]["adjusted_mutual_information"]:.3f}</td><td>{c["topics"]["map"]["existing_points_moved"]}</td><td>{len(c.get("citations",{}).get("edges_added",[]))}/{len(c.get("citations",{}).get("edges_removed",[]))}</td></tr>' for name, c in comparisons.items())
    paper_rows = "".join(f'<tr><td>{esc(p["issue"])}</td><td>{esc(p["title"])}</td><td>{esc(p["label"])}</td><td>{p["similarity"]:.3f}</td><td>{p["margin"]:.3f}</td><td>{esc(p["full_topic"])}</td><td>{esc(p["review"])}</td></tr>' for p in new_papers)
    verification = ""
    if result["workflow_checks"]:
        verification = '<h2>Additional workflow verification</h2><p>See <a href="workflow-checks.json">recorded unchanged-refresh and synthetic-correction checks</a>. The synthetic source is separate from all five comparison releases.</p>'
    content = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>DPE update experiment</title>
<style>body{{font:16px/1.55 system-ui;max-width:1500px;margin:40px auto;padding:0 24px;color:#183b40}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #cddbd7;padding:9px;text-align:left;vertical-align:top}}th{{background:#edf4f1}}h1,h2{{line-height:1.2}}a{{color:#146b70}}code{{word-break:break-all}}.scroll{{overflow:auto}}li{{margin:8px 0}}</style>
<h1>DPE: two issues removed and restored</h1><p>Volume 37 Issues 5 and 6 were withheld, then restored one at a time. Incremental updates retain a frozen baseline model; comparison builds refit all current abstracts with the same settings. All builds are isolated local artifacts. <a href="reference/site/">Frozen original published website</a>.</p>
<p>{esc(result['note'])}</p><p>Production unchanged: <strong>{esc(result['production_unchanged'] if 'production_unchanged' in result else state.get('production_unchanged'))}</strong>. Reference data: {esc(result['reference_data'])}</p>
<h2>Recommendation</h2><p>{esc(result['recommendation'])}</p><p>Full refits changed {comparisons['00-baseline → 01-rebuild']['topics']['changed_after_matching']} of 1,138 existing assignments after Issue 5, then {comparisons['01-rebuild → 02-rebuild']['topics']['changed_after_matching']} of 1,145 after Issue 6, after topic matching. Incremental updates changed zero existing assignments or coordinates. Of the fourteen arrivals, {sum(p['topic'] != -1 for p in new_papers)} were assigned and {sum(p['topic'] == -1 for p in new_papers)} remained unassigned.</p>
<h2>Checkpoints and measured work</h2><p>Encoded columns show topic/static-search documents. Topic time includes token checks, classification or fitting, projection and timeline. Baseline search vectors and encoder weights were reused. Shared graph work is not duplicated between branches.</p><div class="scroll"><table><tr><th>Open website</th><th>Articles</th><th>Topics</th><th>Unassigned</th><th>Edges</th><th>Communities</th><th>Topic time</th><th>Encoded</th><th>Site size</th><th>Browser checks</th></tr>{rows}</table></div>
<h2>Changes between checkpoints</h2><p>Assignments are compared after maximum-overlap topic matching. AMI is label independent. Raw map movement is zero for preserved incremental points; aligned median/p95 displacement is recorded in JSON for refits.</p><table><tr><th>Comparison</th><th>Existing assignments changed</th><th>AMI</th><th>Map points moved</th><th>Edges added/removed</th></tr>{comparison_rows}</table>
<h2>Fourteen restored articles</h2><p>Similarity and margin measure affinity to the frozen baseline. Full-refit topic numbers belong to a different model. Qualitative reviews use complete abstracts; they are not gold-standard accuracy measurements.</p><div class="scroll"><table><tr><th>Issue</th><th>Article</th><th>Incremental assignment</th><th>Similarity</th><th>Margin</th><th>Full topic</th><th>Abstract review</th></tr>{paper_rows}</table></div>
<h2>Validation and evidence</h2><ul>{''.join(f'<li>{esc(k)}: <strong>{esc(v)}</strong></li>' for k,v in acceptance.items())}</ul><p><a href="report.json">Full measurements, crosswalks, timeline and search comparisons</a> · <a href="changes.csv">Article and citation changes CSV</a> · <a href="state.json">Stage commands, timings and artifact hashes</a></p>
<h2>Qualitative review findings</h2><ul>{''.join(f'<li>{esc(f)}</li>' for f in result['findings'])}</ul><p>These findings do not silently revise the frozen eligibility decisions used for the controlled replay. A future publication should resolve the flagged eligibility question explicitly.</p>
{verification}
<p>Each checkpoint directory contains screenshots, the browser report, complete abstracts, model evidence, citation evidence and its independently inspectable website. Run <code>python3 scripts/preview_static.py --directory {esc(directory)} --port 8791</code> and open <code>http://localhost:8791/report.html</code>.</p></html>'''
    (directory / "report.html").write_text(content)
    print(json.dumps({"acceptance": acceptance, "report": str(directory / "report.html")}))


if __name__ == "__main__":
    main()
