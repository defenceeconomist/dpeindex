import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.citation_data import (build_edges, cache_path, digest, eligibility, fetch_record,
                               identity_matches, match_reference, normalize, normalize_doi, write_json)
from app.citations import build_snapshot, graph_for, graph_summary, partition, projection, theme_comparison
from app.citation_store import DOWNLOADS, validate_snapshot
from app.topic_store import corpus_fingerprint


def article(name, year="2000", volume="11", page="10"):
    return {"handle": "RePEc:test:" + name, "title": name + " and security", "authors": [name.title() + " Researcher"],
            "volume": volume, "issue": "1", "year": year, "pages": page + "-19", "doi": "10.1234/" + name,
            "abstract": "An investigation of security, growth and public policy."}


def record(a, refs=()):
    message = {"title": [a["title"]], "volume": a["volume"], "page": a["pages"],
               "author": [{"family": "Researcher"}], "reference": list(refs), "reference-count": len(refs)}
    return {"status": "ok", "doi": a["doi"], "message": message, "sha256": digest(message), "retrieved_at": "2026-10-15T12:00:00Z"}


def fixture():
    articles = [article("alpha"), article("beta", "2001", page="20"), article("gamma", "2002", page="30"), article("isolated", "2003", page="40")]
    a, b, c, d = articles
    records = {a["handle"]: record(a), b["handle"]: record(b, [{"DOI": a["doi"]}, {"DOI": a["doi"]}]),
               c["handle"]: record(c, [{"DOI": a["doi"]}, {"DOI": b["doi"]}, {"DOI": c["doi"]}]), d["handle"]: record(d)}
    return articles, records


class MatchingTests(unittest.TestCase):
    def test_reviewed_target_withheld_from_catalogue_keeps_evidence_without_an_edge(self):
        articles, records = fixture()
        source, target = articles[2], articles[0]
        ref = {"DOI": target["doi"]}
        records[source["handle"]] = record(source, [ref])
        decision = {"references": {source["handle"]+"#0": {"target": target["handle"],
            "reference_sha256": digest(ref), "evidence": "Verified target DOI"}},
            "outside_catalogue_handles": [target["handle"]]}
        subset = articles[1:]
        _, edges, _, _, unmatched = build_edges(subset, records, decision)
        self.assertFalse(any(e["source"] == source["handle"] for e in edges))
        row = next(r for r in unmatched if r["source"] == source["handle"])
        self.assertEqual(row["status"], "reviewed_outside_catalogue")
        self.assertEqual(row["review"]["target"], target["handle"])
        decision["outside_catalogue_handles"] = []
        with self.assertRaisesRegex(ValueError, "not an eligible"):
            build_edges(subset, records, decision)

    def test_normalization_html_ordinals_and_doi(self):
        self.assertEqual(normalize_doi(" DOI: https://doi.org/10.1234/HELLO "), "10.1234/hello")
        self.assertEqual(normalize_doi("https://dx.doi.org/10.1234/HELLO"), "10.1234/hello")
        self.assertEqual(normalize("The<i>Lancet</i> survey"), "the lancet survey")
        self.assertEqual(normalize("21<sup>st</sup> century"), "21st century")
        self.assertEqual(normalize("CO<sub>2</sub> emissions"), "co2 emissions")
        self.assertEqual(normalize("Title<sup>1</sup>"), "title")

    def test_unique_exact_recovery_and_ambiguities(self):
        a = article("alpha")
        ref = {"journal-title": "Defence & Peace Economics", "volume": "11", "first-page": "10", "author": "Researcher A."}
        self.assertEqual(match_reference(ref, [a], {})[:2], (a["handle"], "bibliographic"))
        other = {**a, "handle": "another-paper"}
        self.assertIsNone(match_reference(ref, [a, other], {})[0])
        self.assertEqual(match_reference({"article-title": a["title"], "author": "Researcher A."}, [a], {})[:2], (a["handle"], "title_author"))
        self.assertEqual(match_reference({"DOI": a["doi"]}, [a], {a["doi"]: [a["handle"], "another-paper"]})[1], "ambiguous_doi")
        self.assertIsNone(match_reference({"article-title": "alpha security", "author": "Researcher A."}, [a], {})[0])

    def test_conflicting_doi_cannot_supply_references(self):
        articles, records = fixture()
        records[articles[1]["handle"]] = record(articles[0], [{"DOI": articles[2]["doi"]}])
        eligible, edges, audit, pending, _ = build_edges(articles, records, {})
        self.assertFalse(next(a for a in audit if a["handle"] == articles[1]["handle"])["identity_verified"])
        self.assertFalse(any(e["source"] == articles[1]["handle"] for e in edges))
        self.assertTrue(any(r["kind"] == "identity" for r in pending))

    def test_recovery_requires_review_and_reference_hash_binding(self):
        articles, records = fixture()
        source, target = articles[2], articles[0]
        ref = {"article-title": target["title"], "author": "Researcher A."}
        records[source["handle"]] = record(source, [ref])
        _, edges, _, pending, _ = build_edges(articles, records, {})
        self.assertTrue(any(r["kind"] == "recovered_link" for r in pending))
        key = source["handle"] + "#0"
        decision = {"references": {key: {"target": target["handle"], "reference_sha256": digest(ref), "evidence": "Exact title and surname checked."}}}
        _, edges, _, pending, _ = build_edges(articles, records, decision)
        self.assertFalse(any(r["kind"] == "recovered_link" for r in pending))
        self.assertFalse(next(e for e in edges if e["source"] == source["handle"])["baseline"])
        decision["references"][key]["reference_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "reference changed"):
            build_edges(articles, records, decision)

    def test_exclusions_and_missing_deposits_are_explicit(self):
        a = article("alpha")
        self.assertFalse(eligibility({**a, "title": "Editorial Board"}, {})["include"])
        self.assertTrue(eligibility({**a, "title": "A theory of security: an introduction"}, {}).get("review_required"))
        articles, records = fixture()
        snapshot, _, _ = build_snapshot(articles, records, {}, None, "20261015T120000Z-abcd1234")
        self.assertEqual(sum(r["missing_reference_deposit"] for r in snapshot["coverage"]), 2)
        self.assertEqual(snapshot["metadata"]["isolated_count"], 1)

    def test_cached_collection_replays_without_network_and_checks_hash(self):
        a = article("alpha")
        with tempfile.TemporaryDirectory() as temporary:
            cached = record(a)
            write_json(cache_path(temporary, a["doi"]), cached)
            with patch("urllib.request.urlopen", side_effect=AssertionError("network access")):
                self.assertEqual(fetch_record(a["doi"], temporary), cached)
            cached["sha256"] = "invalid"
            write_json(cache_path(temporary, a["doi"]), cached)
            with self.assertRaisesRegex(ValueError, "Corrupt"):
                fetch_record(a["doi"], temporary)


class AnalysisTests(unittest.TestCase):
    def test_layout_is_reproducible_across_python_hash_seeds(self):
        articles = [article(name, page=str(i * 10)) for i, name in enumerate(["alpha", "beta", "gamma", "delta", "epsilon", "zeta"], 1)]
        records = {a["handle"]: record(a) for a in articles}
        for source, target in [(1, 0), (2, 1), (4, 3), (5, 4)]:
            records[articles[source]["handle"]] = record(articles[source], [{"DOI": articles[target]["doi"]}])
        script = ("import json; from app.citations import build_snapshot; "
                  f"articles,records=json.loads({json.dumps([articles, records])!r}); "
                  "snapshot,_,_=build_snapshot(articles,records,{},None,'20261015T120000Z-abcd1234'); "
                  "print(json.dumps(snapshot['nodes'],sort_keys=True))")
        outputs = [subprocess.check_output([sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed})
                   for seed in ["1", "77"]]
        self.assertEqual(outputs[0], outputs[1])

    def test_hand_checked_degrees_components_flows_and_determinism(self):
        articles, records = fixture()
        first, evidence, _ = build_snapshot(articles, records, {}, None, "20261015T120000Z-abcd1234")
        second, _, _ = build_snapshot(list(reversed(articles)), records, {}, None, "20261015T120001Z-abcd1234")
        self.assertEqual(first["nodes"], second["nodes"])
        self.assertEqual(first["edges"], second["edges"])
        self.assertEqual(first["communities"], second["communities"])
        self.assertEqual(first["metadata"]["edge_count"], 3)
        degrees = {n["title"].split()[0]: (n["in_degree"], n["out_degree"]) for n in first["nodes"]}
        self.assertEqual(degrees, {"alpha": (2, 0), "beta": (1, 1), "gamma": (0, 2), "isolated": (0, 0)})
        self.assertEqual(first["network"]["weak_components"], [3, 1])
        self.assertEqual(sum(r["count"] for r in first["flows"]), 3)
        self.assertEqual(len(evidence["edges"][0]["evidence"]), 2)
        corrupted = copy.deepcopy(first); corrupted["nodes"][0]["degree"] += 1
        with self.assertRaises(ValueError): validate_snapshot(corrupted)

    def test_reciprocal_projection_and_topic_denominators(self):
        articles, records = fixture()
        edges = [{"source": articles[0]["handle"], "target": articles[1]["handle"]}, {"source": articles[1]["handle"], "target": articles[0]["handle"]}]
        self.assertEqual(projection(graph_for(articles, edges))[articles[0]["handle"]][articles[1]["handle"]]["weight"], 2)
        topics = {"metadata": {"run_id": "topic-run", "corpus_fingerprint": corpus_fingerprint(articles)},
                  "topics": [{"id": 0, "label": "Growth"}], "documents": [{"handle": articles[0]["handle"], "topic": 0}, {"handle": articles[1]["handle"], "topic": -1}]}
        comparison = theme_comparison([{**a, "community": 0} for a in articles], topics, corpus_fingerprint(articles))
        self.assertEqual([c["count"] for c in comparison["rows"][0]["cells"]], [1, 1, 2])
        self.assertEqual(sum(c["share"] for c in comparison["rows"][0]["cells"]), 100)
        self.assertEqual(theme_comparison([], topics, "changed")["status"], "unavailable")




if __name__ == "__main__":
    unittest.main()
