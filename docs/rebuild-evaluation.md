# Static rebuild evaluation — 15 October 2026

Training excluded Volume 37 Issues 5 and 6 from the bundled 1,221-record source, giving **1,207 records / 1,138 complete abstracts**. Only those abstracts contributed to fitting, vocabulary, calibration and projections. Existing text-keyed training embeddings were reused; new-issue embeddings were absent from the experiment cache.

## Selected model and readable names

All 54 configurations were evaluated. Candidates with **at least 10 topics** at seed 42 were preferred, with no upper cap; their top three configurations were checked across seeds 42, 7 and 21. Mean weighted score selected the winner and stability broke ties. This supersedes the earlier five-topic release selected without the preference.

The selected model has **10 topics**, with `n_neighbors=40`, `n_components=5`, `min_cluster_size=20`, `min_samples=5`. The final fitting seed is 42 and Qwen revision is `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.

1. Conflict, war and peace
2. Military spending and economic growth
3. Terrorism and counterterrorism
4. Defence industries and procurement
5. Defence spending and alliances
6. Military personnel, recruitment and veterans
7. Arms trade and arms races
8. Geopolitical risk and financial markets
9. Military equipment costs
10. Military spending and public debt

Names were reviewed against each topic's keywords and all three complete representative abstracts. Hierarchy names summarize their member topics. The label manifest binds names to the training corpus, keyword lists, representative papers and hierarchy membership; names do not alter assignments or model scores. Incremental releases preserve them.

| Measure | Untuned baseline | Selected, after reassignment |
| --- | ---: | ---: |
| NPMI coherence | 0.0899 | 0.1382 |
| Original-embedding cosine silhouette | 0.1046 | 0.0770 |
| Keyword diversity | 0.9300 | 0.9300 |
| Assigned coverage | 78.73% | 92.97% |

The model leaves 80 training abstracts unassigned. Frozen-centroid calibration accepted 116 of 120 held-out confident members, with 95.69% agreement, a 0.475 cosine threshold and a 0.02 runner-up margin. This measures consistency with discovered clusters, not independent classification accuracy.

## Penultimate issue update

Issue 37:5 added seven records, yielding **1,214 articles**. Exactly **seven Qwen embeddings and seven BGE search vectors** were newly computed. All **1,138 historical assignments and map coordinates** were preserved, along with model identity, readable names and hierarchy. Search reused 1,207 earlier vectors.

| Paper | Topic | Cosine | Margin |
| --- | --- | ---: | ---: |
| Europe’s dependence on US foreign military sales: evidence for policy makers from a new database | Unassigned | 0.637 | 0.008 |
| The unsung ‘sheroes’: studying the role of women as decision makers in curbing conflicts | Conflict, war and peace | 0.647 | 0.110 |
| Is fiscal decentralization helpful for mitigating internal conflict risk? sub-Saharan African countries’ focus | Conflict, war and peace | 0.602 | 0.107 |
| The impact of violence on economic growth: evidence from Colombia’s armed conflict | Conflict, war and peace | 0.490 | 0.038 |
| Reassessing the military expenditure–economic growth nexus in the context of geopolitical risk: evidence from Poland | Military spending and economic growth | 0.495 | 0.081 |
| Macroeconomic consequences of the war in Ukraine on Central and Eastern European economies: a SVAR analysis | Unassigned | 0.644 | 0.014 |
| Unpacking the effect of coup d’état on energy poverty in Africa | Conflict, war and peace | 0.545 | 0.076 |

Citation coverage changed from 1129 to 1136 eligible nodes and from 3061 to 3080 directed edges. Graphs include only their checkpoint's catalogue. The website and current methods download no longer append provisional markers to community names; archived review evidence is retained.

## Verification and artifacts

All **47 Python tests** and **26 JavaScript tests** passed, including the full saved snapshots and readable-label rendering. Baseline and Issue 5 releases each passed **12/12 real-browser checks** across Chromium, Firefox and WebKit. The updated Issue 5 website is approximately 103.7 MB, with assets loaded on demand.

Resuming the completed update reported zero new document encodings. **Issue 37:6 was not run**; its manual update instructions are in the README.

- `data/local-artifacts/baseline-min10/release/`: labelled model, topic report, corpus, citation evidence and baseline site.
- `data/local-artifacts/issue-5-min10/release/`: updated assignments, encoding measurements, browser results and screenshots.
- `dist/`: current site built from the Issue 5 dataset and latest JavaScript.
- [Machine-readable evaluation](rebuild-evaluation.json): all trials, finalists, baseline comparison, label list, update assignments and timings.

Large model and release artifacts remain local and ignored by Git. The source, label manifest and compact evaluation are versioned. Prior releases and immutable citation downloads are retained.
