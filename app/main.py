from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .store import load_article, load_catalog, redis_client, semantic_search

SITE = Path(__file__).resolve().parent.parent / "site"
app = FastAPI(title="Defence and Peace Economics Index", docs_url=None, redoc_url=None)


@app.get("/api/health")
def health():
    try:
        redis_client().ping()
        count = load_catalog()["count"]
        return {"status": "ok", "articles": count}
    except Exception as error:
        raise HTTPException(503, f"Catalog unavailable: {error}") from error


@app.get("/api/catalog")
def catalog():
    return load_catalog()


@app.get("/api/source")
def source():
    raw = redis_client().get("dpe:source")
    return json.loads(raw) if raw else {}


@app.get("/api/article")
def article(handle: str = Query(min_length=10, max_length=200)):
    result = load_article(handle)
    if result is None:
        raise HTTPException(404, "Article not found")
    return result


@app.get("/api/search")
def search(q: str = Query(min_length=2, max_length=250)):
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(400, "Enter at least two characters")
    semantic = semantic_search(query, 40)
    by_handle = {item["handle"]: item for item in semantic}
    # Add exact title and author matches, which dense retrieval can miss.
    terms = re.findall(r"[\w’-]+", query.casefold())
    for summary in load_catalog()["articles"]:
        haystack = (summary["title"] + " " + " ".join(summary["authors"])).casefold()
        if terms and all(term in haystack for term in terms):
            if summary["handle"] not in by_handle:
                full = load_article(summary["handle"])
                if full:
                    by_handle[summary["handle"]] = {**full, "score": 0}
    values = list(by_handle.values())
    values.sort(key=lambda item: (
        -int(query.casefold() in " ".join(item["authors"]).casefold()),
        -int(query.casefold() in item["title"].casefold()),
        -item["score"],
    ))
    return {"query": query, "results": values[:40], "total": len(values)}


@app.get("/styles.css")
def styles():
    return FileResponse(SITE / "styles.css", media_type="text/css")


@app.get("/app.js")
def javascript():
    return FileResponse(SITE / "app.js", media_type="text/javascript")


@app.get("/")
def index():
    return FileResponse(SITE / "index.html", media_type="text/html")
