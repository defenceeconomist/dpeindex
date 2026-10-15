"""Read RePEc's ReDIF-Article records without scraping an HTML service."""

from __future__ import annotations

import html
import re
from pathlib import Path

FIELDS = {
    "template-type", "author-name", "title", "abstract", "journal", "pages",
    "issue", "volume", "year", "month", "keywords", "x-doi", "file-url",
    "file-format", "file-restriction", "handle",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_redif(source: str) -> list[dict]:
    articles: list[dict] = []
    record: dict[str, list[str]] = {}
    field: str | None = None

    def finish() -> None:
        if not record:
            return
        one = lambda key: _clean(" ".join(record.get(key, [])))
        handle = one("handle")
        title = one("title")
        if not handle or not title:
            return
        doi = one("x-doi")
        articles.append({
            "handle": handle,
            "title": title,
            "authors": [_clean(name) for name in record.get("author-name", [])],
            "abstract": one("abstract"),
            "journal": one("journal"),
            "volume": one("volume"),
            "issue": one("issue"),
            "year": one("year"),
            "month": one("month"),
            "pages": one("pages"),
            "keywords": one("keywords"),
            "doi": doi,
            "doi_url": f"https://doi.org/{doi}" if doi else "",
            "file_url": one("file-url"),
            "file_format": one("file-format"),
            "file_restriction": one("file-restriction"),
            "repec_url": "https://ideas.repec.org/a/taf/defpea/" + "".join(handle.split("defpea:", 1)[-1].split(":")) + ".html",
        })

    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z][A-Za-z-]*):\s*(.*)$", line)
        if match and match.group(1).lower() in FIELDS:
            key, value = match.group(1).lower(), match.group(2)
            if key == "template-type":
                finish()
                record = {}
            if key == "author-name":
                record.setdefault(key, []).append(value)
            else:
                record.setdefault(key, []).append(value)
            field = key
        elif match:
            field = None
        elif field and record.get(field):
            record[field][-1] += " " + line
        elif field:
            record.setdefault(field, []).append(line)
    finish()
    return articles


def parse_file(path: Path) -> list[dict]:
    return parse_redif(path.read_text(encoding="utf-8-sig"))
