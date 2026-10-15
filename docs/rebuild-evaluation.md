# Static rebuild evaluation — 15 October 2026

The source snapshot contains 1,221 records. Training excluded Volume 37 Issues 5 and 6, giving **1,207 records / 1,138 complete abstracts**. The model and keyword vocabulary were fitted only on those abstracts. Existing text-keyed training embeddings were reused; new-issue embeddings were deliberately absent from the experiment cache.

## Selected model

All 54 configurations were evaluated. The three highest-scoring configurations were tested across seeds 42, 7 and 21. Removing the old 10–20-topic preference selected **five topics**, with `n_neighbors=20`, `n_components=5`, `min_cluster_size=35`, `min_samples=5`. The final fit uses seed 42. Qwen revision: `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.

| Measure | Untuned baseline | Selected, after reassignment |
| --- | ---: | ---: |
| NPMI coherence | 0.0899 | 0.1853 |
| Original-embedding cosine silhouette | 0.1046 | 0.1119 |
| Keyword diversity | 0.9300 | 0.9600 |
| Assigned coverage | 78.73% | 98.95% |

The final model leaves 12 training abstracts unassigned. Frozen-centroid calibration accepted 189 of 208 held-out confident members, with 97.88% agreement, a 0.40 cosine threshold and a 0.02 runner-up margin. This measures consistency with discovered clusters, not independent classification accuracy. Five broad clusters reflect the stated scoring rule; no claim is made that this is the only meaningful taxonomy.

## Penultimate issue update

Issue 37:5 added seven records, yielding **1,214 articles**. Exactly **seven Qwen embeddings and seven BGE search vectors** were newly computed. All **1,138 historical assignments and map coordinates** were preserved, as were model identity, labels and hierarchy. Search reused 1,207 earlier vectors. Six new abstracts were assigned; one remained unassigned because it did not meet the margin criterion.

| Paper | Topic ID | Cosine | Margin |
| --- | ---: | ---: | ---: |
| Europe’s dependence on US foreign military sales: evidence for policy makers from a new database | 0 | 0.624 | 0.147 |
| The unsung ‘sheroes’: studying the role of women as decision makers in curbing conflicts | 1 | 0.650 | 0.110 |
| Is fiscal decentralization helpful for mitigating internal conflict risk? sub-Saharan African countries’ focus | 1 | 0.600 | 0.117 |
| The impact of violence on economic growth: evidence from Colombia’s armed conflict | 1 | 0.489 | 0.076 |
| Reassessing the military expenditure–economic growth nexus in the context of geopolitical risk: evidence from Poland | Unassigned | 0.436 | 0.017 |
| Macroeconomic consequences of the war in Ukraine on Central and Eastern European economies: a SVAR analysis | 4 | 0.649 | 0.027 |
| Unpacking the effect of coup d’état on energy poverty in Africa | 1 | 0.542 | 0.071 |

Citation coverage changed from 1129 to 1136 eligible nodes and from 3061 to 3080 directed edges. Each graph uses only its checkpoint's catalogue; cached reference evidence and reviewed decisions were retained. Community interpretations remain membership-bound.

## Verification and artifacts

All 45 Python tests and all 25 JavaScript tests passed, including the full saved snapshots. The optional citation-free pipeline also passed all 12 real-browser checks.

Both baseline and Issue 5 releases passed **12/12 real-browser checks** across Chromium, Firefox and WebKit. Checks include real WASM inference, encoder agreement, retry/fallback, filters, links, plots, citation downloads, mobile views and deployment prefixes. The Issue 5 site is approximately 103.6 MB; assets load on demand.

Resuming the completed update and refreshing the identical Issue 5 source both reported zero new document encodings. **Issue 37:6 was not run**; see the manual instructions in the README.

- `data/local-artifacts/baseline/release/`: training model, topic report, corpus, citation evidence and baseline site.
- `data/local-artifacts/issue-5/release/`: updated site, assignments, encoding measurements, browser results and screenshots.
- `data/local-artifacts/issue-5-replay/state.json`: unchanged-refresh replay evidence.
- `dist/`: current rebuilt static site.
- [Machine-readable evaluation](rebuild-evaluation.json): every trial, finalist, baseline comparison, update assignments and measured timings.

Model files and large releases are local ignored artifacts; the source code and compact evaluation are versioned. Nothing has been published to GitHub Pages by this rebuild.
