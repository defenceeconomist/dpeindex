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

Readable topic and hierarchy names are maintained in `data/topic-labels.json`, based on the current model’s extracted keywords and representative abstracts. They appear consistently in the map, hierarchy, timeline and Articles filters. Labels are applied only when the run, corpus, keywords, representative papers and hierarchy membership match; other runs retain their extracted keyword labels. The original names remain available as `keyword_label` in `/api/topics`, with label provenance in `metadata.display_labels`. This display layer leaves the immutable model snapshot, assignments, embeddings and quality measurements unchanged.

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

DOM interaction checks exercise topic/branch/map selection, temporal charts, the main-page abstract reader, theme and year filtering, stale results and API failures. Install the pinned JavaScript dependencies once:

```sh
npm ci
npm test
```

## Themes over time and article filters

The Topics page uses [BERTopic topics over time](https://maartengr.github.io/BERTopic/getting_started/topicsovertime/topicsovertime.html) to show changing prevalence and period-specific keywords while retaining the published topic identities and final assignments. It includes line charts and a heatmap, annual/five-year/decade intervals, count/share measures, linked theme highlighting, an accessible period table and links to matching articles. Calendar bins include all available publication years. Early catalog coverage and partial end periods are identified. Share denominators include unassigned abstracts; missing abstracts are counted separately. Periods without abstracts appear as gaps, rather than zero prevalence.

Temporal analysis is offline. It restores the published assignment-based c-TF-IDF representation from complete canonical abstracts, checks its keyword signature against the published model, then runs `topics_over_time` separately for each interval width. `global_tuning=True` stabilises period keywords against the pooled topic representation; `evolution_tuning=False` keeps periods independent of previous period representations. Smoothed words may therefore originate outside the selected period. Counts and assignments are never smoothed. The operation loads no sentence-transformer weights, fits no clusters and calls no inference API.

Build or refresh the temporal analysis for the current model without replacing it:

```sh
docker compose --profile topics build dpe-topics
docker compose --profile topics run --rm dpe-topics --timeline
```

The command requires a canonical corpus fingerprint matching the published model, writes `runs/RUN_ID/timeline.json` in the artifact volume, and publishes under `dpe:topics:timeline:RUN_ID` only if that model is still active. A failed analysis keeps the existing timeline. New `--tune` and `--rebuild` runs include the timeline in their atomic model snapshot. `/api/topics` returns it as `timeline`; missing or invalid analysis does not remove the existing map or hierarchy. Undated abstracts are reported separately.

`GET /api/themes` supplies current theme labels and compact article assignments for the Articles filters. `/api/issue` and `/api/search` accept `theme`, `run`, `year_from` and `year_to`. A theme requires its model run ID; old-run requests return 409 so topic IDs cannot silently change meaning. Semantic filtering uses Qdrant point-ID conditions before ranking and limiting results, and exact title/author matches use the same filter. Main-page URLs retain filters; `article=HANDLE` plus volume/issue opens that paper’s complete abstract. Old model links reset the theme with an explanation. Theme availability failures leave normal issue browsing and search usable.

## Archived retrospective reference

The retrospective comparison is retained for offline research review in `data/retrospectives.json` and `app/retrospectives.py`. It is not displayed on the site or included in `/api/topics`. The source PDFs and EPUB in `docs/` are not served by the site.

The reference preserves source-table counts, category definitions, provenance and a reviewed crosswalk bound to model run `20261015T112751Z-f48f8afb`. Editorial counts and model coverage have different populations; their agreement does not measure classification accuracy. Future model rebuilds do not automatically revise this scholarly interpretation.

The Python suite checks source transcription, publication-period boundaries, denominators, overlapping mappings, model binding and archive failure states. To include the published-run regression with an exported immutable snapshot:

```sh
DPE_AUDITED_SNAPSHOT=/path/to/snapshot.json python -m unittest discover -s tests -v
```

`DPE_TOPIC_SNAPSHOT=/path/to/api-topics.json node --test tests/*ui.test.mjs` also exercises the full published map, hierarchy and timeline through the linked explorer. Both standalone and portal routes use relative asset and API URLs.

## Citation conversations

The **Citations** page at `/citations/` (or `/dpe/citations/` through the portal) maps directed citations between eligible articles in the existing 1997–2026 RePEc catalogue. It adds an independently discovered citation-community view alongside the abstract-based Topics page. Scopus, hosted inference and topic-model retraining are not required. Reference deposits come from the public Crossref API.

The initial audited analysis contains **1,143 eligible papers, 3,097 directed links, 36 communities and 188 papers with no observed internal links**. The DOI-only baseline has 3,093 links. The 78 exclusions include issue introductions, editorials, administrative notices, corrections and routine book notices; research comments, replies and substantive review articles remain eligible. Community labels are provisional descriptions based on the leading titles and available abstracts. Some communities contain only two papers and should not be read as established intellectual conversations. Sixty eligible papers have no deposited reference list. The methods report and reference review queue expose the remaining gaps.

The page focuses on the directed network and a paginated ranking of every paper matching the selected community, citation threshold and publication years. Rank by **citations in, citations out or citations in + out**. Changing the count selector also selects that ranking measure; the ranking buttons can change the sort without changing the paper filters. The table renders only the requested page (25, 50 or 100 rows) from the network data already loaded for the map, with previous/next controls and direct page selection. Filter and sort changes return to page one; selecting a paper on the map opens its ranking page. Community buttons and the minimum-citation and From/To year sliders apply to both the map and table, with live count/year labels. The map starts with at least **3 citations in** from other eligible DPE papers: 351 papers and 1,193 links in the initial study. The count selector also supports outgoing or combined counts; set the minimum to 0 to remove the count limit. Reset restores all years and the default of 3 incoming citations. Selecting a paper hidden by the filters reveals it by resetting the community/year limits and minimum count; selecting papers with no observed links also sets the minimum to 0. Every visible edge has a directional arrow from citing to cited paper; selecting a paper highlights incoming and outgoing links. Counts above the map and in the rankings refer to the whole recovered network; the visible map count reflects filters and the selected edge source. Switching to DOI-only edges retains the recovered citation counts, community assignments and coordinates. Article links open the existing Articles reader. Citation totals refer only to this study's eligible article universe, not global citations or the complete historical journal. Temporal prevalence, the frozen BERTopic comparison, methods, coverage and downloads remain available in the published API and run artifacts.

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


## GitHub Pages deployment

The same website source supports the Mac's FastAPI service and a standalone static deployment at **https://defenceeconomist.github.io/dpeindex/**. `main` maintains both modes; `gh-pages` contains generated publishing output. The public site uses no running Python service, Redis, Qdrant, hosted inference or connection to the Mac. Preparing a new release still happens on the Mac with the shared Redis service available.

Use Node 24 and Python with the base requirements installed. Install dependencies, export the currently published data and build:

```sh
npm ci
npm run static:build
npm run static:preview
```

The preview serves both `http://localhost:8790/` and `http://localhost:8790/dpeindex/`. Build output is `dist/`; downloaded assets, the frozen dataset and reusable search vectors are retained under `.static-cache/`. Both directories are ignored by Git and Docker. To rebuild assets from the last captured dataset without contacting Redis, use `npm run static:build -- --skip-export`.

`python -m app.export_static --output DIRECTORY` can also export the data independently. It watches the catalogue, source and published model pointers while capturing the full article records and immutable citation archives. A concurrent update retries the capture. Missing records, inconsistent counts, invalid timelines, unreviewed citation archives and corrupt downloads fail the export. Older catalogues without a stored corpus fingerprint use the live API's computed fallback. Exporting does not sync, retrain, collect references or publish analysis. Existing `stale` and `topic_changed` flags remain visible in the exported payloads. The offline retrospective reference is not added to the public interface.

The static data client loads issue files lazily and caches full records for article expansion. It preserves run-scoped theme and inclusive year filters, missing-abstract records, unassigned abstracts and deep links. Static builds replace the API configuration only in their output; the Mac service continues to use its existing API and Qdrant search.

### Browser semantic search

The first search automatically loads the self-hosted quantized BGE-small-en-v1.5 model and its WebAssembly runtime. Inference runs in a worker with one WASM thread; a GPU and special cross-origin headers are unnecessary. Download/startup progress appears in the search status. Versioned model assets use browser caching when available; model loading failures show explicitly labelled keyword results and a retry button. Searches stay on the visitor's device.

Search weights are pinned to immutable Hugging Face revision `ea104dacec62c0de699686887e3f920caeb4f3e3`. The offline and browser encoders share the same tokenizer, q8 model, CLS pooling, normalization, 384 dimensions, 512-token search limit and retrieval prefix. Article text follows the Mac's title/authors/abstract/keywords composition. This search limit is separate from the complete-abstract topic analysis. Search vectors are exported as little-endian float32 values with an ordered handle list; they never replace the Mac's Qdrant vectors. Theme/year filters apply before the semantic top 40, with the existing exact author/title prioritisation.

The initial complete build is approximately 148 MB, including retained citation downloads; browsing does not download all of this. Model weights are approximately 34 MB; the model and WASM runtime together transfer about 46 MB on the first semantic search, before HTTP compression. Local preview checks on 15 October 2026 measured 0.4–1.0 seconds for first search and 55–125 ms for a repeat search on the development Mac. These local timings exclude internet bandwidth and do not predict performance on a visitor's phone. Four representative comparisons against the Mac/Qdrant search returned the same first result in every case, with 7–8 of the top ten results in common; browser/offline query embeddings had cosine agreement above 0.999999.

### Validate and publish

```sh
npm test
NUMBA_CACHE_DIR=/tmp/dpe-numba-tests python3 -m unittest discover -s tests -v
npx playwright install chromium firefox webkit
npm run test:browser
npm run static:validate
npm run static:publish
```

The browser suite checks real WASM inference in Chromium, Firefox and WebKit, offline/browser encoder agreement, cache availability, failed model loading and retry, rapid queries, both deployment prefixes, article details, filters, chart rendering, citation pagination, downloads and mobile layouts. To exercise the deployed site, use `DPE_PUBLIC_URL=https://defenceeconomist.github.io npm run test:browser`. Build validation checks asset/data/model/download hashes, vector identities and dimensions, relative HTML links, the 1 GB site limit and Git's 100 MiB individual-file limit. The generated bundle includes dependency and model licence information under `licenses/`.

Publishing requires an authenticated Git push to `origin`. The publishing command validates `dist/`, creates a separate temporary Git worktree, preserves all previously published citation download paths, rejects changes to immutable downloads, validates the combined site, then makes a normal commit and pushes it to `gh-pages`. It leaves the source checkout and its untracked files alone. Retained archives are never silently deleted to fit a size limit: an oversized build fails. Publishing is manual; local data refreshes do not change the public website automatically.

In the repository's **Settings → Pages**, select **Deploy from a branch**, branch **gh-pages**, folder **/(root)**. The generated `.nojekyll` file makes GitHub's managed Pages workflow publish the prepared files directly. No custom domain or additional paid service is required.

For rollback, check out the desired earlier `gh-pages` commit into a separate temporary worktree and pass its directory to `npm run static:publish -- /path/to/previous-site`. The publisher restores that release as a new commit while retaining newer immutable citation downloads. Do not reset or force-push the publishing branch.

## Complete staged updates and the two-issue experiment

`python -m app.update` prepares a complete release in a private Docker Compose project. It never writes to the live Redis/Qdrant services, edits `data/defpea.redif`, or pushes Pages. It reads the live catalogue, published model settings, citation evidence and search vectors, then keeps frozen inputs, logs, hashes and outputs under the ignored `data/update-artifacts/` directory. Successful preparation stops its private containers and retains their volumes for recovery.

Prerequisites are the running existing `dpe-index` service, the web and citations images built with the normal Compose file, Node dependencies, and a Python environment containing the base and topic requirements. By default the topic interpreter is `data/topic-artifacts/host-venv/bin/python`; override it with `--topic-python` or `DPE_TOPIC_PYTHON`. Model weights and baseline embeddings are copied from the existing caches. Source cache volumes are mounted read-only. Topic and citation workers use separate environments because their pinned NumPy requirements differ. Use `--device mps` for native Apple Silicon inference; CPU is the default.

Bootstrap a persisted model and website, then prepare subsequent complete RePEc snapshots:

```sh
python3 -m app.update refresh --source data/defpea.redif --mode rebuild \
  --output data/update-artifacts/initial --device mps
python3 -m app.update refresh --fetch --previous data/update-artifacts/initial \
  --output data/update-artifacts/next --device mps
```

A source must contain the complete catalogue, rather than only the new issue. The updater reports added, changed and removed handles. Removals require `--allow-removals`. Abstract corrections trigger reclassification; other metadata corrections update the displayed records and applicable search vectors. Papers without abstracts remain browsable without an assignment. An unchanged incremental refresh reuses the previous release with zero document encodings. `--mode rebuild` deliberately refits all current abstracts using the saved parameters and encoder revision; it does not run the tuning search.

Incremental updates freeze the model's topic IDs, centroids, labels, hierarchy and original map positions. They encode only uncached abstract texts, classify new or corrected abstracts using calibrated similarity and margin thresholds, and place new points with the saved UMAP projection. Calibration requires the existing 95% held-out cluster-agreement policy; this is not independently measured classification accuracy. Inadequate calibration or ambiguous matches leave papers unassigned. Current keyword descriptions and timeline counts are recalculated from the latest assignments, independently of frozen training evidence.

Every assignment snapshot has a unique `run_id` and a stable `model_id`. Theme links use the model identity, so an incremental release preserves their meaning. Full refits create a different model identity and old links receive the existing reset explanation. Legacy snapshots continue to use their run ID as their model identity. A reviewed label manifest can be supplied with `--labels FILE`; it applies only when the baseline corpus, extracted keywords, representative papers and hierarchy membership match exactly.

References are fetched only when absent from the verified cache. Use `--refresh-references` to deliberately refresh existing deposits; changed evidence may require renewed review. Citation topology is rebuilt against the complete current catalogue. Changed community membership cannot inherit an old interpretation. A reviewed reference pointing outside a deliberately reduced catalogue retains its evidence but supplies no edge. The update command stops at required identity, eligibility, recovered-reference or DOI-audit review gates:

```sh
python3 -m app.update status data/update-artifacts/next
# Inspect the reported review.json, edit the workspace's inputs/decisions.json.
python3 -m app.update resume data/update-artifacts/next
```

Frozen original decisions are retained separately. Resume verifies completed artifact hashes, reuses completed stages and reruns citation-dependent stages after a review correction. Corrupt completed outputs require a new workspace; corrupt per-document embedding cache entries are recomputed. Concurrent updates cannot share a workspace. Only trusted, verified local model artifacts are loaded.

The static builder caches document vectors independently by full search text and the pinned encoding settings. It reassembles the ordered matrix without re-encoding unchanged papers. Individual checkpoint builds and previews can be selected explicitly:

```sh
npm run static:build -- --dataset PATH_TO_DATASET --output PATH_TO_SITE --cache PATH_TO_CACHE
python3 scripts/preview_static.py --directory PATH_TO_SITE --port 8791
npm run static:validate -- PATH_TO_SITE
```

Each release is checked in Chromium, Firefox and WebKit, including real semantic inference, filters, links, charts, downloads and mobile layouts. Browser totals come from the dataset manifest. Screenshots and the JSON browser report are saved beside its website.

After inspecting a prepared release, publish that site's directory using the existing explicit publisher:

```sh
npm run static:publish -- data/update-artifacts/next/release/site
```

This publishes the prepared static dataset; it does not promote the private experimental datastores into the Mac service. The publisher retains immutable earlier citation downloads and enforces the existing size limits. Reference archives account for substantial release growth; computationally incremental updates do not make those archives smaller.

Run the reproducible two-issue experiment with:

```sh
python3 -m app.update simulate --issues 37:5 37:6 \
  --output data/update-artifacts/two-issue --device mps
```

This withholds fourteen articles, fits the 1,207-article baseline, then restores Issue 5 and Issue 6 to reach 1,214 and 1,221 articles. Each addition produces both an incremental release and an independent full refit. Only baseline vectors seed the caches, so each arrival exercises seven real new-document encodings in catalogue, topic and browser-search indexing. Frozen Crossref records are replayed as articles arrive, avoiding changes in upstream deposits. Graph work is shared between the two model branches at each corpus checkpoint. The original website is retained as a reference when the existing local build matches the captured published model.

The simulation creates five websites, full model/citation evidence, timing and cache measurements, a changes CSV and `report.html`/`report.json`. Topic comparisons use maximum-overlap matching and label-independent agreement; map displacement is also measured after alignment. Complete-abstract qualitative reviews can be recorded as an array of `{ "handle": "...", "review": "..." }` in the workspace's `article-review.json` before report generation. They do not establish gold-standard classification accuracy. The state records before/after live datastore fingerprints and the `gh-pages` reference to demonstrate isolation. Repeating `resume` on an unchanged completed experiment verifies its artifacts and performs no document encoding. Passing a simulation workspace as `--previous` selects its final incremental release; a particular comparison checkpoint can also be named explicitly.
