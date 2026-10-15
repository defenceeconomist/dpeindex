from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse

from .store import issue_from_catalog, load_article, load_catalog, redis_client, semantic_search
from .topic_store import load_topics
from .retrospectives import load_comparison
from .theme_filter import selection_handles, theme_options
from .topic_timeline import load_timeline

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
    data = load_catalog()
    return {"count": data["count"], "volumes": data["volumes"]}


@app.get("/api/issue")
def issue(volume: str = Query(min_length=1, max_length=10),
          number: str = Query(min_length=1, max_length=10),
          theme: int | None = Query(default=None, ge=-1), run: str | None = Query(default=None, max_length=80),
          year_from: int | None = Query(default=None, ge=1500, le=2500),
          year_to: int | None = Query(default=None, ge=1500, le=2500)):
    catalog = load_catalog()
    allowed = selection_handles(catalog, theme, run, year_from, year_to)
    articles = issue_from_catalog(catalog, volume, number)
    if articles is None:
        raise HTTPException(404, "Issue not found")
    if allowed is not None:
        articles = [article for article in articles if article["handle"] in allowed]
    return {"volume": volume, "issue": number, "articles": articles}


@app.get("/api/themes")
def themes():
    return theme_options(load_topics(), load_catalog())


@app.get("/api/source")
def source():
    raw = redis_client().get("dpe:source")
    return json.loads(raw) if raw else {}


@app.get("/api/topics")
def topics():
    try:
        data = load_topics()
        if data.get("status") == "ready":
            data["comparison"] = load_comparison(data)
            data["timeline"] = load_timeline(data)
        return data
    except Exception as error:
        raise HTTPException(503, "The topic explorer is temporarily unavailable.") from error


@app.get("/api/article")
def article(handle: str = Query(min_length=10, max_length=200)):
    result = load_article(handle)
    if result is None:
        raise HTTPException(404, "Article not found")
    return result


@app.get("/api/search")
def search(q: str = Query(min_length=2, max_length=250),
           theme: int | None = Query(default=None, ge=-1), run: str | None = Query(default=None, max_length=80),
           year_from: int | None = Query(default=None, ge=1500, le=2500),
           year_to: int | None = Query(default=None, ge=1500, le=2500)):
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(400, "Enter at least two characters")
    catalog = load_catalog()
    allowed = selection_handles(catalog, theme, run, year_from, year_to)
    semantic = semantic_search(query, 40, allowed_handles=allowed)
    by_handle = {item["handle"]: item for item in semantic}
    # Add exact title and author matches, which dense retrieval can miss.
    terms = re.findall(r"[\w’-]+", query.casefold())
    for summary in catalog["articles"]:
        if allowed is not None and summary["handle"] not in allowed:
            continue
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
    summary_fields = ("handle", "title", "authors", "volume", "issue", "year", "pages", "doi")
    results = [{key: item[key] for key in summary_fields} for item in values[:40]]
    return {"query": query, "results": results, "total": len(values)}


@app.get("/styles.css")
def styles():
    return FileResponse(SITE / "styles.css", media_type="text/css")


@app.get("/scroll.css")
def scroll_styles():
    return FileResponse(SITE / "scroll.css", media_type="text/css")


@app.get("/app.js")
def javascript():
    return FileResponse(SITE / "app.js", media_type="text/javascript")


@app.get("/article-ui.js")
def article_ui():
    return FileResponse(SITE / "article-ui.js", media_type="text/javascript")


@app.get("/topics/")
def topic_page():
    return FileResponse(SITE / "topics.html", media_type="text/html")


@app.get("/topics")
def topic_page_redirect():
    # A relative redirect retains Caddy's /dpe/ prefix as well as standalone URLs.
    return RedirectResponse("topics/", status_code=308)


@app.get("/topics.js")
def topic_javascript():
    return FileResponse(SITE / "topics.js", media_type="text/javascript")


@app.get("/retrospectives.js")
def retrospective_javascript():
    return FileResponse(SITE / "retrospectives.js", media_type="text/javascript")


@app.get("/topic-timeline.js")
def timeline_javascript():
    return FileResponse(SITE / "topic-timeline.js", media_type="text/javascript")


@app.get("/topics.css")
def topic_styles():
    return FileResponse(SITE / "topics.css", media_type="text/css")


@app.get("/vendor/plotly.min.js")
def plotly_javascript():
    return FileResponse(SITE.parent / "vendor" / "plotly.min.js", media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/")
def index():
    return FileResponse(SITE / "index.html", media_type="text/html")
