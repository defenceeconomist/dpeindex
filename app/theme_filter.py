"""Run-scoped article filters shared by issue browsing and semantic search."""
from fastapi import HTTPException

from .topic_store import load_topics


def selection_handles(catalog, theme=None, run=None, year_from=None, year_to=None, snapshot=None):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "The first year must not exceed the last year.")
    if theme is None and year_from is None and year_to is None:
        return None
    assignments = None
    if theme is not None:
        snapshot = snapshot if snapshot is not None else load_topics()
        if snapshot.get("status") != "ready":
            raise HTTPException(409, "Themes are not available until the first topic model is built.")
        if run != snapshot["metadata"]["run_id"]:
            raise HTTPException(409, "The topic model has changed. Reload the page to use its current themes.")
        if theme not in {item["id"] for item in snapshot["topics"]} | {-1}:
            raise HTTPException(404, "Theme not found in this model.")
        assignments = {item["handle"] for item in snapshot["documents"] if item["topic"] == theme}
    return {article["handle"] for article in catalog["articles"]
            if (assignments is None or article["handle"] in assignments)
            and (year_from is None or str(article["year"]).isdigit() and int(article["year"]) >= year_from)
            and (year_to is None or str(article["year"]).isdigit() and int(article["year"]) <= year_to)}


def theme_options(snapshot, catalog):
    if snapshot.get("status") != "ready":
        return {"status": "not_built", "topics": [], "articles": []}
    assignments = {item["handle"]: item["topic"] for item in snapshot["documents"]}
    articles = [{**{key: article[key] for key in ("handle", "volume", "issue", "year")},
                 "topic": assignments.get(article["handle"])} for article in catalog["articles"]]
    return {"status": "ready", "run_id": snapshot["metadata"]["run_id"],
            "built_at": snapshot["metadata"]["built_at"], "stale": snapshot["stale"],
            "topics": [{"id": item["id"], "label": item["label"],
                        "count": sum(article["topic"] == item["id"] for article in articles)} for item in snapshot["topics"]],
            "articles": articles}
