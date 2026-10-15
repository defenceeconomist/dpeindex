import {escapeHtml} from "./article-ui.js";

const esc = escapeHtml;
const pct = (value) => `${value.toFixed(1)}%`;
const relationLabels = {close: "Close match", broader: "Model topic is broader", narrower: "Model topic is narrower", partial: "Partial overlap", not_resolved: "No distinct model topic"};
const doiUrl = (source) => `https://doi.org/${encodeURI(source.doi)}`;
const cite = (period, label) => `<a href="${doiUrl(period.source)}" target="_blank" rel="noopener">${esc(label || `${period.source.authors} (${period.source.year})`)}</a>`;
const category = (period, name) => period.categories.find((item) => item.name === name);
const topic = (period, key) => period.model_topics.find((item) => item.key === key);

function narrative(data) {
  const [first, second, third] = ["first", "second", "third"].map((id) => data.periods.find((period) => period.id === id));
  const editorial = (period, name) => pct(category(period, name).share);
  const model = (period, key) => pct(topic(period, key).share);
  return `<div class="comparison-narrative">
    <p>The three retrospectives describe a journal whose research agenda changes with its security environment. The published model supplies ${third.model_topics.length} fixed themes from the pooled abstracts, counted within the publication periods covered by each paper. These are shared topics across periods, rather than separate models fitted to each decade.</p>
    <p class="comparison-caution"><strong>Retrospective-guided themes.</strong> ${esc(data.method.training)} ${esc(data.method.independence)}</p>
    <p><strong>Conflict and terrorism gain share.</strong> Between the second and third decades, the editorial share of conflict and civil wars rises from ${editorial(second, "Conflict and civil wars")} to ${editorial(third, "Conflict and civil wars")}. The model’s civil-conflict, war and peace topic rises from ${model(second, "civil_conflict")} to ${model(third, "civil_conflict")}; strategic conflict and intervention form a separate topic (${model(second, "strategic_conflict")} to ${model(third, "strategic_conflict")}). Terrorism rises from ${editorial(second, "Terrorism")} to ${editorial(third, "Terrorism")} editorially and from ${model(second, "terrorism")} to ${model(third, "terrorism")} in the model. These are thematic correspondences, not verified classifications of the same papers. ${cite(second, "Arce & Kollias (2010), Table I")}; ${cite(third, "Gaibulloev et al. (2020), Tables 1–2")}.</p>
    <p><strong>Alliance emphasis declines while personnel research grows.</strong> Alliance shares fall from ${editorial(second, "Economics of alliances")} to ${editorial(third, "Economics of alliances")} editorially, alongside ${model(second, "alliances")} to ${model(third, "alliances")} in the model. Personnel research rises from ${editorial(second, "Military personnel and recruitment")} to ${editorial(third, "Military personnel and recruitment")} editorially and from ${model(second, "personnel")} to ${model(third, "personnel")} in the model. ${cite(second, "Arce & Kollias (2010), Table I")}; ${cite(third, "Gaibulloev et al. (2020), Table 1")}.</p>
    <p><strong>Finer splits change the comparison.</strong> In the third retrospective, conflict (${category(third, "Conflict and civil wars").count} papers) outranks defence and the economy (${category(third, "Defence and the economy").count}). The model’s spending/growth topic has ${topic(third, "economic_effects").count} abstracts (${model(third, "economic_effects")}), compared with ${topic(third, "civil_conflict").count} (${model(third, "civil_conflict")}) in civil conflict and ${topic(third, "strategic_conflict").count} (${model(third, "strategic_conflict")}) in strategic conflict. The categories therefore do not support a direct ranking comparison. Expenditure demand, procurement/industrial cooperation and national budgets/policy now have separate topics; industry/innovation, equipment costs, debt and political institutions form others. Conversion, regional research and peacekeeping remain dispersed, and the broad security category spans market risk, sanctions, piracy and policing. Seeding did not recover every editorial category as a standalone cluster. ${cite(second, "Arce & Kollias (2010), pp. 397–399")}; ${cite(third, "Gaibulloev et al. (2020), Table 1")}.</p>
    <p class="comparison-caution"><strong>The first decade is an incomplete sample.</strong> The catalog supplies only volumes 8–9 and the first two issues of volume 10: ${first.coverage.abstract_count} abstracts, including ${first.coverage.unassigned_count} unassigned, against ${first.classified_total} papers classified in the original retrospective. These results cannot establish the first decade’s overall thematic distribution. ${cite(first, "Hartley & Sandler (2000), Table 1, pp. 4–6")}.</p>
  </div>`;
}

function shareChart(rows, denominator, maxShare, kind) {
  return `<ol class="share-bars ${kind}-bars">${[...rows].sort((a, b) => b.share - a.share).map((row) => `<li class="share-row${row.id === -1 ? " unassigned-share" : ""}">
    <div class="share-row-label"><span>${esc(row.name)}</span><span class="share-value">${row.count} / ${denominator} · <strong>${pct(row.share)}</strong></span></div>
    <div class="share-track" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, 100 * row.share / maxShare))}%"></span></div>
  </li>`).join("")}</ol>`;
}

function periodContent(period, data) {
  const c = period.coverage;
  const modelRows = [...period.model_topics, {id: -1, name: "Unassigned abstracts", count: c.unassigned_count, share: c.unassigned_share}];
  const maxShare = Math.max(10, Math.ceil(Math.max(...modelRows.map((row) => row.share), ...period.categories.map((row) => row.share)) / 10) * 10);
  const modelById = new Map(period.model_topics.map((item) => [item.id, item]));
  return `<div class="comparison-period-heading"><h3>${esc(period.label)} <span>${esc(period.years)}</span></h3><p>${esc(period.coverage_label)}</p></div>
    ${period.partial ? '<p class="comparison-caution"><strong>Incomplete first-decade sample.</strong> Volumes 1–7 are unavailable. Volumes 10(3–4) fall outside the original retrospective’s cutoff and are excluded. Do not interpret this sample as the decade’s thematic distribution.</p>' : ""}
    <dl class="comparison-coverage" aria-label="Selected period coverage"><div><dt>Catalog records</dt><dd>${c.record_count}</dd></div><div><dt>Usable abstracts</dt><dd>${c.abstract_count}</dd></div><div><dt>Missing abstracts</dt><dd>${c.missing_abstract_count}</dd></div><div><dt>Unassigned / usable</dt><dd>${c.unassigned_count} / ${c.abstract_count} <small>(${pct(c.unassigned_share)})</small></dd></div></dl>
    <p class="comparison-denominators" role="status"><strong>Two different denominators:</strong> editorial shares use ${period.classified_total} classified papers; model shares use all ${c.abstract_count} usable abstracts, including ${c.unassigned_count} unassigned. Missing abstracts are excluded. The populations are not identical, so matching counts are thematic correspondence, not classification accuracy.</p>
    <div class="comparison-share-charts">
      <section aria-labelledby="editorial-shares-title"><p class="eyebrow">The retrospective</p><h4 id="editorial-shares-title">Editorial categories</h4><p class="chart-description">${period.categories.length} categories · ${period.classified_total} classified papers · ${period.excluded_book_reviews} book reviews excluded</p>${shareChart(period.categories, period.classified_total, maxShare, "editorial")}</section>
      <section aria-labelledby="model-shares-title"><p class="eyebrow">The fixed topic model</p><h4 id="model-shares-title">Abstract themes</h4><p class="chart-description">${period.model_topics.length} themes + unassigned · ${c.abstract_count} abstracts · same 0–${maxShare}% bar scale</p>${shareChart(modelRows, c.abstract_count, maxShare, "model")}<p class="chart-footnote">Charts rank each classification separately. Their rows are not matched pairs. Descriptive model names are interpretation aids; the explorer retains its keyword labels.</p></section>
    </div>
    <p class="comparison-source-note">${esc(period.source_note)} ${cite(period, `${period.source.authors} (${period.source.year}), ${period.source.locator}`)}.</p>
    <details class="comparison-crosswalk"><summary>Category definitions &amp; crosswalk <span>${period.categories.length} editorial categories</span></summary>
      <p>Relationship labels describe the model’s scope relative to the editorial category. A close match is thematic, not a verified article-by-article match. <strong>Mappings overlap:</strong> a model topic can appear in several rows; its count must never be added across those rows. Counts below describe each entire topic, not the intersection with an editorial category. Category scope descriptions paraphrase the sources.</p>
      <div class="crosswalk-scroll" tabindex="0" role="region" aria-label="Category crosswalk, scroll horizontally on small screens"><table><caption class="sr-only">${esc(period.label)} category definitions and overlapping topic correspondences</caption><thead><tr><th scope="col">Editorial category &amp; scope</th><th scope="col">Model correspondence</th><th scope="col">Interpretation</th></tr></thead><tbody>${period.categories.map((row) => `<tr><th scope="row">${esc(row.name)}<small>${row.count} / ${period.classified_total} papers · ${pct(row.share)}</small><p>${esc(row.definition)}</p></th><td><span class="relationship ${row.relationship}">${relationLabels[row.relationship]}</span>${row.topic_ids.length ? `<ul>${row.topic_ids.map((id) => {const model = modelById.get(id); return `<li>${esc(model.name)}<small>${model.count} / ${c.abstract_count} abstracts · ${pct(model.share)}</small></li>`;}).join("")}</ul>` : '<p>No direct counterpart. The theme may occur within other topics.</p>'}</td><td>${esc(row.note)}</td></tr>`).join("")}</tbody></table></div>
    </details>
    <details class="comparison-examples"><summary>Read the papers behind the interpretation</summary><p>Period-specific examples checked against the full abstracts, selected near their topic’s embedding centre. These illustrate topic scope; they are not a validation sample of the editorial assignments. Selecting a paper opens its complete abstract on the Articles page with the matching theme selected.</p>${!data.links_enabled ? '<p class="comparison-caution">Article links are disabled because these reviewed examples belong to the earlier model run.</p>' : ""}<div class="comparison-example-grid">${period.model_topics.filter((item) => item.examples.length).map((item) => `<div><h4>${esc(item.name)}</h4><ul>${item.examples.map((paper) => `<li><button type="button" class="comparison-paper" data-comparison-handle="${esc(paper.handle)}" ${data.links_enabled ? "" : "disabled"}>${esc(paper.title)} <span>${esc(paper.year)} · ${esc(paper.authors.join(", "))} · Read abstract ↗</span></button></li>`).join("")}</ul></div>`).join("")}</div></details>`;
}

export function mountComparison(container, data, onPaper) {
  if (!data || data.status === "not_built") { container.hidden = true; return; }
  container.hidden = false;
  if (!["ready", "review_required"].includes(data.status)) {
    container.innerHTML = `<h2 id="comparison-title">The retrospectives &amp; the model</h2><p class="topic-notice">${esc(data.message || "The retrospective comparison is temporarily unavailable.")}</p>`;
    return;
  }
  const date = new Date(data.audited_model.built_at).toLocaleDateString(undefined, {day: "numeric", month: "long", year: "numeric"});
  container.innerHTML = `<div class="comparison-heading"><p class="eyebrow">Three decades, two perspectives</p><h2 id="comparison-title">The retrospectives &amp; the model</h2><p class="comparison-dateline">Comparison audited against the model of ${esc(date)} · <code>${esc(data.audited_model.run_id)}</code></p></div>
    ${data.status === "review_required" ? '<p class="comparison-caution" role="status"><strong>Review needed after a model rebuild.</strong> This dated comparison retains its audited model’s counts and interpretation. The explorer above uses a newer model. Topic mappings and article links are disabled until the comparison is reviewed against that run.</p>' : ""}
    ${narrative(data)}
    <div class="comparison-period-selector" role="group" aria-label="Choose a retrospective period">${data.periods.map((period) => `<button type="button" data-period="${esc(period.id)}" aria-pressed="false">${esc(period.label)}<span>${esc(period.years)}${period.partial ? " · partial sample" : ""}</span></button>`).join("")}</div>
    <div id="comparison-period-content"></div>
    <details class="comparison-method"><summary>Comparison method &amp; sources</summary><p>${esc(data.method.basis)} ${esc(data.method.model_denominator)} ${esc(data.method.editorial_denominator)}</p><p>${esc(data.method.crosswalk)} ${esc(data.method.limits)}</p><p>${esc(data.method.review || "")}</p><p>Periods follow publication volumes and issues, not years discussed within an abstract. Volumes 31 onward are outside these comparisons. Updating this comparison did not refit the published model. The model and comparison code make no GPT or hosted inference API calls.</p><ol class="comparison-references">${data.periods.map((period) => `<li>${esc(period.source.authors)} (${period.source.year}). <cite>${esc(period.source.title)}</cite>. ${esc(period.source.locator)}. <a href="${doiUrl(period.source)}" target="_blank" rel="noopener">DOI: ${esc(period.source.doi)}</a>.</li>`).join("")}</ol></details>`;
  function selectPeriod(id) {
    const period = data.periods.find((item) => item.id === id);
    if (!period) return;
    container.querySelectorAll("button[data-period]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.period === id)));
    container.querySelector("#comparison-period-content").innerHTML = periodContent(period, data);
  }
  container.addEventListener("click", (event) => {
    const periodButton = event.target.closest("button[data-period]");
    if (periodButton) selectPeriod(periodButton.dataset.period);
    const paperButton = event.target.closest("button[data-comparison-handle]");
    if (paperButton && data.links_enabled && !paperButton.disabled) onPaper(paperButton.dataset.comparisonHandle);
  });
  selectPeriod("third");
}
