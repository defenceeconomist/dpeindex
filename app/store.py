"""Pure catalogue and search-text helpers; no services."""
import re

def embed_text(article: dict) -> str:
    return "\n".join(filter(None, [
        article["title"], "Authors: " + ", ".join(article["authors"]),
        article["abstract"], "Keywords: " + article["keywords"] if article["keywords"] else "",
    ]))


def _sort_number(value: str) -> int:
    match = re.match(r"\d+", value)
    return int(match.group()) if match else -1


def catalog_for(articles: list[dict]) -> dict:
    volumes: dict[str, dict] = {}
    summaries = []
    for article in articles:
        summary = {key: article[key] for key in (
            "handle", "title", "authors", "volume", "issue", "year", "pages", "doi",
        )}
        summaries.append(summary)
        volume = volumes.setdefault(article["volume"], {
            "number": article["volume"], "years": set(), "count": 0, "issues": {},
        })
        volume["years"].add(article["year"])
        volume["count"] += 1
        issue = volume["issues"].setdefault(article["issue"], {
            "number": article["issue"], "count": 0,
        })
        issue["count"] += 1
    result = []
    for volume in sorted(volumes.values(), key=lambda value: _sort_number(value["number"]), reverse=True):
        volume["years"] = sorted(volume["years"])
        volume["issues"] = sorted(volume["issues"].values(), key=lambda value: _sort_number(value["number"]))
        result.append(volume)
    summaries.sort(key=lambda a: (
        -_sort_number(a["volume"]), _sort_number(a["issue"]),
        _sort_number(a["pages"].split("-")[0]), a["title"],
    ))
    return {"volumes": result, "articles": summaries, "count": len(articles)}
