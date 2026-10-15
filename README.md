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

## Topic explorer

The **Articles / Topics** navigation opens a linked abstract map, topic hierarchy and dynamic time charts at `http://localhost:8789/topics/` (or `/dpe/topics/` through the portal). Select a topic, hierarchy branch or map point to highlight its papers. Paper links open the complete-abstract reader on **Articles**, which also provides theme and publication-year filters. Papers without abstracts remain available under **All themes**; ambiguous abstracts have an explicit **Unassigned** option and grey map points.

Topic modelling is an explicit offline operation. It uses local `Qwen/Qwen3-Embedding-0.6B` through Sentence Transformers, then BERTopic, UMAP and HDBSCAN. It makes **no GPT or hosted inference API calls**. The initial model download comes from Hugging Face. The web image serves Plotly locally and does not install or load the modelling stack.

Build the optional training image and run the full parameter search:

```sh
docker compose --profile topics build dpe-topics
docker compose --profile topics run --rm dpe-topics --tune
```

This compares 54 configurations, favours 10–20 broad topics, and validates three finalists with seeds 42, 7 and 21. Ranking uses normalised coherence and original-embedding cosine silhouette with weight 2 each, and keyword diversity and coverage with weight 1 each. The baseline uses default clustering settings with the same complete embeddings and keyword processing. Coherence is document-level NPMI of the top ten topic terms. The mean validation score selects the winner; adjusted mutual information breaks ties.

Every abstract is tokenised without truncation, including special tokens, before encoding. Over-limit input fails with the affected article handles. Embeddings are normalised and cached by full abstract text, model name and immutable model revision; a fully cached rebuild does not load the encoder weights. English stopword filtering applies only to keyword extraction. Outlier reassignment requires a calibrated similarity threshold and a margin over the second-best topic. Calibration requires at least 95% agreement on held-out confident cluster members; this is agreement with discovered clusters, not independently verified accuracy.

To refit the last successfully published settings and model revision:

```sh
docker compose --profile topics run --rm dpe-topics --rebuild
```

To review before publication:

```sh
docker compose --profile topics run --rm dpe-topics --tune --no-publish
docker compose --profile topics run --rm dpe-topics --publish-run RUN_ID
```

The `dpe-topic-artifacts` volume retains `runs/RUN_ID/report.json`, `trials.json`, per-article `token-coverage.json`, `snapshot.json`, the BERTopic model, cached embeddings and `selected.json`. Reports include representative article titles and full abstracts for review, original and final quality metrics, baseline results, outlier calibration, parameters, seeds and library versions. The separate `dpe-model-cache` volume retains downloaded model files.

Training reads a consistent snapshot of canonical Redis articles. Complete, validated runs publish atomically to `dpe:topics:current` and `dpe:topics:run:RUN_ID` in Redis DB 4. A renewable build lease prevents concurrent publication. Failed or interrupted runs leave the previous result intact. Catalog sync records a corpus fingerprint; `/api/topics` returns `stale: true` when the saved model differs from the current abstracts or displayed metadata. Refreshes stay manual. Topic IDs are scoped to a run.

Docker uses CPU inference. On Apple Silicon, the same command module also supports native GPU inference with Python 3.12, the packages in `requirements-topics.txt`, plus the base FastAPI/Redis dependencies:

```sh
HF_HOME=/path/to/model-cache TOPIC_ARTIFACTS_DIR=/path/to/artifacts \
  python -m app.topics --tune --device mps --batch-size 8
```

The initial GPU run for this catalog used all 1,152 abstracts; the longest was 414 tokens against a 32,768-token limit. The 69 articles with missing abstracts were excluded. Corpus counts will change with future syncs.

Full Python checks, including the final keyword-processing regression, run in the modelling image:

```sh
docker compose --profile topics run --rm --entrypoint python dpe-topics -m unittest discover -s tests -v
```

DOM interaction checks exercise topic/branch/map selection, temporal charts, the main-page abstract reader, theme and year filtering, stale results and API failures. They use a temporary dependency outside the project:

```sh
npm install --prefix /tmp/dpe-ui-tests --no-audit --no-fund linkedom
node --test tests/*ui.test.mjs
```

## Themes over time and article filters

The Topics page uses [BERTopic topics over time](https://maartengr.github.io/BERTopic/getting_started/topicsovertime/topicsovertime.html) to show changing prevalence and period-specific keywords while retaining the published topic identities and final assignments. It includes line charts and a heatmap, annual/five-year/decade intervals, count/share measures, linked theme highlighting, an accessible period table and links to matching articles. Calendar bins include all available publication years; they have different boundaries from the retrospective comparison below. Early catalog coverage and partial end periods are identified. Share denominators include unassigned abstracts; missing abstracts are counted separately. Periods without abstracts appear as gaps, rather than zero prevalence.

Temporal analysis is offline. It restores the published assignment-based c-TF-IDF representation from complete canonical abstracts, checks its keyword signature against the published model, then runs `topics_over_time` separately for each interval width. `global_tuning=True` stabilises period keywords against the pooled topic representation; `evolution_tuning=False` keeps periods independent of previous period representations. Smoothed words may therefore originate outside the selected period. Counts and assignments are never smoothed. The operation loads no sentence-transformer weights, fits no clusters and calls no inference API.

Build or refresh the temporal analysis for the current model without replacing it:

```sh
docker compose --profile topics build dpe-topics
docker compose --profile topics run --rm dpe-topics --timeline
```

The command requires a canonical corpus fingerprint matching the published model, writes `runs/RUN_ID/timeline.json` in the artifact volume, and publishes under `dpe:topics:timeline:RUN_ID` only if that model is still active. A failed analysis keeps the existing timeline. New `--tune` and `--rebuild` runs include the timeline in their atomic model snapshot. `/api/topics` returns it as `timeline`; missing or invalid analysis does not remove the existing map or hierarchy. Undated abstracts are reported separately.

`GET /api/themes` supplies current theme labels and compact article assignments for the Articles filters. `/api/issue` and `/api/search` accept `theme`, `run`, `year_from` and `year_to`. A theme requires its model run ID; old-run requests return 409 so topic IDs cannot silently change meaning. Semantic filtering uses Qdrant point-ID conditions before ranking and limiting results, and exact title/author matches use the same filter. Main-page URLs retain filters; `article=HANDLE` plus volume/issue opens that paper’s complete abstract. Old model links reset the theme with an explanation. Theme availability failures leave normal issue browsing and search usable.

## Retrospective comparison

The Topics page also compares the fixed published themes with the three original retrospective papers in `docs/`. The comparison does not train or publish a model. `/api/topics` adds a `comparison` payload with source-table counts, coverage, model counts, separate percentage denominators, category definitions, overlapping crosswalks and period-specific example papers.

`data/retrospectives.json` records the original sources, DOI references, table/page locations and file hashes. It binds the reviewed crosswalk to run `20261015T112751Z-f48f8afb`, its corpus fingerprint and ordered topic keywords. The source PDFs and EPUB are not served by the site. Editorial counts preserve the original tables: 207 papers in Hartley & Sandler (2000), 331 in Arce & Kollias (2010), and 389 in Gaibulloev, Kollias & Solomon (2020). The original first retrospective lists five arms-trade papers; the 2020 historical summary lists six and totals 208. The page reports this discrepancy rather than silently harmonising it. Separately listed book reviews do not enter editorial percentage denominators.

`app/retrospectives.py` calculates model counts from the audited published snapshot, using publication volumes and issues, including combined issue ranges. The first source's cutoff is volume 10, issue 2, so issues 3–4 are excluded. Available first-period records cover only volumes 8–9 and 10(1–2): 45 abstracts, three missing abstracts and seven unassigned. Volumes 11–20 contain 330 abstracts, 31 missing and 26 unassigned; volumes 21–30 contain 381 abstracts, 26 missing and 23 unassigned. Volumes 31 onward are excluded. Frozen missing-abstract identities preserve the audited coverage after catalog changes. Every model share uses all usable abstracts, including unassigned ones. Editorial and model populations differ; matching counts do not measure classification accuracy.

Crosswalk relationships distinguish close, broader, narrower, partial and unresolved matches. Definitions paraphrase the editorial scope; the interpretation notes explain where model clusters combine or split it. Topic counts can recur in multiple editorial rows and are never summed as exclusive matches. Review used the full abstracts of the two nearest confident members to each period-specific topic centroid (one example for the second period's one-paper geopolitical-risk topic). The examples include the Spain and UK country surveys in the industry cluster, conversion in Russian industry, equipment-cost and national-defence cost studies, debt research, alliance burden sharing, civil conflict and terrorism. This checks interpretation of topic scope, not editorial article-level assignments. The first period's broad conflict examples also illustrate the limits of interpreting a pooled cluster from a tiny early sample.

When a different model becomes active, the API reads `dpe:topics:run:20261015T112751Z-f48f8afb` to retain the dated comparison, returns `review_required`, and disables links from the old crosswalk to the new explorer. The new model remains usable. If the archive or evidence fails validation, only the comparison becomes unavailable. Catalog freshness is still reported separately by the existing topic API. A failed rebuild leaves the last successful model and its comparison intact.

After a future rebuild, updating this comparison requires an explicit review: retain the old archive, audit the new snapshot and period coverage, read representative abstracts, revise the crosswalk and narrative as necessary, then update the binding and regression expectations together. Rebuilding a model alone never updates the scholarly interpretation automatically.

The Python suite tests period boundaries, denominators, source transcription, overlapping mappings, model binding and archive failure states. The published-run regression also checks the exact 45/330/381 abstract totals and all topic counts when its snapshot is available. To run that regression from an exported immutable snapshot:

```sh
DPE_AUDITED_SNAPSHOT=/path/to/snapshot.json python -m unittest discover -s tests -v
```

DOM checks cover period selection, source citations, complete-abstract links and disabled links after a model change. `DPE_TOPIC_SNAPSHOT=/path/to/api-topics.json node --test tests/*ui.test.mjs` additionally exercises the full published API payload, including its comparison, through the linked explorer. Both standalone and portal routes use relative asset and API URLs.

## Citation conversations

The **Citations** page at `/citations/` (or `/dpe/citations/` through the portal) maps directed citations between eligible articles in the existing 1997–2026 RePEc catalogue. It adds an independently discovered citation-community view alongside the abstract-based Topics page. Scopus, hosted inference and topic-model retraining are not required. Reference deposits come from the public Crossref API.

The initial audited analysis contains **1,143 eligible papers, 3,097 directed links, 36 communities and 188 papers with no observed internal links**. The DOI-only baseline has 3,093 links. The 78 exclusions include issue introductions, editorials, administrative notices, corrections and routine book notices; research comments, replies and substantive review articles remain eligible. Community labels are provisional descriptions based on the leading titles and available abstracts. Some communities contain only two papers and should not be read as established intellectual conversations. Sixty eligible papers have no deposited reference list. The methods report and reference review queue expose the remaining gaps.

The page focuses on the directed network and a top-20 table with separate rankings for **citations in, citations out and citations in + out**. Rankings use the whole recovered study and remain unchanged by map filters. Community buttons filter the map; minimum-citation and From/To year sliders sit below it with live count/year labels. The map starts with at least **3 citations in** from other eligible DPE papers: 351 papers and 1,193 links in the initial study. The count selector also supports outgoing or combined counts; set the minimum to 0 to remove the count limit. Reset restores all years and the default of 3 incoming citations. Selecting a paper hidden by the filters reveals it by resetting the community/year limits and minimum count; selecting papers with no observed links also sets the minimum to 0. Every visible edge has a directional arrow from citing to cited paper; selecting a paper highlights incoming and outgoing links. Counts above the map and in the rankings refer to the whole recovered network; the visible map count reflects filters and the selected edge source. Switching to DOI-only edges retains the recovered citation counts, community assignments and coordinates. Article links open the existing Articles reader. Citation totals refer only to this study's eligible article universe, not global citations or the complete historical journal. Temporal prevalence, the frozen BERTopic comparison, methods, coverage and downloads remain available in the published API and run artifacts.

The explorer caches node display data and arrow geometry when the snapshot loads. Edges and arrowheads share WebGL line batches, and all communities share one marker trace with per-paper colours. Five stable traces replace per-community traces and per-edge SVG annotations. Slider labels update immediately; animation-frame scheduling keeps only the latest pending view and never overlaps Plotly redraws. Filtering retains the published coordinates, community colours and zoom state.

Build the optional, CPU-only citation image and collect references:

```sh
docker compose --profile citations build dpe-citations
docker compose --profile citations run --rm dpe-citations --collect
docker compose --profile citations run --rm dpe-citations --build
```

`CROSSREF_MAILTO` optionally supplies a contact address for Crossref's polite pool. Collection uses three workers, respects `Retry-After`, retries transient failures, and resumes from verified cache entries. `--refresh` explicitly refreshes the cache. Neither building from cache nor serving requests calls Crossref. The web image contains no NetworkX or Matplotlib dependency.

`data/citation-decisions.json` records the reviewed DOI corrections, eligibility decisions, recovered references, year-stratified DOI audit and community interpretations. Three catalogue records incorrectly share the Greece debt paper's DOI; their actual DOIs have suffixes **a**, **c** and **d**. Citation-specific corrections preserve RePEc handles and leave the canonical article/topic records intact. Crossref title normalization handles HTML, footnotes, subscripts and ordinal suffixes; unresolved identities never supply another paper's references.

The recovered graph accepts normalized DOI links first. Other automatic candidates require a unique exact journal/volume/first-page/author match or exact title/author match. Every accepted recovery is individually reviewed and bound to the deposited reference hash. Ambiguous or incomplete internal candidates remain in `review.json` and create no edges. References whose stated journal volume precedes catalogue coverage are reported separately. A missing reference deposit means data are unavailable, rather than evidence of a genuinely empty bibliography.

`--build` always creates a draft. Inspect `runs/RUN_ID/review.json`, resolve required identity/eligibility/recovered-link reviews in the decisions file, and add the DOI sample audit before rebuilding. When decisions change, rebuild the citation image to include them. A fresh build is also required when the underlying reference hash changes. Interpretations are bound to exact membership hashes, so a different partition cannot inherit old labels silently.

Publish a reviewed run with:

```sh
docker compose --profile citations run --rm dpe-citations --publish-run RUN_ID
```

Publication validates nodes, edges, degrees, flows, coverage, review completion and export hashes, checks the live catalogue fingerprint, and atomically writes an immutable archive plus `dpe:citations:current` in Redis DB 4. A renewable build lease prevents concurrent operations. Failed collection, analysis or publication keeps the previous published run. Unresolved reference candidates can remain in the queue; accepted recovered edges, identity decisions and eligibility must pass review. Public reads use only Redis. A changed catalogue marks the study stale; a changed topic run separately marks the frozen comparison. Neither event silently changes community or topic IDs.

Each artifact run retains `corpus.json`, `records.json`, `topics.json`, `decisions.json`, `snapshot.json`, reference-level evidence, coverage and review files, node/edge CSVs, directed GEXF, SVG/PNG figures and a standalone HTML methods report. `bundle.zip` includes the frozen inputs. The raw records allow exact input replay after the shared Crossref cache changes:

```sh
python -m app.citations --build \
  --corpus /path/to/run/corpus.json --records /path/to/run/records.json \
  --topics /path/to/run/topics.json --decisions /path/to/run/decisions.json
```

Native execution uses `requirements-citations.txt` plus the base Redis dependencies; the artifact directory defaults to `data/citation-artifacts/`, or `CITATION_ARTIFACTS_DIR`. A local Redis connection is required for the build lease. Docker retains artifacts in `dpe-citation-artifacts`. API endpoints are `GET /api/citations` and `GET /api/citations/RUN_ID/download/FILENAME`; immutable downloads remain tied to their published run.

Communities use Louvain on an undirected projection, with reciprocal edge weights summed, resolution 1 and the highest modularity across seeds 0–19 with deterministic tie-breaking. The report includes a DOI-only partition, resolution 0.5/1.5 sensitivity checks and adjusted Rand agreement on shared connected papers. ForceAtlas2 runs within connected components with seed 42 and 500 iterations; separate components are packed at the right and isolated papers below. Relative placement of disconnected components is arbitrary. Figure exports preserve directed edges; browser views show arrowheads on every visible link.

Run citation checks and the full browser interaction suite with:

```sh
python -m unittest discover -s tests -p 'test_citations.py' -v
node --test tests/*ui.test.mjs
```

`DPE_CITATION_SNAPSHOT=/path/to/run/snapshot.json` enables the full-data citation DOM check. The Python checks cover exact matching, conflicting DOIs, hash-bound review decisions, cached replay, directed degrees, components, reciprocal weights, topic denominators, immutability and failure states. The complete Python suite can also use `NUMBA_CACHE_DIR=/tmp/dpe-numba-tests` when native modelling dependencies need a writable JIT cache.
