"""Refresh the publisher ReDIF file and its Redis/Qdrant index."""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

from .redif import parse_file
from .store import sync_articles

SOURCE = "ftp://ftp.repec.org/opt/ReDIF/RePEc/taf/defpea/defpea.redif"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="Fetch the current ReDIF file from RePEc FTP")
    parser.add_argument("--file", type=Path, default=Path("data/defpea.redif"))
    args = parser.parse_args()
    if args.fetch:
        with urllib.request.urlopen(SOURCE, timeout=90) as response:
            raw = response.read()
        if b"Template-Type: ReDIF-Article" not in raw or len(raw) < 100_000:
            raise RuntimeError("Source did not contain the expected ReDIF article data")
        args.file.parent.mkdir(parents=True, exist_ok=True)
        args.file.write_bytes(raw)
    articles = parse_file(args.file)
    if len(articles) < 1000:
        raise RuntimeError(f"Expected at least 1000 articles; parsed {len(articles)}")
    print(sync_articles(articles, source_url=SOURCE), flush=True)


if __name__ == "__main__":
    main()
