import {escapeHtml} from "./article-ui.js";
import {mountComparison} from "./retrospectives.js";
import {mountTimeline} from "./topic-timeline.js";

const $ = (selector) => document.querySelector(selector);
const palette = ["#237b7b", "#c66b46", "#758945", "#6d70a3", "#b28530", "#a25778", "#4a8eae", "#9b5f3e", "#4e8c6b", "#b95d56", "#767b39", "#906ca4", "#388f96", "#d18c59", "#6085ad", "#b27996", "#809b72", "#ac944a", "#59757d", "#9b7770"];
const state = {data: null, selection: null, selectedHandle: null, nodeId: null, label: "All topics", timeline: null};
const plotConfig = {responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"]};
const plotFont = {family: '"DM Sans", sans-serif', color: "#557077", size: 11};
const colour = (topic) => topic === -1 ? "#a1a9aa" : palette[state.data.topics.findIndex((item) => item.id === topic) % palette.length];
const topicById = (id) => state.data.topics.find((topic) => topic.id === id);
const isSelected = (topic) => state.selection === null || state.selection.has(topic);
const short = (text, length) => text.length > length ? text.slice(0, length - 1) + "…" : text;

function renderTopicControls() {
  const button = (id, label, count, color) => `<button type="button" class="topic-button" data-topic="${id}" aria-pressed="false"><span class="topic-swatch" style="background:${color}"></span><span class="topic-label">${escapeHtml(label)}</span><span class="topic-count">${count}</span></button>`;
  $("#topic-list").innerHTML = button("all", "All topics", state.data.documents.length, "#174a4f") +
    state.data.topics.map((topic) => button(topic.id, topic.label, topic.count, colour(topic.id))).join("") +
    button(-1, "Unassigned", state.data.metadata.outliers.after, colour(-1));
  const nodes = new Map(state.data.hierarchy.map((node) => [node.id, node]));
  const children = new Set(state.data.hierarchy.flatMap((node) => node.children));
  const root = state.data.hierarchy.find((node) => !children.has(node.id));
  function branch(node) {
    const count = state.data.documents.filter((paper) => node.topics.includes(paper.topic)).length;
    return `<li><button type="button" data-node="${escapeHtml(node.id)}">${escapeHtml(node.label)} <span>(${count} papers)</span></button>${node.children.length ? `<ul>${node.children.map((id) => branch(nodes.get(id))).join("")}</ul>` : ""}</li>`;
  }
  $("#hierarchy-list").innerHTML = `<ul>${branch(root)}</ul>`;
  $("#topic-list").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-topic]");
    if (!button) return;
    if (button.dataset.topic === "all") selectTopics(null, null, "All topics");
    else {
      const id = Number(button.dataset.topic);
      selectTopics(new Set([id]), id === -1 ? null : String(id), id === -1 ? "Unassigned abstracts" : topicById(id).label);
    }
  });
  $("#hierarchy-list").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-node]");
    if (button) selectNode(button.dataset.node);
  });
}

function updateControls() {
  const selectedPaper = state.data.documents.find((paper) => paper.handle === state.selectedHandle);
  document.querySelectorAll(".topic-button").forEach((button) => {
    const selected = button.dataset.topic === "all" ? state.selection === null : state.selection?.has(Number(button.dataset.topic));
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    button.dataset.focused = String(button.dataset.topic !== "all" && Number(button.dataset.topic) === selectedPaper?.topic);
    button.title = button.dataset.focused === "true" ? "Contains the selected paper" : "";
  });
  const count = state.data.documents.filter((paper) => isSelected(paper.topic)).length;
  $("#selection-summary").textContent = `${state.label} · ${count.toLocaleString()} ${count === 1 ? "abstract" : "abstracts"}. Browse papers on the Articles page.`;
  const selected = state.selection ? state.data.topics.filter((topic) => state.selection.has(topic.id)) : [];
  $("#selection-links").innerHTML = state.selection === null ? '<a href="../">Browse all articles ↗</a>' :
    [...selected.map((topic) => `<a href="${escapeHtml(articleUrl(null, topic.id))}">${escapeHtml(topic.label)} ↗</a>`),
     ...(state.selection.has(-1) ? [`<a href="${escapeHtml(articleUrl(null, -1))}">Unassigned abstracts ↗</a>`] : [])].join("");
  const paperLink = $("#selected-article-link");
  paperLink.hidden = !selectedPaper;
  if (selectedPaper) {
    paperLink.href = articleUrl(selectedPaper);
    paperLink.textContent = `${selectedPaper.title} (${selectedPaper.year}) · Read abstract on Articles ↗`;
  }

}

function selectNode(id) {
  const node = state.data.hierarchy.find((item) => item.id === id);
  if (node) selectTopics(new Set(node.topics), node.id, node.label);
}

function articleUrl(paper = null, topic = paper?.topic) {
  const params = new URLSearchParams();
  if (topic !== undefined) { params.set("theme", topic); params.set("run", state.data.metadata.run_id); }
  if (paper) { params.set("volume", paper.volume); params.set("issue", paper.issue); params.set("article", paper.handle); }
  return `../?${params}`;
}

function selectTopics(selection, nodeId, label, paperHandle = null) {
  state.selection = selection;
  state.nodeId = nodeId;
  state.label = label;
  state.selectedHandle = paperHandle;
  updateControls();
  state.timeline?.setSelection(selection);
  Promise.all([drawMap(), drawHierarchy()]).catch(showChartError);
}

function showChartError(error) {
  console.error(error);
  $("#page-status").hidden = false;
  $("#page-status").textContent = "A chart could not be displayed. You can still explore topics and papers using the topic controls and Articles page.";
}

function mapData() {
  const ids = [...state.data.topics.map((topic) => topic.id), -1];
  const traces = ids.map((id) => {
    const papers = state.data.documents.filter((paper) => paper.topic === id);
    return {type: "scattergl", mode: "markers", name: id === -1 ? "Unassigned" : topicById(id).label,
      x: papers.map((paper) => paper.x), y: papers.map((paper) => paper.y),
      customdata: papers.map((paper) => [paper.handle, escapeHtml(paper.title), escapeHtml(paper.year), escapeHtml(id === -1 ? "Unassigned" : topicById(id).label)]),
      marker: {color: colour(id), size: id === -1 ? 5 : 7, opacity: isSelected(id) ? .82 : .10},
      hovertemplate: "%{customdata[1]}<br>%{customdata[2]} · %{customdata[3]}<extra></extra>", showlegend: false};
  });
  const selected = state.data.documents.find((paper) => paper.handle === state.selectedHandle);
  if (selected) traces.push({type: "scattergl", mode: "markers", x: [selected.x], y: [selected.y],
    customdata: [[selected.handle]], marker: {size: 16, color: colour(selected.topic), symbol: "circle-open", line: {color: "#142f35", width: 2}},
    hoverinfo: "skip", showlegend: false});
  return traces;
}

async function drawMap() {
  const annotations = state.data.topics.filter((topic) => isSelected(topic.id)).map((topic) => {
    const papers = state.data.documents.filter((paper) => paper.topic === topic.id);
    return {x: papers.reduce((total, paper) => total + paper.x, 0) / papers.length,
      y: papers.reduce((total, paper) => total + paper.y, 0) / papers.length,
      text: topic.label.split(" · ").slice(0, 2).map((word) => escapeHtml(word)).join("<br>"),
      showarrow: false, yshift: 18, font: {size: 10, color: "#142f35"}, bgcolor: "rgba(255,255,255,.83)", borderpad: 3};
  });
  await Plotly.react($("#datamap"), mapData(), {margin: {l: 12, r: 12, t: 30, b: 8},
    paper_bgcolor: "#fff", plot_bgcolor: "#fff", font: plotFont, dragmode: "pan", hovermode: "closest",
    hoverlabel: {bgcolor: "#fff", font: {size: 11}}, annotations,
    xaxis: {visible: false}, yaxis: {visible: false}, uirevision: state.data.metadata.run_id}, plotConfig);
}

function hierarchyGeometry() {
  const nodes = new Map(state.data.hierarchy.map((node) => [node.id, {...node}]));
  const children = new Set(state.data.hierarchy.flatMap((node) => node.children));
  const root = [...nodes.values()].find((node) => !children.has(node.id));
  let nextY = 0;
  const leaves = [];
  function visit(node) {
    if (!node.children.length) { node.y = nextY++; leaves.push(node); }
    else { node.children.forEach((id) => visit(nodes.get(id))); node.y = node.children.reduce((total, id) => total + nodes.get(id).y, 0) / 2; }
  }
  visit(root);
  return {nodes, root, leaves};
}

async function drawHierarchy() {
  const {nodes, leaves, root} = hierarchyGeometry();
  const x = [], y = [];
  nodes.forEach((node) => {
    if (!node.children.length) return;
    const [left, right] = node.children.map((id) => nodes.get(id));
    x.push(left.distance, node.distance, node.distance, right.distance, null);
    y.push(left.y, left.y, right.y, right.y, null);
  });
  const ordered = [...nodes.values()];
  const traces = [{type: "scatter", mode: "lines", x, y, line: {color: "#ccd9d5", width: 1.6}, hoverinfo: "skip", showlegend: false},
    {type: "scatter", mode: "markers", x: ordered.map((node) => node.distance), y: ordered.map((node) => node.y),
      customdata: ordered.map((node) => [node.id, escapeHtml(node.label), state.data.documents.filter((paper) => node.topics.includes(paper.topic)).length]),
      marker: {size: ordered.map((node) => node.id === state.nodeId ? 13 : node.children.length ? 7 : 9),
        color: ordered.map((node) => node.children.length ? "#557077" : colour(node.topics[0])),
        opacity: ordered.map((node) => state.selection === null || node.topics.some((id) => state.selection.has(id)) ? 1 : .22),
        line: {color: "#fff", width: 1}},
      hovertemplate: "%{customdata[1]}<br>%{customdata[2]} papers<extra></extra>", showlegend: false}];
  const mobile = window.matchMedia("(max-width: 680px)").matches;
  await Plotly.react($("#hierarchy"), traces, {margin: {l: mobile ? 132 : 218, r: 15, t: 22, b: 40},
    paper_bgcolor: "#fff", plot_bgcolor: "#fff", font: plotFont, hovermode: "closest",
    xaxis: {title: {text: "Topic distance", font: {size: 10}}, gridcolor: "#edf1ef", zeroline: false, range: [-root.distance * .025, root.distance * 1.06]},
    yaxis: {tickmode: "array", tickvals: leaves.map((node) => node.y),
      ticktext: leaves.map((node) => escapeHtml(short(node.label.replaceAll(" · ", ", "), mobile ? 22 : 36))),
      tickfont: {size: mobile ? 9 : 10}, showgrid: false, zeroline: false, range: [-.8, leaves.length - .2], fixedrange: true},
    uirevision: state.data.metadata.run_id}, {...plotConfig, scrollZoom: false});
}

function renderMethod() {
  const {metadata: meta, report} = state.data;
  const before = report.selected_before_reassignment, after = report.selected_after_reassignment, baseline = report.baseline;
  const metricRows = [["Topics", "topic_count"], ["Unassigned abstracts", "outliers"], ["Coherence (NPMI)", "coherence"], ["Cosine silhouette", "silhouette"], ["Keyword diversity", "diversity"]];
  const display = (value) => Number.isInteger(value) ? value : value.toFixed(3);
  $("#model-method").innerHTML = `<p>Every non-empty abstract is embedded in full using the local <strong>Qwen3-Embedding-0.6B</strong> sentence transformer. Authors and titles are used for browsing, not for fitting topics. Articles without abstracts are excluded. No GPT or hosted inference API is used.</p><p>Checked ${meta.token_coverage.checked_abstracts.toLocaleString()} abstracts: the longest was ${meta.token_coverage.max_tokens.toLocaleString()} tokens against an effective limit of ${meta.token_coverage.sequence_limit.toLocaleString()}. <strong>Zero abstracts were truncated.</strong> Stopwords are filtered only for topic keywords.</p><p>${report.trials.length ? "The parameter search compared 54 configurations. Three finalists were checked with three random seeds." : "This model was rebuilt using previously selected parameters."} Selection favours 10–20 broad themes, coherence, and separation in the original embedding space. The untuned baseline uses the same embeddings and keyword processing with default clustering parameters. The map uses its own two-dimensional projection and is not used to score clusters.</p><table><caption class="sr-only">Comparison with the untuned BERTopic baseline using the same embeddings</caption><thead><tr><th>Quality check</th><th>Untuned</th><th>Tuned</th><th>After reassignment</th></tr></thead><tbody>${metricRows.map(([name, key]) => `<tr><th>${name}</th><td>${display(baseline[key])}</td><td>${display(before[key])}</td><td>${display(after[key])}</td></tr>`).join("")}</tbody></table><p>Outlier reduction: ${meta.outliers.before} → ${meta.outliers.after}. ${meta.outliers.agreement === null ? "There were too few reliable validation matches for cautious reassignment." : `The similarity threshold achieved ${(meta.outliers.agreement * 100).toFixed(1)}% agreement on ${meta.outliers.accepted_validation} held-out confident members. This measures agreement with the discovered clusters, not independently verified accuracy.`} Ambiguous abstracts remain unassigned.</p><p>UMAP neighbours: ${meta.params.n_neighbors}; clustering dimensions: ${meta.params.n_components}; minimum cluster size: ${meta.params.min_cluster_size}; minimum samples: ${meta.params.min_samples}. Seeds: ${meta.seeds.join(", ")}.</p><p>Topic labels are extracted keywords. The hierarchy describes similarity between those keyword profiles. Topic IDs belong to this model run and may change on rebuild.</p><p>Run: <code>${escapeHtml(meta.run_id)}</code><br>Model revision: <code>${escapeHtml(meta.model_revision)}</code><br>Corpus fingerprint: <code>${escapeHtml(meta.corpus_fingerprint)}</code></p>`;
}

async function init() {
  try {
    const response = await fetch("../api/topics");
    if (!response.ok) throw new Error("The topic explorer is temporarily unavailable.");
    const data = await response.json();
    if (data.status === "not_built") {
      $("#page-status").innerHTML = "<strong>The topic explorer is being prepared.</strong><br>Once the first model is built, this page will show the journal’s research themes, a map of its abstracts, and the connections between topics.";
      return;
    }
    state.data = data;
    $("#abstract-count").textContent = data.metadata.abstract_count.toLocaleString();
    $("#theme-count").textContent = data.topics.length;
    $("#unassigned-count").textContent = data.metadata.outliers.after;
    $("#missing-count").textContent = data.metadata.missing_abstract_count;
    $("#model-date").textContent = `Model built ${new Date(data.metadata.built_at).toLocaleDateString(undefined, {day: "numeric", month: "long", year: "numeric"})}`;
    $("#stale-notice").hidden = !data.stale;
    $("#page-status").hidden = true;
    $("#explorer").hidden = false;
    renderTopicControls();
    renderMethod();
    mountComparison($("#retrospectives"), data.comparison, (handle) => {
      const paper = data.documents.find((item) => item.handle === handle);
      if (paper) location.assign(articleUrl(paper));
    });
    $("#comparison-jump").hidden = $("#retrospectives").hidden;
    $("#reset-selection").addEventListener("click", () => selectTopics(null, null, "All topics"));
    if (!window.Plotly && document.readyState !== "complete") await new Promise((resolve) => window.addEventListener("load", resolve, {once: true}));
    if (!window.Plotly) throw new Error("The charts could not be loaded. Please reload the page.");
    updateControls();
    await Promise.all([drawMap(), drawHierarchy()]);
    state.timeline = await mountTimeline($("#topic-timeline"), data, colour, (id) => {
      selectTopics(id === null ? null : new Set([id]), id === null || id === -1 ? null : String(id), id === null ? "All topics" : topicById(id)?.label || "Unassigned abstracts");
    });
    $("#datamap").on("plotly_click", (event) => {
      const handle = event.points[0]?.customdata?.[0];
      const paper = state.data.documents.find((item) => item.handle === handle);
      if (paper) selectTopics(new Set([paper.topic]), paper.topic === -1 ? null : String(paper.topic), topicById(paper.topic)?.label || "Unassigned abstracts", handle);
    });
    $("#hierarchy").on("plotly_click", (event) => {
      const nodeId = event.points.find((point) => point.customdata)?.customdata[0];
      if (nodeId) selectNode(nodeId);
    });
  } catch (error) {
    console.error(error);
    $("#page-status").hidden = false;
    $("#page-status").innerHTML = `${escapeHtml(error.message)}<br><button type="button" id="retry-load">Retry</button>`;
    $("#retry-load").addEventListener("click", () => location.reload());
  }
}

init();
