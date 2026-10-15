"""Static theme metadata."""
def theme_options(snapshot, catalog):
    if snapshot.get("status") != "ready":
        return {"status": "not_built", "topics": [], "articles": []}
    assignments = {item["handle"]: item["topic"] for item in snapshot["documents"]}
    articles = [{**{key: article[key] for key in ("handle", "volume", "issue", "year")},
                 "topic": assignments.get(article["handle"])} for article in catalog["articles"]]
    return {"status": "ready", "run_id": snapshot["metadata"]["run_id"],
            "model_id": snapshot["metadata"].get("model_id", snapshot["metadata"]["run_id"]),
            "built_at": snapshot["metadata"]["built_at"], "stale": snapshot["stale"],
            "topics": [{"id": item["id"], "label": item["label"],
                        "count": sum(article["topic"] == item["id"] for article in articles)} for item in snapshot["topics"]],
            "articles": articles}
