"""Crossref collection and conservative, evidence-preserving reference matching."""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def normalize_doi(value):
    value = urllib.parse.unquote(html.unescape(str(value or ""))).strip()
    for _ in range(2):
        value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value, flags=re.I)
    return value.strip().lower()


def normalize(value):
    value = re.sub(r"<sup\b[^>]*>\s*\d+\s*</sup>", "", str(value or ""), flags=re.I | re.S)
    value = re.sub(r"</?sub\b[^>]*>", "", value, flags=re.I)
    value = html.unescape(re.sub(r"<[^>]+>", " ", value)).replace("&", " and ")
    value = re.sub(r"(\d+)\s+(st|nd|rd|th)\b", r"\1\2", value, flags=re.I)
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def surname(name):
    name = str(name or "").split(",", 1)[0]
    parts = normalize(name).split()
    while len(parts) > 1 and len(parts[-1]) == 1:
        parts.pop()
    return parts[-1] if parts else ""


def first_page(value):
    return re.split(r"[-–—]", str(value or ""))[0].strip().lower()


def journal_matches(value):
    return normalize(value) in {"defence and peace economics", "defense and peace economics",
                                 "defence economics", "defense economics"}


def cache_path(root, doi):
    return Path(root) / "crossref" / (hashlib.sha256(normalize_doi(doi).encode()).hexdigest() + ".json")


def fetch_record(doi, root, *, refresh=False, contact=""):
    doi = normalize_doi(doi)
    path = cache_path(root, doi)
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        if cached["status"] in {"ok", "not_found"}:
            if cached.get("sha256") != digest(cached.get("message")):
                raise ValueError(f"Corrupt Crossref cache for {doi}")
            return cached
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")
    if contact:
        url += "?" + urllib.parse.urlencode({"mailto": contact})
    request = urllib.request.Request(url, headers={"User-Agent": "DPE-Citation-Study/1.0" + (f" (mailto:{contact})" if contact else ""),
                                                   "Accept": "application/json"})
    result = {"doi": doi, "url": url, "retrieved_at": now(), "status": "error", "message": None}
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result.update(status="ok", http_status=response.status, message=json.load(response)["message"])
            break
        except urllib.error.HTTPError as error:
            result.update(http_status=error.code, error=str(error))
            if error.code == 404:
                result["status"] = "not_found"
                break
            if error.code not in {408, 429, 500, 502, 503, 504}:
                break
            wait = error.headers.get("Retry-After", "")
            time.sleep(min(float(wait) if wait.isdigit() else 2 ** attempt, 60))
        except (OSError, ValueError) as error:
            result["error"] = str(error)
            time.sleep(2 ** attempt)
    result["sha256"] = digest(result["message"])
    write_json(path, result)
    return result


def collect(articles, root, decisions, *, refresh=False, contact="", workers=3):
    dois = sorted({normalize_doi(decisions.get("identities", {}).get(a["handle"], {}).get("doi", a["doi"]))
                   for a in articles} - {""})
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_record, doi, root, refresh=refresh, contact=contact): doi for doi in dois}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result["status"] == "error":
                errors.append(result["doi"])
            if index % 50 == 0 or index == len(dois):
                print(f"Crossref {index}/{len(dois)}; transient failures: {len(errors)}", flush=True)
    if errors:
        raise RuntimeError(f"{len(errors)} Crossref requests failed; rerun collection to resume.")


def read_records(articles, root, decisions):
    records = {}
    for article in articles:
        doi = normalize_doi(decisions.get("identities", {}).get(article["handle"], {}).get("doi", article["doi"]))
        path = cache_path(root, doi)
        if not doi or not path.exists():
            records[article["handle"]] = {"status": "missing", "message": None, "doi": doi}
            continue
        record = json.loads(path.read_text())
        if record.get("sha256") != digest(record.get("message")):
            raise ValueError(f"Corrupt Crossref cache for {doi}")
        records[article["handle"]] = record
    return records


def identity_matches(article, message):
    if not message:
        return False
    titles = message.get("title", [])
    title_match = normalize(article["title"]) in {normalize(t) for t in titles}
    place_match = (str(message.get("volume", "")) == str(article["volume"]) and
                   first_page(message.get("page", "")) == first_page(article["pages"]) and
                   bool(first_page(article["pages"])))
    authors = {normalize(a.get("family", "")) for a in message.get("author", [])}
    author_match = bool({surname(a) for a in article["authors"]} & authors)
    return title_match and (place_match or author_match)


def eligibility(article, decisions):
    override = decisions.get("eligibility", {}).get(article["handle"])
    if override:
        return override
    title = normalize(article["title"])
    if (title in {"introduction", "book review", "editorial board", "erratum", "corrigendum", "foreword", "list of reviewers"}
            or title.startswith(("editorial", "introduction to ", "book review of "))
            or "introduction to the special issue" in title):
        return {"include": False, "reason": "Editorial, issue introduction, routine book notice or correction (title rule)."}
    if "introduction" in title:
        return {"include": True, "reason": "Ambiguous introduction requires individual review.", "review_required": True}
    return {"include": True, "reason": "Research article or substantive review; no administrative title marker."}


def match_reference(ref, articles, doi_index):
    """Return only unique exact matches; never make a fuzzy identity decision."""
    doi = normalize_doi(ref.get("DOI"))
    if doi in doi_index:
        candidates = doi_index[doi]
        if len(candidates) == 1:
            return candidates[0], "doi", []
        return None, "ambiguous_doi", candidates
    author = surname(ref.get("author", ""))
    candidates = []
    if author and journal_matches(ref.get("journal-title")) and ref.get("volume") and ref.get("first-page"):
        candidates = [a["handle"] for a in articles if str(a["volume"]) == str(ref["volume"])
                      and first_page(a["pages"]) == first_page(ref["first-page"])
                      and author in {surname(x) for x in a["authors"]}]
        if len(candidates) == 1:
            return candidates[0], "bibliographic", []
    title = normalize(ref.get("article-title", ""))
    if author and title:
        by_title = [a["handle"] for a in articles if normalize(a["title"]) == title
                    and author in {surname(x) for x in a["authors"]}]
        if len(by_title) == 1 and (not candidates or candidates == by_title):
            return by_title[0], "title_author", []
        candidates = sorted(set(candidates + by_title))
    if candidates:
        return None, "ambiguous", candidates
    if journal_matches(ref.get("journal-title")) and str(ref.get("volume", "")).isdigit():
        volumes = [int(a["volume"]) for a in articles if str(a["volume"]).isdigit()]
        if volumes and (int(ref["volume"]) < min(volumes) or int(ref["volume"]) > max(volumes)):
            return None, "stated_outside_catalogue", []
    text = normalize(ref.get("unstructured", ""))
    if journal_matches(ref.get("journal-title")) or any(j in text for j in ["defence and peace economics", "defense and peace economics"]):
        return None, "unresolved_internal_candidate", []
    return None, "outside_or_unresolved", []


def build_edges(articles, records, decisions):
    eligible, audit, review = [], [], []
    doi_index = {}
    for a in articles:
        record = records[a["handle"]]
        decision = eligibility(a, decisions)
        identity = identity_matches(a, record.get("message"))
        override = decisions.get("identities", {}).get(a["handle"], {})
        if override.get("verified") and override.get("evidence") and record.get("message"):
            identity = True
        row = {"handle": a["handle"], "title": a["title"], "year": a["year"], "doi": record["doi"],
               "eligible": decision["include"], "eligibility": decision,
               "identity_verified": identity, "fetch_status": record["status"],
               "retrieved_at": record.get("retrieved_at"), "response_sha256": record.get("sha256"),
               "reference_count": len((record.get("message") or {}).get("reference", [])),
               "deposited_reference_count": (record.get("message") or {}).get("reference-count"),
               "identity_evidence": override.get("evidence")}
        audit.append(row)
        if decision.get("review_required"):
            review.append({"kind": "eligibility", **row})
        if not decision["include"]:
            continue
        eligible.append({**a, "citation_doi": record["doi"], "identity_verified": identity})
        if identity and record["doi"]:
            doi_index.setdefault(record["doi"], []).append(a["handle"])
        if not identity and not (override.get("accepted_unresolved") and override.get("evidence")):
            review.append({"kind": "identity", **row, "crossref_title": (record.get("message") or {}).get("title")})
    edges, unmatched = {}, []
    eligible_handles = {a["handle"] for a in eligible}
    for a in eligible:
        if not a["identity_verified"]:
            continue  # A mismatched DOI must never supply another paper's references.
        record = records[a["handle"]]
        for index, ref in enumerate(record["message"].get("reference", [])):
            reference_id = f"{a['handle']}#{index}"
            target, method, candidates = match_reference(ref, eligible, doi_index)
            override = decisions.get("references", {}).get(reference_id)
            if override:
                if override.get("reference_sha256") != digest(ref):
                    raise ValueError(f"Reviewed reference changed: {reference_id}")
                if not override.get("evidence"):
                    raise ValueError(f"Missing reference review evidence: {reference_id}")
                if override.get("target") is not None and override["target"] not in eligible_handles:
                    raise ValueError("Reviewed target is not an eligible article")
                target, method = override.get("target"), "reviewed"
            evidence = {"reference_id": reference_id, "reference": ref, "method": method,
                        "retrieved_at": record.get("retrieved_at"), "response_sha256": record.get("sha256")}
            if override:
                evidence["review"] = override
            if target and target != a["handle"]:
                if method != "doi" and not override:
                    review.append({"kind": "recovered_link", "source": a["handle"], "target": target, **evidence})
                edge = edges.setdefault((a["handle"], target), {"source": a["handle"], "target": target, "baseline": False, "evidence": []})
                edge["baseline"] |= method == "doi"
                edge["evidence"].append(evidence)
            elif not target:
                unmatched.append({"source": a["handle"], "status": method, "candidates": candidates, **evidence})
                if method in {"ambiguous", "ambiguous_doi", "unresolved_internal_candidate"} and not override:
                    review.append({"kind": "unresolved_reference", **unmatched[-1]})
    return eligible, sorted(edges.values(), key=lambda e: (e["source"], e["target"])), audit, review, unmatched
