# DPE research note

`manuscript.qmd` is the editable source; `references.bib` contains the ten cited references. `manuscript.html` is self-contained and `manuscript.pdf` is the typeset version. No author or affiliation has been invented.

## Reproduce the manuscript

From the repository root:

```sh
python3 manuscript/prepare.py
quarto render manuscript/manuscript.qmd --to html
quarto render manuscript/manuscript.qmd --to pdf
```

Figure regeneration requires Python, NumPy and Matplotlib. The checked environment used Python 3.14, NumPy 2.4.3, Matplotlib 3.11.1 and Quarto 1.8.26 with its installed TinyTeX/XeLaTeX. Quarto rendering itself uses the committed generated tables and figures and requires neither Python nor the original model packages. The first PDF render on another machine may need to install LaTeX packages; with those installed, rendering is offline. Install a Quarto-compatible TeX distribution if necessary.

The generator reads only `data/evidence.json`, checks its checksum against `data/provenance.json`, and writes figures, tables, statistics and `data/crosswalk.csv`. It does not read the moving current-release pointer, fetch references, retrain models, or change the application. PNG figures are embedded in both outputs; separate PDF figures are supplied for publication use.

## Evidence and boundaries

The explicit source release is `data/local-artifacts/mcp-updates/20261015T200537Z-fd83c402/release`. The initial extract was created with:

```sh
python3 manuscript/prepare.py --freeze data/local-artifacts/mcp-updates/20261015T200537Z-fd83c402/release
```

The freeze option refuses to overwrite existing evidence. Keep this note frozen; use a separate directory and review the narrative if preparing an updated edition. The portable extract contains paper metadata and assignments, keyword/representative identifiers, training diagnostics, model definition, citation nodes/edges and summaries, and original retrospective table data. Original abstracts and the four local reference publications are not copied into the manuscript directory.

`data/provenance.json` records the release path and SHA-256 hashes of source artifacts, reference documents, and relevant implementation files. It also records the frozen extract's checksum. `generated/statistics.json` contains derived counts and denominators. `data/crosswalk.csv` records all 55 editorial-category rows across three retrospectives, their original counts and locators, and the reviewed scope links to current topics.

Crosswalk relations describe the editorial category relative to the model: `broader`, `narrower`, `close`, `partial`, or `not_resolved`. Union counts deduplicate topics within a row, but the same topic can recur in other rows. Never add crosswalk rows as if they were a mutually exclusive classification. A blank union count denotes no defensible resolved mapping, not zero articles. This is a manuscript-specific crosswalk and does not modify the application's archived retrospective audit.

## Source review

- Hartley and Sandler (2000), Table 1, printed pp. 4–6: verified category counts, including five arms-trade papers, the 207 total, the broad definition of expenditure studies, and the cutoff at volume 10(2). First-period model coverage is only 48 catalogue records / 45 abstracts.
- Arce and Kollias (2010), Table I, p. 398, and discussion pp. 397–399: verified the 331-paper classification and interpretations of the growth, terrorism and conflict shifts. Their historical comparison reproduces six first-decade arms-trade papers; the note retains the original first-survey count instead.
- Gaibulloev, Kollias and Solomon (2020), Tables 1–2 and thematic discussion: verified the 389-paper total, 190-paper leading-three total, personnel and alliance shifts, and new security/company categories. Their historical 208 total is not substituted for the original 207.
- Delahais and Thyrard, *Evaluation* 32(1), 11–36: reviewed methodology and the section on the added value and limitations of clustering. The note adapts the direct-citation/conversation distinction, without claiming their Scopus universe, external citations or substantive validation were reproduced. The local PDF and Crossref record identify a January 2026 issue and online publication on 26 December 2025; BibTeX uses the issue year with an online-date note.
- Methods references were checked against original arXiv records, JOSS, PLOS, and publisher metadata. The methods description also follows the actual repository implementation, rather than assuming BERTopic defaults.

All three representative abstracts and the keywords for each of the ten current topics were read to review the crosswalk. Particular corrections to the archived mapping concern expenditure-demand models (current topic 4, rather than growth alone), security (overlaps conflict, terrorism and geopolitical markets), and the renumbered personnel, equipment and market topics. Full-abstract review establishes scope plausibility, not article-level ground truth. Citation community labels retain their provisional status.

## Verification

The generator checks snapshot integrity, corpus uniqueness, topic counts, missing and unassigned totals, topic/citation fingerprint alignment, citation edge uniqueness, in/out-degrees, isolate and community counts, editorial totals, and every topic-by-community cell against paper assignments. `validate.py` checks the generated HTML/PDF, bibliography, figure/table references, and required outputs. Final render/layout checks are summarised in `generated/validation.json`.

```sh
python3 manuscript/validate.py
```

The manuscript is about 3,400 words of main text, plus abstract, tables, captions and references. It is a research note on the frozen analysis, not an independently validated re-estimation. The biggest substantive restrictions are pre-1997 absence, 69 missing abstracts, 60 eligible papers without deposited references, age-dependent citation opportunity, and provisional interpretation of citation communities.
