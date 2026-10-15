import {escapeHtml} from "./article-ui.js";
import {dataClient} from "./data-client.js";

const $ = (selector) => document.querySelector(selector);
const esc = (value) => escapeHtml(String(value ?? ""));
const palette = ["#237b7b", "#c66b46", "#758945", "#6d70a3", "#b28530", "#a25778", "#4a8eae", "#9b5f3e", "#4e8c6b", "#b95d56", "#767b39", "#906ca4", "#388f96", "#d18c59", "#6085ad", "#b27996", "#809b72", "#ac944a", "#59757d", "#9b7770"];
const metrics = {in_degree: "citations in", out_degree: "citations out", degree: "citations in + out"};
const state = {data: null, community: null, paper: null, ranking: "in_degree", page: 1, pageSize: 25, rankedNodes: [], firstYear: 0, lastYear: 0, edgeSets: {}, nodeDisplay: new Map(), maxima: {}};
const DEFAULT_MIN_CITATIONS = 3;
const colour = (id) => id === -1 ? "#a1a9aa" : palette[id % palette.length];
const label = (id) => id === -1 ? "No observed internal links" : state.data.communities.find((c) => c.id === id)?.label || "Unknown community";
const config = {responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ["select2d", "lasso2d"]};
const font = {family: '"DM Sans", sans-serif', color: "#557077", size: 11};
let pendingView = null, framePending = false, drawing = false, lastViewKey = null, selectionKey = null, rankingKey = null;

function articleUrl(node) {
  return `../?${new URLSearchParams({volume: node.volume, issue: node.issue, article: node.handle})}`;
}

function selectedEdges() {
  return state.edgeSets[$("#citation-edges").value === "baseline" ? "baseline" : "recovered"];
}

function citationMetric() {
  const metric = $("#citation-count-type").value;
  return Object.hasOwn(metrics, metric) ? metric : "in_degree";
}

function minimumCitations() {
  const count = Number($("#citation-minimum").value);
  return Number.isFinite(count) ? Math.max(0, Math.round(count)) : DEFAULT_MIN_CITATIONS;
}

function updateSliders() {
  const metric = citationMetric(), minimum = $("#citation-minimum");
  minimum.max = String(state.maxima[metric]);
  minimum.value = String(Math.min(minimumCitations(), Number(minimum.max)));
  $("#citation-minimum-label").textContent = `Minimum ${metrics[metric]}`;
  $("#citation-minimum-value").textContent = minimum.value;
  minimum.setAttribute("aria-valuetext", `${minimum.value} ${metrics[metric]}`);
  for (const end of ["from", "to"]) {
    const slider = $(`#citation-${end}`);
    $(`#citation-${end}-value`).textContent = slider.value;
    slider.setAttribute("aria-valuetext", slider.value);
  }
}

function filteredNodes() {
  const from = Number($("#citation-from").value), to = Number($("#citation-to").value);
  const metric = citationMetric(), minimum = minimumCitations();
  return state.data.nodes.filter((node) => (state.community === null || node.community === state.community)
    && Number(node.year) >= from && Number(node.year) <= to && node[metric] >= minimum);
}

function communityButtons() {
  const entries = [{id: "all", label: "All communities", count: state.data.nodes.length}, ...state.data.communities,
    {id: -1, label: "No observed internal links", count: state.data.metadata.isolated_count}];
  $("#citation-communities").innerHTML = entries.map((c) => `<button type="button" class="topic-button" data-community="${c.id}" aria-pressed="false"><span class="topic-swatch" style="background:${c.id === "all" ? "#174a4f" : colour(c.id)}"></span><span class="topic-label">${esc(c.label)}</span><span class="topic-count">${c.count}</span></button>`).join("");
  $("#citation-communities").addEventListener("click", (event) => {
    const button = event.target.closest("[data-community]");
    if (!button) return;
    state.community = button.dataset.community === "all" ? null : Number(button.dataset.community);
    if (state.community === -1) $("#citation-minimum").value = "0";
    state.paper = null; render();
  });
}

function resetFilters({minimum = DEFAULT_MIN_CITATIONS} = {}) {
  state.community = null; state.paper = null; state.ranking = "in_degree";
  $("#citation-from").value = String(state.firstYear); $("#citation-to").value = String(state.lastYear);
  $("#citation-count-type").value = "in_degree"; $("#citation-minimum").value = String(minimum);
}

function selectPaper(handle) {
  if (!state.data.nodes.some((node) => node.handle === handle)) return;
  if (!filteredNodes().some((node) => node.handle === handle)) resetFilters({minimum: 0});
  state.paper = handle;
  render();
  const page = Math.floor(state.rankedNodes.findIndex((node) => node.handle === handle) / state.pageSize) + 1;
  if (page !== state.page) { state.page = page; renderRankingsPage(); }
}

function updateRankings(nodes) {
  const key = [state.community, citationMetric(), minimumCitations(), $("#citation-from").value, $("#citation-to").value, state.ranking].join("|");
  if (key === rankingKey) return;
  rankingKey = key;
  const metric = state.ranking;
  state.rankedNodes = [...nodes].sort((a, b) => b[metric] - a[metric] || b.degree - a.degree || (a.handle < b.handle ? -1 : a.handle > b.handle ? 1 : 0));
  state.page = 1;
  renderRankingsPage();
}

function renderRankingsPage() {
  const metric = state.ranking;
  const total = state.rankedNodes.length, pages = Math.max(1, Math.ceil(total / state.pageSize));
  state.page = Math.max(1, Math.min(pages, state.page));
  const offset = (state.page - 1) * state.pageSize, papers = state.rankedNodes.slice(offset, offset + state.pageSize);
  $("#citation-ranking-caption").textContent = `Papers ranked by ${metrics[metric]} · current filters`;
  $("#citation-ranking-status").textContent = total ? `Showing ${offset + 1}–${offset + papers.length} of ${total.toLocaleString()} matching papers` : "No papers match the current filters.";
  $("#citation-page").value = String(state.page);
  $("#citation-page").max = String(pages);
  $("#citation-page").disabled = pages === 1;
  $("#citation-page-total").textContent = `of ${pages}`;
  $("#citation-page-previous").disabled = state.page === 1;
  $("#citation-page-next").disabled = state.page === pages;
  document.querySelectorAll("[data-ranking]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.ranking === metric)));
  document.querySelectorAll("[data-ranking-column]").forEach((column) => column.setAttribute("aria-sort", column.dataset.rankingColumn === metric ? "descending" : "none"));
  $("#citation-ranking-rows").innerHTML = papers.map((node, i) => `<tr data-selected="${node.handle === state.paper}"><td>${offset + i + 1}</td><td><button type="button" aria-pressed="${node.handle === state.paper}" data-paper="${esc(node.handle)}">${esc(node.title)}</button><small>${esc(node.authors.join(", "))} · ${esc(node.year)}</small><a class="citation-reader-link" href="${esc(articleUrl(node))}">Read abstract ↗</a></td>${["in_degree", "out_degree", "degree"].map((count) => `<td${metric === count ? ' class="ranking-active"' : ""}>${node[count]}</td>`).join("")}</tr>`).join("") || '<tr><td colspan="5">No papers match these filters. Choose another community or widen the citation count and year range.</td></tr>';
  $(".citation-table-scroll").scrollTop = 0;
}

function renderSelection() {
  const node = state.data.nodes.find((n) => n.handle === state.paper);
  $("#citation-clear-paper").hidden = !node;
  if (!node) {
    const community = state.data.communities.find((c) => c.id === state.community);
    $("#citation-selection").innerHTML = community ? `<h3>${esc(community.label)}</h3><p>${esc(community.interpretation)}</p><p>${community.count} papers in the whole study.</p>` : "<p>Select a paper on the map or in the ranking table to explore its incoming and outgoing citations.</p>";
    return;
  }
  const edges = selectedEdges(), lookup = new Map(state.data.nodes.map((n) => [n.handle, n]));
  const incoming = edges.filter((e) => e.target === node.handle).map((e) => lookup.get(e.source));
  const outgoing = edges.filter((e) => e.source === node.handle).map((e) => lookup.get(e.target));
  const relations = (papers, title, color) => `<details open><summary style="color:${color}">${title} (${papers.length})</summary><ul>${papers.sort((a, b) => Number(b.year) - Number(a.year) || a.handle.localeCompare(b.handle)).map((paper) => `<li><button type="button" data-paper="${esc(paper.handle)}">${esc(paper.title)} (${esc(paper.year)})</button></li>`).join("") || "<li>No observed links in this direction.</li>"}</ul></details>`;
  $("#citation-selection").innerHTML = `<h3>${esc(node.title)}</h3><p>${esc(node.authors.join(", "))} · ${esc(node.year)} · ${esc(label(node.community))}</p><a href="${esc(articleUrl(node))}">Read the complete abstract on Articles ↗</a><p>Whole recovered network: cited by ${node.in_degree} papers · cites ${node.out_degree} papers. Lists below use ${$("#citation-edges").value === "baseline" ? "DOI-only" : "recovered"} links across all study years.</p>${!node.identity_verified ? "<p>This paper’s DOI identity remains unresolved; its outgoing references are unavailable.</p>" : ""}<div class="citation-relations">${relations(incoming, "Cites this paper", "#237b7b")}${relations(outgoing, "Cited by this paper", "#c66b46")}</div>`;
}

function prepareNetwork() {
  const nodes = state.data.nodes, lookup = new Map(nodes.map((node) => [node.handle, node]));
  for (const metric of Object.keys(metrics)) state.maxima[metric] = Math.max(0, ...nodes.map((node) => node[metric]));
  state.nodeDisplay = new Map(nodes.map((node) => [node.handle, {
    hover: [node.handle, esc(node.title), esc(node.year), node.in_degree, node.out_degree, esc(label(node.community))],
    size: Math.min(35, 4 + Math.sqrt(node.degree + 1) * 2), colour: colour(node.community),
  }]));
  const xs = nodes.map((node) => node.x), ys = nodes.map((node) => node.y);
  const span = nodes.length ? Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), .1) : 1;
  // Cache arrow geometry once. Every edge and its arrowhead share one WebGL line batch.
  // Separate Plotly annotations create hundreds of SVG elements on every slider update.
  state.edgeSets.recovered = state.data.edges.map((edge) => {
    const s = lookup.get(edge.source), t = lookup.get(edge.target);
    const dx = t.x - s.x, dy = t.y - s.y, length = Math.hypot(dx, dy);
    const x = [s.x, t.x, null], y = [s.y, t.y, null];
    if (length > 0) {
      const ux = dx / length, uy = dy / length, size = Math.min(length * .15, span * .005);
      const tipX = s.x + dx * .75, tipY = s.y + dy * .75;
      const backX = tipX - ux * size, backY = tipY - uy * size, wing = size * .55;
      x.push(backX - uy * wing, tipX, backX + uy * wing, null);
      y.push(backY + ux * wing, tipY, backY - ux * wing, null);
    }
    return {...edge, x, y};
  });
  state.edgeSets.baseline = state.edgeSets.recovered.filter((edge) => edge.baseline);
}

async function drawNetwork({nodes, edges, paper}) {
  if (!window.Plotly) throw new Error("Chart library unavailable");
  const groups = [[], [], []];
  for (const edge of edges) groups[edge.target === paper ? 1 : edge.source === paper ? 2 : 0].push(edge);
  const lineTrace = (group, uid, name, color, width) => {
    const x = [], y = [];
    for (const edge of group) { x.push(...edge.x); y.push(...edge.y); }
    return {uid, type: "scattergl", mode: "lines", x, y, name, hoverinfo: "skip", line: {color, width}, showlegend: !!paper && group.length > 0, connectgaps: false};
  };
  // Keep five stable traces, rather than adding/removing a trace for every community.
  const traces = [
    lineTrace(groups[0], "citations-other", "Other citations", paper ? "rgba(45,89,94,.22)" : "rgba(45,89,94,.55)", paper ? 1 : 1.4),
    lineTrace(groups[1], "citations-in", "Cites this paper", "#237b7b", 2.5),
    lineTrace(groups[2], "citations-out", "Cited by this paper", "#c66b46", 2.5),
    {uid: "citation-papers", type: "scattergl", mode: "markers", name: "Papers", showlegend: false,
      x: nodes.map((node) => node.x), y: nodes.map((node) => node.y), customdata: nodes.map((node) => state.nodeDisplay.get(node.handle).hover),
      marker: {size: nodes.map((node) => state.nodeDisplay.get(node.handle).size), color: nodes.map((node) => state.nodeDisplay.get(node.handle).colour), opacity: .85, line: {width: .4, color: "white"}},
      hovertemplate: "%{customdata[1]}<br>%{customdata[2]} · Cited by %{customdata[3]} · Cites %{customdata[4]}<br>%{customdata[5]}<extra></extra>"},
  ];
  const focused = nodes.find((node) => node.handle === paper);
  traces.push({uid: "citation-selected", type: "scattergl", mode: "markers", x: focused ? [focused.x] : [], y: focused ? [focused.y] : [], customdata: focused ? [[focused.handle]] : [], showlegend: false, hoverinfo: "skip",
    marker: {size: focused ? Math.min(42, 12 + Math.sqrt(focused.degree + 1) * 2) : 0, symbol: "circle-open", color: "#142f35", line: {width: 2}}});
  await Plotly.react($("#citation-network"), traces, {margin: {l: 5, r: 5, t: 8, b: 5}, font, paper_bgcolor: "#fff", plot_bgcolor: "#fff", dragmode: "pan", hovermode: "closest", uirevision: state.data.metadata.run_id,
    xaxis: {visible: false}, yaxis: {visible: false, scaleanchor: "x", scaleratio: 1}, legend: {orientation: "h", x: 0, y: 1.08, font: {size: 9}}}, config);
  const plot = $("#citation-network");
  if (!plot.citationClickBound && typeof plot.on === "function") {
    plot.on("plotly_click", (event) => { const handle = event.points?.[0]?.customdata?.[0]; if (handle) selectPaper(handle); });
    plot.citationClickBound = true;
  }
}

function scheduleNetwork(view) {
  pendingView = view;
  $("#citation-network").setAttribute("aria-busy", "true");
  if (drawing || framePending) return;
  framePending = true;
  window.requestAnimationFrame(async () => {
    framePending = false;
    const latest = pendingView;
    pendingView = null; drawing = true;
    try {
      await drawNetwork(latest);
    } catch {
      $("#citation-status").hidden = false;
      $("#citation-status").textContent = "The map could not be displayed. The paper rankings and article links remain available.";
    } finally {
      drawing = false;
      // At most one draw is active and one latest view is pending, even during a long drag.
      if (pendingView) scheduleNetwork(pendingView);
      else $("#citation-network").setAttribute("aria-busy", "false");
    }
  });
}

function render() {
  updateSliders();
  const nodes = filteredNodes(), handles = new Set(nodes.map((node) => node.handle));
  if (state.paper && !handles.has(state.paper)) state.paper = null;
  updateRankings(nodes);
  const key = [state.community, state.paper, citationMetric(), minimumCitations(), $("#citation-from").value, $("#citation-to").value, $("#citation-edges").value].join("|");
  if (key === lastViewKey) return;
  lastViewKey = key;
  const edges = selectedEdges().filter((edge) => handles.has(edge.source) && handles.has(edge.target));
  $("#citation-visible").textContent = `${nodes.length.toLocaleString()} visible papers · ${edges.length.toLocaleString()} visible links · ${$("#citation-from").value}–${$("#citation-to").value} · minimum ${minimumCitations()} ${metrics[citationMetric()]} · ${$("#citation-edges").value === "baseline" ? "DOI-only edges; recovered community assignments retained" : "verified recovered network"}. Totals above describe the whole study.`;
  const nextSelection = [state.community, state.paper, $("#citation-edges").value].join("|");
  if (nextSelection !== selectionKey) {
    selectionKey = nextSelection;
    document.querySelectorAll("[data-community]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.community === "all" ? state.community === null : Number(button.dataset.community) === state.community)));
    document.querySelectorAll("#citation-ranking-rows [data-paper]").forEach((button) => {
      const selected = button.dataset.paper === state.paper;
      button.setAttribute("aria-pressed", String(selected)); button.closest("tr").dataset.selected = String(selected);
    });
    renderSelection();
  }
  scheduleNetwork({nodes, edges, paper: state.paper});
}

function renderStudy() {
  const data = state.data, m = data.metadata;
  $("#citation-stats").innerHTML = [[m.eligible_count, "eligible papers"], [m.edge_count, "observed citation links"], [data.communities.length, "citation communities"], [m.isolated_count, "with no observed links"]].map(([count, text]) => `<div><strong>${count.toLocaleString()}</strong><span>${text}</span></div>`).join("");
  $("#citation-date").textContent = `${m.year_from}–${m.year_to} catalogue coverage · retrieved ${m.retrieved_to?.slice(0, 10) || "date unavailable"}`;
  $("#citation-stale").hidden = !data.stale;
  $("#citation-scope").textContent = `${m.article_count.toLocaleString()} catalogue records; ${m.excluded_count} excluded from the study. Pre-1997 articles are outside this catalogue. Reference deposits may be incomplete; a paper with no observed links may still cite, or be cited by, other DPE papers. Citation links alone do not establish agreement or debate.`;
  const years = data.nodes.map((node) => Number(node.year));
  state.firstYear = years.length ? Math.min(...years) : m.year_from;
  state.lastYear = years.length ? Math.max(...years) : m.year_to;
  for (const end of ["from", "to"]) {
    const slider = $(`#citation-${end}`);
    slider.min = String(state.firstYear); slider.max = String(state.lastYear);
    slider.value = String(end === "from" ? state.firstYear : state.lastYear);
  }
  prepareNetwork(); communityButtons();
}

function bindControls() {
  for (const event of ["input", "change"]) $("#citation-minimum").addEventListener(event, render);
  $("#citation-count-type").addEventListener("change", () => { state.ranking = citationMetric(); render(); });
  $("#citation-edges").addEventListener("change", render);
  for (const end of ["from", "to"]) for (const event of ["input", "change"]) $(`#citation-${end}`).addEventListener(event, () => {
    if (Number($("#citation-from").value) > Number($("#citation-to").value)) {
      $(end === "from" ? "#citation-to" : "#citation-from").value = $(`#citation-${end}`).value;
    }
    render();
  });
  $("#citation-reset").addEventListener("click", () => { resetFilters(); $("#citation-edges").value = "recovered"; render(); });
  $("#citation-clear-paper").addEventListener("click", () => { state.paper = null; render(); });
  $("#citation-ranking-controls").addEventListener("click", (event) => {
    const button = event.target.closest("[data-ranking]");
    if (button && Object.hasOwn(metrics, button.dataset.ranking)) { state.ranking = button.dataset.ranking; updateRankings(filteredNodes()); }
  });
  $("#citation-page-previous").addEventListener("click", () => { state.page--; renderRankingsPage(); });
  $("#citation-page-next").addEventListener("click", () => { state.page++; renderRankingsPage(); });
  $("#citation-page").addEventListener("change", () => {
    const value = Number($("#citation-page").value);
    if (Number.isInteger(value)) state.page = value;
    renderRankingsPage();
  });
  $("#citation-page-size").addEventListener("change", () => {
    const value = Number($("#citation-page-size").value);
    if (![25, 50, 100].includes(value)) return;
    state.pageSize = value; state.page = 1; renderRankingsPage();
  });
  for (const id of ["#citation-ranking-rows", "#citation-selection"]) $(id).addEventListener("click", (event) => { const target = event.target.closest("[data-paper]"); if (target) selectPaper(target.dataset.paper); });
}

async function load() {
  try {
    state.data = await dataClient.getCitations();
    if (state.data.status !== "ready") { $("#citation-status").textContent = (state.data.message || "The citation analysis is being prepared.") + " Articles and Topics remain available."; return; }
    renderStudy(); bindControls();
    $("#citation-explorer").hidden = false; $("#citation-status").hidden = true;
    render();
  } catch {
    $("#citation-status").hidden = false;
    $("#citation-status").innerHTML = 'The citation explorer is temporarily unavailable. Please try again.<br><button id="citation-retry" type="button">Retry</button>';
    $("#citation-retry").addEventListener("click", () => window.location.reload());
  }
}

load();
