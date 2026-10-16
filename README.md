# Defence and Peace Economics index

A static JavaScript site for browsing RePEc articles, exploring BERTopic themes and timelines, viewing citation networks, and searching semantically **in the browser**. It needs no Redis, Qdrant, FastAPI, Docker, application server, or hosted inference. Python runs only during offline data preparation; any static host can serve the finished site.

The bundled source is `data/defpea.redif`, from [RePEc](https://ideas.repec.org/getdata.html). It contains 1,221 article records. Articles without abstracts remain browsable but are excluded from topic fitting. The unrelated research PDFs and archived retrospective evidence are not published.

The article index also filters verified special issues and the Country Survey series,
with optional selection of a particular issue or country. See [collection evidence and maintenance](docs/collections.md).

## Setup

Use Python 3.12 and Node 24. Initial installation and model downloads need internet access.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-topics.txt
python -m pip install -r requirements-citations.txt  # citation graphs and figure exports
npm ci
npx playwright install chromium firefox webkit
```

Core parsing, file management and export use the Python standard library. Topic packages and citation packages are offline dependencies. `--without-citations` omits citation collection and analysis when that optional stack is not installed; the site then explains that citation analysis was not included.

## MCP tools

The optional local MCP server exposes RePEc fetching and the existing incremental
update pipeline over stdio. Install it into the same Python environment as the
topic and citation dependencies, then register its absolute launcher paths:

```sh
python -m pip install -r requirements-mcp.txt
codex mcp add dpe -- "$PWD/.venv/bin/python" "$PWD/scripts/run-mcp.py"
codex mcp get dpe
```

Use your actual environment's Python path if it differs from `.venv`. The launcher
works from any directory. Other MCP clients can use the same command and argument;
no HTTP service or credentials are needed. Set `tool_timeout_sec = 120` in the
`[mcp_servers.dpe]` entry to allow time for FTP retries. See
[Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

| Tool | Purpose |
| --- | --- |
| `dpe_fetch_redif()` | Fetch the complete DPE catalogue from the fixed anonymous RePEc FTP URL; return a snapshot ID, checksum, retrieval time, counts and changes against the current release. |
| `dpe_update_model(snapshot_id, through?, device?, without_citations?, allow_removals?)` | Freeze that snapshot and start an incremental update of the current model; return a workspace ID immediately. |
| `dpe_update_status(workspace_id?)` | Show completed stages, failures, review paths and the finished release. Omit the ID to inspect the current model. |
| `dpe_resume_update(workspace_id)` | Resume after resolving an error or citation review, verifying completed stages before reuse. |

First call `dpe_fetch_redif`, then pass its `snapshot_id` to `dpe_update_model`,
and check the returned `workspace_id` with `dpe_update_status`. A trained baseline
must already exist. Omit `through` to include all issues; use `37:6`, for example,
to cap the catalogue. Defaults keep citations and full browser verification.
The model identity, reviewed topic names, hierarchy and historical assignments
are preserved. This is an incremental refresh; use the training CLI to discover
new clusters deliberately.

Downloads are stored in `data/local-artifacts/repec/<snapshot_id>/` with raw ReDIF
and provenance. Fetching rejects empty, oversized, malformed, duplicate and
wrong-series records, uses network timeouts and retries transport failures.
Updates verify snapshot checksums and reject missing existing handles unless
`allow_removals` is explicitly enabled. The CLI's `--fetch` uses the same validation.

Jobs live in `data/local-artifacts/mcp-updates/<workspace_id>/`; detached workers
survive MCP disconnects and restarts. One MCP update can run at a time. Status
returns the worker log, review decisions and report paths. For `review_required`,
inspect `review.json`, correct that workspace's `inputs/decisions.json`, then
resume. A changed current release or damaged completed artifact requires a fresh
workspace. Only successful checked releases update `current.json`; publishing
the website remains a separate explicit command. Retain the returned IDs for
checking jobs from a later session.

Run MCP and fetch checks with the normal Python test suite after installing
`requirements-mcp.txt`. Protocol tests exercise real stdio discovery and calls;
network and failure tests run against isolated fixtures.

## Train and demonstrate the held-out update

```sh
python -m app.update train --source data/defpea.redif \
  --exclude 37:5 37:6 --labels data/topic-labels.json \
  --topic-cache data/local-artifacts/min10-topic-cache \
  --search-cache data/local-artifacts/min10-search-cache \
  --output data/local-artifacts/baseline-min10
python -m app.update refresh --source data/defpea.redif --through 37:5 \
  --previous data/local-artifacts/baseline-min10/release \
  --topic-cache data/local-artifacts/min10-topic-cache \
  --search-cache data/local-artifacts/min10-search-cache \
  --output data/local-artifacts/issue-5-min10
```

The baseline contains 1,207 articles and 1,138 complete abstracts. Issue 37:5 adds seven articles, yielding 1,214 records. Issue 37:6 stays withheld. `inputs/split.json` records exact membership; `inputs/source.redif` preserves the original source. The full source is retained as provenance, but only `inputs/corpus.json` enters topic fitting, vocabulary, calibration and projection. Existing full-corpus settings and labels are not reused.

Training compares 54 UMAP/HDBSCAN configurations using pinned `Qwen/Qwen3-Embedding-0.6B` revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`. Coherence (document NPMI) and original-embedding cosine silhouette have weight 2; keyword diversity and coverage have weight 1. Metrics are normalized over valid trials. Candidates with at least 10 topics at seed 42 are preferred, with no upper cap. Their top three configurations are evaluated with seeds 42, 7 and 21; mean score selects the winner and adjusted mutual information breaks ties. Smaller models are considered only if the grid contains no valid model with at least 10 topics. The winning parameters are fitted with seed 42. This selects the best tested configuration, not an independently verified ground truth.

The grid is `n_neighbors=[10,20,40]`, `n_components=[5,10]`, `min_cluster_size=[20,35,50]`, `min_samples=[1,3,5]`. Reports retain the untuned baseline, all trials, finalist stability, representative abstracts, library versions, and quality before and after calibrated outlier reassignment. All abstracts are checked against the encoder limit without truncation. Caches are keyed by full abstract text and encoder identity. Apple Silicon uses MPS automatically; choose `--device cpu` or `--device cuda` explicitly when appropriate.

Human-readable topic and hierarchy names are reviewed against extracted keywords and representative abstracts. `--labels data/topic-labels.json` applies the saved manifest only when its training corpus, keywords, representative papers and hierarchy match; mismatches fail instead of silently reusing names. Future corpora need a newly reviewed label manifest, or omit `--labels` to inspect extracted keywords before naming the new model.

Updates preserve model identity, labels, centroids, hierarchy, historical assignments and map positions. New or corrected abstracts receive a calibrated cosine-centroid assignment and saved UMAP projection. Uncertain matches remain unassigned. Counts, descriptive keywords and temporal representations are refreshed without reclustering. Calibration targets at least 95% agreement with held-out cluster assignments; that is consistency with discovered clusters, not independently measured classification accuracy.

The completed experiment and measured results are documented in [docs/rebuild-evaluation.md](docs/rebuild-evaluation.md). Large generated releases and model artifacts are ignored by Git; retain them locally or in your own artifact backup.

## Manual final-issue update

The original held-out experiment stopped at issue 37:5. The MCP refresh on
15 October 2026 added issue 37:6 to the current local release: **1,221 articles**,
seven new topic embeddings and seven new search vectors, with all **1,145**
earlier abstract assignments and map positions preserved. Model identity and
hierarchy are unchanged, and all 12 browser checks passed. To reproduce that
seven-article update from the earlier checkpoint, run:

```sh
python -m app.update refresh --source data/defpea.redif --through 37:6 \
  --previous data/local-artifacts/community-labels/release \
  --output data/local-artifacts/issue-6
python -m app.update status data/local-artifacts/issue-6
python3 scripts/preview_static.py \
  --directory data/local-artifacts/issue-6/release/site --port 8790
```

Expect seven additions, 1,221 articles, unchanged `model_id`, and unchanged earlier topic assignments. Inspect `release/update-report.json` for additions, encoding counts, assignments and timings; inspect `release/browser-results.json` and screenshots for browser validation. The default pipeline checks Chromium, Firefox and WebKit before selecting the new local release. An unchanged refresh performs zero document encodings.

For later complete RePEc snapshots, use `--fetch` instead of `--source` and omit `--through`. Fetch writes into the new workspace and does not overwrite the bundled source. Supply the complete catalogue, not an issue-only file. Removed handles fail unless `--allow-removals` is explicitly supplied. Metadata changes refresh display/search text; abstract corrections also reclassify the affected article. To deliberately discover a new topic structure, run `train --exclude` with no excluded issues and a new output directory; this retunes the full source and creates a new model identity, resetting old theme links.

## Review, resume and rollback

```sh
python -m app.update status data/local-artifacts/issue-5-min10
python -m app.update resume data/local-artifacts/issue-5-min10
```

Each workspace has a nonblocking local lock. Completed stages have SHA-256 manifests and are verified on resume; damaged completed artifacts require a new workspace. Failed work does not replace the current local release. Review `review.json` when citation publication stops, correct `inputs/decisions.json`, then resume. Decisions remain editable until citation completion; subsequent changes require a fresh workspace. Reference records are frozen in `inputs/records.json` for replay. Existing verified Crossref deposits are cached; missing deposits are collected through the public API. `CROSSREF_MAILTO` optionally supplies contact information.

Citation identity, eligibility, recovered links and DOI samples retain their review gates. Community interpretations are reused only for exactly matching membership. These are summaries of citation-linked groups, distinct from BERTopic labels; mixed groups can span several subjects. Before publishing a new corpus, inspect the community leading papers in `release/citations/review.json` and review any generic `Community N` labels. Store names, interpretations and reviewed handles in `data/citation-decisions.json`, keyed by `membership_sha256`, never by the numeric community ID. Run another refresh into a fresh workspace with the same corpus and corrected decisions to publish the labels. A decisions-only refresh rebuilds citation outputs while preserving the topic snapshot and performing zero document encodings. Withheld articles create no graph nodes or edges, even when retained evidence refers to them. Each citation release includes its own immutable downloads and a comparison against its current topic assignments.

`data/local-artifacts/current.json` points to the last complete release. Releases contain corpus, source provenance, model files, topic snapshots, citation artifacts, dataset, static site, browser evidence, and an integrity manifest. Model files are trusted local artifacts: do not load untrusted serialized projections. Keep older release directories for rollback. To preview or publish an earlier release, pass its explicit site path; no model retraining or source changes are required.

## Build, preview and publish

For unattended Friday 09:00 checks on the Mac mini, including output logs,
checked automatic publication and iCloud email notifications, see
[weekly updates](docs/weekly-updates.md).

Install the [documentation build dependencies](#python-documentation) first; every
site build includes a fresh Sphinx render.

```sh
npm run static:build       # build dist/ from the current local release's dataset
npm run static:preview    # http://localhost:8790/ and /dpeindex/
npm run static:validate
```

The footer links to the research report at `research-note/`. Static builds
include the self-contained render from `manuscript/manuscript.html` and its PDF. After editing
the note, render both formats with `quarto render manuscript/manuscript.qmd --to all` before
building the site; the site build does not rerun the manuscript analysis.

An explicit release can be selected without changing the current pointer:

```sh
npm run static:build -- --dataset data/local-artifacts/issue-5-min10/release/dataset \
  --output dist --cache data/local-artifacts/search-cache
```

Browser search uses the existing quantized BGE-small-en-v1.5 model, self-hosted weights and WASM, a Web Worker, CLS pooling and normalized 384-dimensional vectors. Offline document encoding and browser query encoding use the same pinned model and settings. Model assets load on the first search, not during browsing. Filters run before ranking. Model failures offer labelled keyword results and retry. This small search model is separate from the complete-abstract Qwen topic encoder.

Publishing remains explicit:

```sh
npm run static:publish -- data/local-artifacts/issue-5-min10/release/site
```

The existing publisher validates files, preserves earlier immutable citation downloads, and pushes a normal commit to `gh-pages`. It never force-pushes. GitHub Pages should serve `gh-pages` from its root. No publication is performed by training or updating. Roll back by passing an earlier release's site directory to the same publishing command.

## Checks

```sh
python -m unittest discover -s tests -v
npm test
DPE_PREVIEW_DIR="$PWD/data/local-artifacts/issue-5-min10/release/site" npm run test:browser
npm run static:validate -- data/local-artifacts/issue-5-min10/release/site
```

The tests cover parser/grouping rules, split isolation, calibration and ambiguous assignments, corrections, saved point preservation, integrity failures and resumability, citation evidence and graph consistency, temporal denominators, static filters and deep links, real browser inference and encoder agreement, retry/fallback, mobile layouts and both deployment prefixes. `--skip-browser-tests` is available for development; use the default checked pipeline for a release.

## Python documentation

Sphinx guides and generated module references live in [docs/python](docs/python/index.rst).
Build them independently of the model dependencies:

```sh
python3 -m venv .venv-docs
.venv-docs/bin/python -m pip install -r requirements-docs.txt
.venv-docs/bin/python -m sphinx -b html -W --keep-going docs/python docs/python/_build/html
.venv-docs/bin/python -m sphinx -b doctest -W --keep-going docs/python docs/python/_build/doctest
```

Open `docs/python/_build/html/index.html`. The docs cover architecture, training
and refresh workflows, citation review, MCP jobs, artifact contracts, all 17
application modules, and supporting preview/manuscript scripts. The build imports
API definitions with the optional MCP transport mocked; it does not train models,
fetch source data, launch jobs, or publish the documentation.

`npm run static:build` also renders these docs into `dist/python-docs/` for
GitHub Pages, failing on Sphinx warnings. Install `requirements-docs.txt` before
building the site. The builder uses `.venv-docs/bin/python` when available,
otherwise `python3`; set `DPE_DOCS_PYTHON` to use another Python executable.
The rendered pages, source links, search index and assets are validated and
published with the rest of the site. The site footer links to the Python docs,
research report, GitHub repository and defenceeconomist.io.
