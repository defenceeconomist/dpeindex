import {escapeHtml as esc} from "./article-ui.js";

const config = {responsive: true, displaylogo: false, scrollZoom: false};
const percent = (value) => value === null ? "No abstracts" : `${value.toFixed(1)}%`;
const shorten = (label, length = 30) => label.length > length ? label.slice(0, length - 1) + "…" : label;

export async function mountTimeline(container, data, colour, onSelect) {
  const timeline = data.timeline;
  if (timeline?.status !== "ready") {
    container.innerHTML = `<h2 id="timeline-title">Themes over time</h2><p class="topic-help">${esc(timeline?.message || "Topics-over-time analysis is being prepared for this model.")}</p>`;
    return null;
  }
  const topics = [...data.topics.map((topic) => ({id: topic.id, label: topic.label})), {id: -1, label: "Unassigned abstracts"}];
  let grouping = "five_years", measure = "share", selection = null;
  let selectedStart = timeline.groups[grouping].periods.at(-1).start;
  const $ = (selector) => container.querySelector(selector);
  container.innerHTML = `<div class="chart-heading"><div><p class="eyebrow">The conversation in motion</p><h2 id="timeline-title">Themes over time</h2></div></div>
    <p class="chart-description">Follow the same themes across publication years. Compare their share of available abstracts or their article counts, then inspect each period’s changing keywords. Time controls apply to this section; the map and hierarchy show the full model.</p>
    <p class="timeline-coverage">Available catalog: ${timeline.first_year}–${timeline.last_year}. Early coverage is incomplete, and the latest grouped period may be partial. Changes describe the available abstracts, rather than the journal’s complete historical output. ${timeline.undated_abstract_count ? `${timeline.undated_abstract_count} abstracts without a usable publication year are excluded here.` : ""}</p>
    <div class="timeline-controls"><div><label for="timeline-group">Time intervals</label><select id="timeline-group"><option value="year">Annual</option><option value="five_years" selected>Five-year periods</option><option value="decade">Decades</option></select></div><div><label for="timeline-measure">Measure</label><select id="timeline-measure"><option value="share">Share of abstracts (%)</option><option value="count">Number of abstracts</option></select></div><div><label for="timeline-theme">Highlight theme</label><select id="timeline-theme"><option value="all">All themes</option>${topics.map((topic) => `<option value="${topic.id}">${esc(topic.label)}</option>`).join("")}</select></div></div>
    <p id="timeline-status" class="topic-help" role="status"></p>
    <div class="timeline-chart-panel"><h3>How prominence changes</h3><p class="chart-description">Select a point to inspect that theme and period. Unassigned abstracts are included in every share denominator.</p><div id="theme-trends" class="theme-trends" role="img" aria-label="Topic prevalence over publication periods"></div></div>
    <div class="timeline-chart-panel"><h3>The changing research mix</h3><p class="chart-description">Darker cells indicate a larger share or count. Select a cell to inspect its keywords. Empty periods remain gaps.</p><div id="theme-heatmap" class="theme-heatmap" role="img" aria-label="Heatmap of themes by publication period"></div></div>
    <div class="timeline-period-control"><label for="timeline-period">Inspect a period</label><select id="timeline-period"></select></div>
    <p id="timeline-period-summary" class="topic-help" aria-live="polite"></p>
    <div class="timeline-table-scroll" tabindex="0" role="region" aria-label="Theme counts and period keywords"><table class="timeline-table"><caption id="timeline-table-caption">Period-specific topic keywords</caption><thead><tr><th scope="col">Theme</th><th scope="col">Abstracts</th><th scope="col">Share</th><th scope="col">Period keywords</th><th scope="col">Articles</th></tr></thead><tbody id="timeline-words"></tbody></table></div>
    <details class="timeline-method"><summary>How this dynamic view is calculated</summary><p>${esc(timeline.method.assignments)} ${esc(timeline.method.denominator)} Missing abstracts are shown separately; they do not enter the denominator. Zero counts are distinguished from periods with no usable abstracts. These calendar intervals differ from the retrospective comparison’s volume and issue cutoffs.</p><p>${esc(timeline.method.keywords)} Keyword smoothing does not change article counts or assignments. These words describe a topic’s changing emphasis, rather than newly discovered or disappearing clusters.</p><p>The analysis uses full abstracts with English stopwords removed only for keyword extraction. No GPT or hosted inference API is used. Method: <a href="https://maartengr.github.io/BERTopic/getting_started/topicsovertime/topicsovertime.html" target="_blank" rel="noopener">BERTopic topics over time</a>. Analysis built ${esc(new Date(timeline.built_at).toLocaleDateString())} for run <code>${esc(timeline.run_id)}</code>.</p></details>`;

  function current() {
    const group = timeline.groups[grouping];
    return {...group, byCell: new Map(group.cells.map((cell) => [`${cell.start}:${cell.topic}`, cell]))};
  }

  function periodOptions() {
    const group = current();
    if (!group.periods.some((period) => period.start === selectedStart)) selectedStart = group.periods.at(-1).start;
    $("#timeline-period").innerHTML = group.periods.map((period) => `<option value="${period.start}">${esc(period.label)}${period.partial ? " · partial" : ""}</option>`).join("");
    $("#timeline-period").value = String(selectedStart);
  }

  function renderWords() {
    const {periods, byCell} = current();
    const period = periods.find((item) => item.start === selectedStart);
    $("#timeline-table-caption").textContent = `Period-specific topic keywords · ${period.label}`;
    $("#timeline-period-summary").textContent = `${period.label}${period.partial ? " · partial period" : ""}: ${period.abstract_count} usable abstracts, ${period.missing_abstract_count} missing abstracts; ${byCell.get(`${period.start}:-1`).count} unassigned. ${period.observed_years.length ? `Observed years: ${period.observed_years.join(", ")}.` : "No catalog records in this period."}`;
    const shown = topics.filter((topic) => selection === null || selection.has(topic.id));
    $("#timeline-words").innerHTML = shown.map((topic) => {
      const cell = byCell.get(`${period.start}:${topic.id}`);
      const params = new URLSearchParams({theme: topic.id, run: data.metadata.run_id, year_from: period.start, year_to: period.end});
      return `<tr><th scope="row"><span class="topic-swatch" style="background:${colour(topic.id)}"></span>${esc(topic.label)}</th><td>${cell.count} / ${period.abstract_count}</td><td>${percent(cell.share)}</td><td>${cell.count ? esc(cell.words.join(" · ") || "No keywords available") : "No assigned abstracts"}</td><td>${cell.count ? `<a href="../?${esc(params.toString())}">Browse papers ↗</a>` : "—"}</td></tr>`;
    }).join("");
  }

  async function draw() {
    const {periods, byCell} = current();
    const labels = periods.map((period) => period.label);
    const selected = (id) => selection === null || selection.has(id);
    const font = {family: '"DM Sans", sans-serif', color: "#557077", size: 11};
    const trends = topics.map((topic) => ({type: "scatter", mode: "lines+markers", name: topic.label,
      x: labels, y: periods.map((period) => { const cell = byCell.get(`${period.start}:${topic.id}`); return cell.share === null ? null : cell[measure]; }),
      customdata: periods.map((period) => {const cell = byCell.get(`${period.start}:${topic.id}`); return [topic.id, period.start, cell.count, period.abstract_count, esc(cell.words.join(", ") || "No assigned abstracts"), esc(topic.label)];}),
      line: {color: colour(topic.id), width: selected(topic.id) ? 2.4 : 1, dash: topic.id === -1 ? "dot" : "solid"},
      marker: {size: selected(topic.id) ? 7 : 4}, opacity: selected(topic.id) ? 1 : .12, connectgaps: false,
      hovertemplate: `%{customdata[5]}<br>%{x}: %{y}${measure === "share" ? "%" : " abstracts"}<br>%{customdata[2]} / %{customdata[3]} usable abstracts<br>%{customdata[4]}<extra></extra>`}));
    const mobile = window.matchMedia("(max-width: 680px)").matches;
    await Plotly.react($("#theme-trends"), trends, {margin: {l: 52, r: 20, t: 25, b: grouping === "year" ? 70 : 50},
      paper_bgcolor: "#fff", plot_bgcolor: "#fff", font, showlegend: false, hovermode: "closest",
      xaxis: {type: "category", tickangle: grouping === "year" ? -45 : 0, nticks: mobile ? 6 : 12},
      yaxis: {title: {text: measure === "share" ? "Share of usable abstracts (%)" : "Abstracts", font: {size: 10}}, rangemode: "tozero", gridcolor: "#edf1ef"},
      uirevision: `${timeline.run_id}:${grouping}:${measure}`}, config);
    const custom = topics.map((topic) => periods.map((period) => { const cell = byCell.get(`${period.start}:${topic.id}`); return [topic.id, period.start, cell.count, period.abstract_count, esc(topic.label), esc(cell.words.join(", ") || "No assigned abstracts")]; }));
    await Plotly.react($("#theme-heatmap"), [{type: "heatmap", x: labels, y: topics.map((topic) => String(topic.id)),
      z: topics.map((topic) => periods.map((period) => {const cell = byCell.get(`${period.start}:${topic.id}`); return cell.share === null ? null : cell[measure];})), customdata: custom,
      colorscale: [[0, "#f2f5f0"], [.3, "#a5c9bd"], [.65, "#4b9590"], [1, "#174a50"]], zmin: 0, hoverongaps: false, xgap: 2, ygap: 2,
      colorbar: {title: {text: measure === "share" ? "%" : "n"}, thickness: 10, len: .85},
      hovertemplate: `%{customdata[4]}<br>%{x}: %{z}${measure === "share" ? "%" : " abstracts"}<br>%{customdata[2]} / %{customdata[3]} usable abstracts<br>%{customdata[5]}<extra></extra>`}],
    {margin: {l: mobile ? 112 : 210, r: 45, t: 15, b: 65}, paper_bgcolor: "#fff", plot_bgcolor: "#fff", font,
      xaxis: {type: "category", nticks: mobile ? 5 : 12, tickangle: grouping === "year" ? -45 : 0},
      yaxis: {type: "category", autorange: "reversed", tickmode: "array", tickvals: topics.map((topic) => String(topic.id)),
        ticktext: topics.map((topic) => esc(shorten(topic.label.replaceAll(" · ", ", "), mobile ? 17 : 31))), tickfont: {size: mobile ? 9 : 10}},
      uirevision: `${timeline.run_id}:${grouping}:${measure}`}, config);
  }

  function safeDraw() { draw().catch(() => { $("#timeline-status").textContent = "A time chart could not be displayed. The period table and controls remain available."; }); }
  function inspect(event) {
    const info = event.points?.find((point) => point.customdata)?.customdata;
    if (!info) return;
    selectedStart = Number(info[1]);
    $("#timeline-period").value = String(selectedStart);
    onSelect(Number(info[0]));
    renderWords();
  }
  $("#timeline-group").addEventListener("change", (event) => { grouping = event.target.value; selectedStart = Math.floor(selectedStart / timeline.groups[grouping].width) * timeline.groups[grouping].width; periodOptions(); renderWords(); safeDraw(); });
  $("#timeline-measure").addEventListener("change", (event) => { measure = event.target.value; safeDraw(); });
  $("#timeline-theme").addEventListener("change", (event) => onSelect(event.target.value === "all" ? null : Number(event.target.value)));
  $("#timeline-period").addEventListener("change", (event) => { selectedStart = Number(event.target.value); renderWords(); });
  periodOptions(); renderWords();
  await draw();
  $("#theme-trends").on("plotly_click", inspect);
  $("#theme-heatmap").on("plotly_click", inspect);
  return {setSelection(value) {
    selection = value;
    $("#timeline-theme").value = value?.size === 1 ? String([...value][0]) : "all";
    $("#timeline-status").textContent = value && value.size > 1 ? `${value.size} related themes highlighted.` : "";
    renderWords(); safeDraw();
  }};
}
