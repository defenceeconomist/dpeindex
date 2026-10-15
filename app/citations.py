"""Offline, reproducible direct-citation analysis for the existing DPE catalogue."""
from __future__ import annotations

import csv
import html
import io
import json
import zipfile
from collections import Counter

from .citation_data import build_edges, digest, now
from .citation_store import validate_snapshot
from .topic_store import SUMMARY_FIELDS, corpus_fingerprint

PALETTE = ["#237b7b", "#c66b46", "#758945", "#6d70a3", "#b28530", "#a25778", "#4a8eae", "#9b5f3e", "#4e8c6b", "#b95d56", "#767b39", "#906ca4", "#388f96", "#d18c59", "#6085ad", "#b27996", "#809b72", "#ac944a", "#59757d", "#9b7770"]


def graph_for(nodes, edges, *, baseline=False):
    import networkx as nx
    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(n["handle"] for n in nodes))
    graph.add_edges_from((e["source"], e["target"]) for e in edges if not baseline or e["baseline"])
    return graph


def projection(graph):
    import networkx as nx
    result = nx.Graph()
    result.add_nodes_from(sorted(n for n in graph if graph.degree(n)))
    for source, target in sorted(graph.edges):
        result.add_edge(source, target, weight=result.get_edge_data(source, target, {}).get("weight", 0) + 1)
    return result


def partition(graph, resolution=1):
    import networkx as nx
    undirected = projection(graph)
    if not undirected.number_of_edges():
        return [], 0, 0
    trials = []
    for seed in range(20):
        groups = nx.community.louvain_communities(undirected, resolution=resolution, seed=seed)
        canonical = tuple(sorted(tuple(sorted(group)) for group in groups))
        score = nx.community.modularity(undirected, groups, resolution=resolution)
        trials.append((-score, canonical, seed))
    negative, groups, seed = min(trials)
    return sorted([list(g) for g in groups], key=lambda g: (-len(g), g)), -negative, seed


def graph_summary(graph):
    import networkx as nx
    return {"nodes": len(graph), "edges": graph.number_of_edges(), "isolates": len(list(nx.isolates(graph))),
            "weak_components": sorted((len(c) for c in nx.weakly_connected_components(graph)), reverse=True),
            "strong_components": sorted((len(c) for c in nx.strongly_connected_components(graph)), reverse=True)}


def partition_agreement(first, second):
    """Adjusted Rand index on papers connected in both networks; isolates excluded."""
    from math import comb
    left = {h: i for i, group in enumerate(first) for h in group}
    right = {h: i for i, group in enumerate(second) for h in group}
    shared = sorted(left.keys() & right.keys())
    if len(shared) < 2:
        return {"shared_connected_papers": len(shared), "adjusted_rand": None}
    cells = Counter((left[h], right[h]) for h in shared)
    a = sum(comb(n, 2) for n in Counter(left[h] for h in shared).values())
    b = sum(comb(n, 2) for n in Counter(right[h] for h in shared).values())
    observed = sum(comb(n, 2) for n in cells.values())
    expected = a * b / comb(len(shared), 2)
    denominator = (a + b) / 2 - expected
    return {"shared_connected_papers": len(shared), "adjusted_rand": (observed - expected) / denominator if denominator else 1.0}


def theme_comparison(nodes, topics, fingerprint):
    run = topics.get("metadata", {}).get("run_id") if topics else None
    result = {"topic_run_id": run, "status": "unavailable", "rows": []}
    if not topics or topics["metadata"]["corpus_fingerprint"] != fingerprint:
        result["message"] = "No topic snapshot matches the citation corpus."
        return result
    assignments = {d["handle"]: d["topic"] for d in topics["documents"]}
    categories = [{"id": str(t["id"]), "label": t["label"]} for t in topics["topics"]]
    categories += [{"id": "-1", "label": "Unassigned abstract"}, {"id": "missing", "label": "Without an abstract"}]
    for community in sorted({n["community"] for n in nodes}):
        members = [n for n in nodes if n["community"] == community]
        counts = Counter(str(assignments[n["handle"]]) if n["handle"] in assignments else "missing" for n in members)
        result["rows"].append({"community": community, "count": len(members), "cells": [
            {**category, "count": counts[category["id"]],
             "share": 100 * counts[category["id"]] / len(members)} for category in categories]})
    result.update(status="ready", categories=categories,
                  denominator="All eligible articles in each citation community, including missing and unassigned abstracts.")
    return result


def temporal_counts(nodes):
    years = [int(n["year"]) for n in nodes if str(n["year"]).isdigit()]
    groups = {}
    for width, name in [(1, "year"), (5, "five_year")]:
        rows = []
        if years:
            for start in range(min(years) // width * width, max(years) + 1, width):
                members = [n for n in nodes if str(n["year"]).isdigit() and start <= int(n["year"]) < start + width]
                counts = Counter(n["community"] for n in members)
                rows.append({"start": start, "end": start + width - 1, "total": len(members),
                             "partial": start < min(years) or start + width - 1 >= max(years),
                             "counts": {str(c): counts[c] for c in sorted({n["community"] for n in nodes})}})
        groups[name] = rows
    return groups


def build_snapshot(articles, records, decisions, topics, run_id):
    import networkx as nx
    eligible, edges, audit, pending, unmatched = build_edges(articles, records, decisions)
    graph = graph_for(eligible, edges)
    groups, modularity, seed = partition(graph)
    membership = {handle: i for i, group in enumerate(groups) for handle in group}
    undirected = projection(graph)
    coordinates = {}
    components = sorted((sorted(c) for c in nx.connected_components(undirected)), key=lambda c: (-len(c), c))
    for index, component in enumerate(components):
        # Graph views can iterate a small component's node set in hash order.
        # Construct the layout graph explicitly so separate Python processes
        # assign the seeded initial positions to the same papers.
        subgraph = nx.Graph()
        subgraph.add_nodes_from(component)
        members = set(component)
        for source in component:
            for target in sorted(undirected[source]):
                if target in members and source < target:
                    subgraph.add_edge(source, target, **undirected[source][target])
        positions = nx.forceatlas2_layout(subgraph, seed=42, max_iter=500, weight="weight")
        positions = nx.rescale_layout_dict(positions, scale=1 if index == 0 else .09)
        # Disconnected components have no meaningful mutual distance. Pack them
        # explicitly so their repulsion cannot compress the main network.
        dx, dy = (0, 0) if index == 0 else (1.25 + .3 * ((index - 1) % 3), .95 - .25 * ((index - 1) // 3))
        coordinates.update({h: (float(p[0]) + dx, float(p[1]) + dy) for h, p in positions.items()})
    isolates = sorted(nx.isolates(graph))
    for i, handle in enumerate(isolates):
        coordinates[handle] = (-1 + (i % 60) * .05, -1.3 - (i // 60) * .055)
    nodes = [{**{k: a[k] for k in SUMMARY_FIELDS}, "citation_doi": a["citation_doi"],
              "identity_verified": a["identity_verified"], "community": membership.get(a["handle"], -1),
              "in_degree": graph.in_degree(a["handle"]), "out_degree": graph.out_degree(a["handle"]),
              "degree": graph.degree(a["handle"]),
              "x": round(float(coordinates[a["handle"]][0]), 6), "y": round(float(coordinates[a["handle"]][1]), 6)}
             for a in sorted(eligible, key=lambda n: n["handle"])]
    by_handle = {a["handle"]: a for a in eligible}
    node_lookup = {n["handle"]: n for n in nodes}
    communities, community_review = [], []
    for i, group in enumerate(groups):
        group_hash = digest(sorted(group))
        leaders = sorted(group, key=lambda h: (-graph.in_degree(h), -graph.degree(h), h))[:5]
        label = decisions.get("communities", {}).get(group_hash, {})
        communities.append({"id": i, "count": len(group), "membership_sha256": group_hash,
                            "label": label.get("label", f"Community {i + 1}"),
                            "provisional": label.get("provisional", True), "interpretation": label.get("interpretation", "Awaiting interpretation of leading papers."),
                            "leaders": leaders, "degree": sum(graph.degree(h) for h in group)})
        community_review.append({"id": i, "membership_sha256": group_hash, "count": len(group),
                                 "leading_papers": [{**node_lookup[h], "abstract": by_handle[h].get("abstract", "")} for h in leaders]})
    flows = Counter((membership[e["source"]], membership[e["target"]]) for e in edges)
    baseline = graph_for(eligible, edges, baseline=True)
    baseline_groups, baseline_q, baseline_seed = partition(baseline)
    sensitivity = []
    for resolution in [.5, 1.5]:
        alternative, score, alt_seed = partition(graph, resolution)
        sensitivity.append({"resolution": resolution, "communities": len(alternative), "modularity": score, "seed": alt_seed,
                            "memberships": alternative, "agreement_with_primary": partition_agreement(groups, alternative)})
    coverage = []
    for year in sorted({a["year"] for a in articles}):
        rows = [a for a in audit if a["year"] == year]
        included = [a for a in rows if a["eligible"]]
        included_handles = {a["handle"] for a in included}
        coverage.append({"year": year, "catalogue": len(rows), "eligible": len(included), "excluded": len(rows) - len(included),
                         "verified_identities": sum(a["identity_verified"] for a in included),
                         "with_references": sum(a["identity_verified"] and a["reference_count"] > 0 for a in included),
                         "missing_reference_deposit": sum(a["identity_verified"] and a["reference_count"] == 0 for a in included),
                         "unresolved_identities": sum(not a["identity_verified"] for a in included),
                         "no_observed_internal_links": sum(n["degree"] == 0 for n in nodes if n["year"] == year),
                         "unmatched_references": sum(r["source"] in included_handles for r in unmatched),
                         "stated_outside_catalogue": sum(r["source"] in included_handles and r["status"] == "stated_outside_catalogue" for r in unmatched)})
    years = sorted({int(a["year"]) for a in articles if str(a["year"]).isdigit()})
    fingerprint = corpus_fingerprint(articles)
    sample = decisions.get("audit", {}).get("doi_sample", [])
    actual_evidence = {v["reference_id"]: v for e in edges for v in e["evidence"]}
    if any(row.get("reference_id") not in actual_evidence or row.get("reference_sha256") != digest(actual_evidence[row["reference_id"]]["reference"])
           or not row.get("evidence") for row in sample):
        raise ValueError("DOI sample review is stale or lacks supporting evidence")
    snapshot = {"schema_version": 1, "status": "ready", "metadata": {
        "run_id": run_id, "built_at": now(), "corpus_fingerprint": fingerprint,
        "decisions_sha256": digest(decisions), "source_records_sha256": digest(audit),
        "article_count": len(articles), "eligible_count": len(nodes), "excluded_count": len(articles) - len(nodes),
        "edge_count": len(edges), "baseline_edge_count": baseline.number_of_edges(), "isolated_count": len(isolates),
        "year_from": min(years) if years else None, "year_to": max(years) if years else None,
        "retrieved_from": min((a["retrieved_at"] for a in audit if a.get("retrieved_at")), default=None),
        "retrieved_to": max((a["retrieved_at"] for a in audit if a.get("retrieved_at")), default=None),
        "networkx_version": nx.__version__, "algorithm": "Louvain on undirected projection; reciprocal edge weights summed",
        "resolution": 1, "seed": seed, "seeds_tested": list(range(20)), "modularity": modularity,
        "layout": "ForceAtlas2 within components; seed 42; 500 iterations; smaller disconnected components packed at right and isolates below",
        "scope": "Existing 1997–2026 catalogue; pre-1997 papers absent; latest year reflects catalogue coverage."},
        "nodes": nodes, "edges": [{k: e[k] for k in ("source", "target", "baseline")} for e in edges],
        "communities": communities, "flows": [{"source": s, "target": t, "count": c} for (s, t), c in sorted(flows.items())],
        "coverage": coverage, "network": graph_summary(graph),
        "baseline": {**graph_summary(baseline), "communities": len(baseline_groups), "modularity": baseline_q, "seed": baseline_seed,
                     "agreement_with_primary": partition_agreement(groups, baseline_groups)},
        "sensitivity": [{k: v for k, v in row.items() if k != "memberships"} for row in sensitivity],
        "timeline": temporal_counts(nodes), "comparison": theme_comparison(nodes, topics, fingerprint),
        "reference_summary": dict(Counter(r["status"] for r in unmatched)),
        "review": {"pending_count": sum(row["kind"] != "unresolved_reference" for row in pending),
                   "unresolved_reference_count": sum(row["kind"] == "unresolved_reference" for row in pending),
                   "doi_sample": sample},
        "downloads": {}}
    review = {"pending": pending, "communities": community_review, "audit": audit,
              "unmatched_statuses": dict(Counter(r["status"] for r in unmatched)), "doi_sample": sample}
    evidence = {"edges": edges, "unmatched": unmatched, "sensitivity": sensitivity,
                "eligibility_and_identity": audit, "decisions": decisions}
    validate_snapshot(snapshot)
    return snapshot, evidence, review


def csv_bytes(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # Keep spreadsheet applications from interpreting bibliographic text as formulas.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v)
                         for k, v in row.items() if k in fields})
    return stream.getvalue().encode("utf-8-sig")


def figure_bytes(snapshot, *, trimmed=False, fmt="svg"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx
    nodes = [n for n in snapshot["nodes"] if n["degree"] >= (10 if trimmed else 1)]
    graph = graph_for(nodes, [e for e in snapshot["edges"] if e["source"] in {n["handle"] for n in nodes} and e["target"] in {n["handle"] for n in nodes}])
    positions = {n["handle"]: (n["x"], n["y"]) for n in nodes}
    fig, ax = plt.subplots(figsize=(16, 10), facecolor="#f7f8f4")
    fig.subplots_adjust(left=.03, right=.72, top=.91, bottom=.12)
    ax.set_facecolor("#f7f8f4")
    if nodes:
        nx.draw_networkx_edges(graph, positions, ax=ax, arrows=False, width=.3, alpha=.16, edge_color="#506d70")
        nx.draw_networkx_nodes(graph, positions, ax=ax, nodelist=[n["handle"] for n in nodes],
                               node_size=[5 + 1.7 * n["degree"] for n in nodes],
                               node_color=[PALETTE[n["community"] % 20] for n in nodes], linewidths=.2, edgecolors="white", alpha=.85)
        from matplotlib.lines import Line2D
        shown = {n["community"] for n in nodes}
        communities = [c for c in snapshot["communities"] if c["id"] in shown]
        handles = [Line2D([], [], color=PALETTE[c["id"] % 20], marker="o", linestyle="None", markersize=5) for c in communities]
        labels = [(c["label"][:47] + "…" if len(c["label"]) > 48 else c["label"]) + f" ({c['count']})" for c in communities]
        fig.legend(handles, labels, loc="center left", bbox_to_anchor=(.73, .5), fontsize=7, frameon=False, labelspacing=.9)
    fig.suptitle("Defence and Peace Economics · internal citations" + (" · degree ≥ 10" if trimmed else ""), fontsize=17, x=.04, ha="left")
    ax.set_aspect("equal")
    ax.axis("off")
    fig.text(.04, .035, f"{len(nodes):,} displayed papers · {graph.number_of_edges():,} displayed links · "
             "node area reflects whole-network degree · colours identify citation communities\n"
             "Arrowheads omitted for legibility; directed edges retained in the downloadable data. " + snapshot["metadata"]["run_id"], fontsize=8, color="#506d70")
    output = io.BytesIO()
    fig.savefig(output, format=fmt, dpi=200, bbox_inches="tight", metadata={"Date": None} if fmt == "svg" else None)
    plt.close(fig)
    return output.getvalue()


def exports(snapshot, evidence, review, inputs=None):
    import base64
    import networkx as nx
    files = {}
    nodes = [{**n, "authors": "; ".join(n["authors"])} for n in snapshot["nodes"]]
    files["nodes.csv"] = csv_bytes(nodes, list(nodes[0]) if nodes else list(SUMMARY_FIELDS))
    files["edges.csv"] = csv_bytes(snapshot["edges"], ["source", "target", "baseline"])
    files["baseline-edges.csv"] = csv_bytes([e for e in snapshot["edges"] if e["baseline"]], ["source", "target", "baseline"])
    files["coverage.csv"] = csv_bytes(snapshot["coverage"], list(snapshot["coverage"][0]) if snapshot["coverage"] else ["year"])
    graph = graph_for(nodes, snapshot["edges"])
    for node in nodes:
        graph.nodes[node["handle"]].update({k: v for k, v in node.items() if v is not None})
        graph.nodes[node["handle"]]["label"] = node["title"]
    output = io.BytesIO()
    nx.write_gexf(graph, output)
    files["network.gexf"] = output.getvalue()
    for name, trimmed, fmt in [("network.svg", False, "svg"), ("network.png", False, "png"), ("degree-10.svg", True, "svg")]:
        files[name] = figure_bytes(snapshot, trimmed=trimmed, fmt=fmt)
    for name, value in [("evidence.json", evidence), ("review.json", review)]:
        files[name] = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()
    meta = snapshot["metadata"]
    def compact_summary(summary):
        return {k: dict(Counter(v)) if k in {"weak_components", "strong_components"} else v for k, v in summary.items()}
    rows = "".join(f"<tr><td>{html.escape(str(c['year']))}</td><td>{c['eligible']}</td><td>{c['with_references']}</td><td>{c['unresolved_identities']}</td><td>{c['no_observed_internal_links']}</td></tr>" for c in snapshot["coverage"])
    communities = "".join(f"<li><strong>{html.escape(c['label'])}</strong> ({c['count']} articles): "
                           f"{html.escape(c['interpretation'])}</li>" for c in snapshot["communities"])
    report = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>DPE citation analysis</title>
<style>body{{font:16px/1.7 system-ui;max-width:1000px;margin:3em auto;padding:0 1em;color:#183b40}}h1,h2{{line-height:1.2}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}img{{width:100%}}code{{overflow-wrap:anywhere}}li{{margin:1em 0}}</style>
<h1>Conversations within Defence and Peace Economics</h1><p>{meta['year_from']}–{meta['year_to']} catalogue coverage · run <code>{meta['run_id']}</code></p>
<p>{meta['article_count']:,} catalogue records; {meta['eligible_count']:,} eligible papers; {meta['edge_count']:,} observed directed links; {meta['baseline_edge_count']:,} DOI-only links; {meta['isolated_count']:,} papers with no observed internal links.</p>
<h2>Method</h2><p>Adapted from Delahais and Thyrard, <a href="https://doi.org/10.1177/13563890251406246">Conversations in Evaluation</a> and its <a href="https://osf.io/rs3m5/">public replication script</a>. RePEc supplies the article universe; Crossref supplies references. No Scopus data are used.</p>
<p>One node per RePEc handle; one edge from citing to cited paper. Repeated references are deduplicated and article self-loops removed. Shared authors do not remove an edge. Editorials, issue introductions, administrative records, corrections and routine book notices are excluded. Substantive reviews remain eligible. Every decision is preserved in evidence.json.</p>
<p>DOIs are normalized and identities checked against metadata. Unique exact bibliographic matches use journal, volume, first page and author surname, or exact title and surname. Ambiguities require documented review; fuzzy matches are not automatically accepted. The recovered graph is accompanied by a DOI-only baseline. Recovered links and DOI corrections are audited before publication.</p>
<p>Louvain communities are computed on an undirected projection, summing reciprocal edge weights. Resolution 1; seeds 0–19; maximum modularity with deterministic tie-breaking. Selected seed {meta['seed']}, modularity {meta['modularity']:.4f}. Resolutions 0.5 and 1.5 are sensitivity checks. NetworkX {meta['networkx_version']}. ForceAtlas2 seed 42, 500 iterations within each component; disconnected components are packed at the right and isolated papers below. Their relative placement is arbitrary. All discovered communities are retained.</p>
<h2>Limits and denominators</h2><p>Pre-1997 papers are outside this catalogue. Publication years follow RePEc issue years, which can differ from first-online dates. 2026 and end periods reflect catalogue availability. Missing Crossref deposits and unmatched references can remove genuine edges; no observed link does not establish no citation. Citations do not establish agreement or substantive intellectual exchange. Community labels describe reviewed leading titles and available abstracts.</p>
<p>Temporal shares use all eligible papers in a period, including papers without observed links; pooled communities remain fixed. Topic comparisons use all eligible community members, with missing and unassigned abstracts reported separately. The bound topic run is <code>{html.escape(str(snapshot['comparison'].get('topic_run_id')))}</code>.</p>
<p>Retrieved {meta['retrieved_from']} through {meta['retrieved_to']}. Corpus SHA256 <code>{meta['corpus_fingerprint']}</code>; decisions SHA256 <code>{meta['decisions_sha256']}</code>.</p>
<h2>Network</h2><img src="data:image/svg+xml;base64,{base64.b64encode(files['network.svg']).decode()}" alt="Internal citation network"><h2>Communities</h2><ol>{communities}</ol>
<h2>Coverage by year</h2><table><tr><th>Year</th><th>Eligible</th><th>Reference deposits</th><th>Unresolved identities</th><th>No observed links</th></tr>{rows}</table>
<h2>Baseline and sensitivity</h2><p>Component summaries below give component size followed by frequency. Adjusted Rand agreement compares connected papers present in both partitions; 1 means identical assignments up to relabelling. Modularity objectives at different resolutions are not directly comparable.</p><pre>{html.escape(json.dumps({'baseline':compact_summary(snapshot['baseline']),'sensitivity':snapshot['sensitivity'],'network':compact_summary(snapshot['network']),'unmatched_reference_statuses':snapshot['reference_summary']},indent=2))}</pre>
<p>Pending required review items: {snapshot['review']['pending_count']}. Unresolved reference candidates retained in the review queue: {snapshot['review']['unresolved_reference_count']}. These candidates do not create links. DOI sample reviewed: {len(snapshot['review']['doi_sample'])}. Download evidence.json and review.json for reference-level evidence and leading abstracts.</p></html>"""
    files["report.html"] = report.encode()
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
        archive.writestr("snapshot.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
        for name, value in (inputs or {}).items():
            archive.writestr("inputs/" + name + ".json", json.dumps(value, ensure_ascii=False, indent=2))
    files["bundle.zip"] = bundle.getvalue()
    return files
