# Defence and Peace Economics article index

Mobile and tablet friendly article browser for the RePEc `taf/defpea` archive. It scrolls continuously from the newest issue through older volumes, expands abstracts and bibliographic details, and searches titles and abstracts using local BGE embeddings in the shared Qdrant service. Issue article lists load near the viewport and are removed from the browser when far away. Redis DB 4 stores the canonical article records and catalog under the `dpe:` prefix. The Qdrant collection is `dpe_articles`.

The browser receives volume and issue counts from `/api/catalog`, fetches summaries one issue at a time from `/api/issue?volume=37&number=6`, and fetches a full article only when expanded. The volume picker jumps to that volume's newest issue; `?volume=37&issue=6` links to a specific issue. Search continues to use `/api/search` and returns summaries, with full details fetched on expansion.

Source: `ftp://ftp.repec.org/opt/ReDIF/RePEc/taf/defpea/defpea.redif`. This uses the ReDIF metadata file described by [RePEc's data guide](https://ideas.repec.org/getdata.html); it does not scrape article pages. The source file is included at `data/defpea.redif` so the first build is reproducible. RePEc asks commercial users to contact them before using its data commercially.

## Run

Start the existing shared services first, then build and start this app:

```sh
cd ../../shared-datastores
docker compose up -d redis qdrant
cd ../apps/dpe
docker compose up -d --build
docker compose exec dpe-index python -m app.sync
```

The app is available at `http://localhost:8789/` and through the Mac mini portal at `/dpe/` once Caddy is restarted. The first index build downloads the local BGE model and embeds 1,221 articles; later runs only embed articles whose title, authors, abstract, or keywords changed.

Refresh from the RePEc FTP source with:

```sh
docker compose exec dpe-index python -m app.sync --fetch
```

The Docker image includes the initial ReDIF file. For a persistent local copy when refreshing, fetch it on the host and rebuild the image before running `app.sync`:

```sh
curl -fsS 'ftp://ftp.repec.org/opt/ReDIF/RePEc/taf/defpea/defpea.redif' -o data/defpea.redif
docker compose up -d --build
docker compose exec dpe-index python -m app.sync
```

Run the parser and grouping checks with `docker compose exec dpe-index python -m unittest discover -s tests`.
