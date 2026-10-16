import {articleCard, attachDetails, escapeHtml} from "./article-ui.js";
import {dataClient} from "./data-client.js";
import {collectionTypes, collectionOptions, collectionLabel, collectionHandles} from "./collection-filter.js";
import {matchesFilters} from "./search-ranking.js";

const state = {
  collections: null, collectionType: "", collection: "", collectionRequest: null,
  catalog: null, issues: [], nodes: new Map(), loaded: new Set(), pending: new Map(),
  failed: new Set(), observer: null, mode: "browse", active: null, lastBrowse: null,
  sequence: 0, scrollScheduled: false, allIssues: [], themes: null, theme: "all",
  yearFrom: "", yearTo: "", searchQuery: "", focusHandle: null, articleHandle: null,
};
const $ = (selector) => document.querySelector(selector);
const keyFor = (volume, issue) => `${volume}:${issue}`;

function filterParams() {
  const params = new URLSearchParams();
  if (state.theme !== "all") { params.set("theme", state.theme); params.set("run", state.themes.model_id || state.themes.run_id); }
  if (state.yearFrom) params.set("year_from", state.yearFrom);
  if (state.yearTo) params.set("year_to", state.yearTo);
  if (state.collectionType) params.set("collection_type", state.collectionType);
  if (state.collection) params.set("collection", state.collection);
  return params;
}

function updateLocation(extras = {}) {
  const params = filterParams();
  Object.entries(extras).forEach(([key, value]) => { if (value) params.set(key, value); });
  if (state.articleHandle) params.set("article", state.articleHandle);
  history.replaceState(null, "", params.size ? `?${params}` : location.pathname);
}

function selectedArticles() {
  if (!state.catalog) return null;
  const params = filterParams();
  const handles = collectionHandles(state.collections, params);
  return state.catalog.articles.filter((article) => matchesFilters(article, params, state.themes, handles));
}

function renderCollectionOptions() {
  const options = collectionOptions(state.collections, state.collectionType);
  $("#collection-choice").hidden = !state.collectionType;
  $("#collection-label").textContent = state.collectionType === "country_survey" ? "Country" : "Special issue";
  $("#collection-filter").innerHTML = `<option value="">${state.collectionType === "country_survey" ? "All countries" : "All special issues"}</option>` + options.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(collectionLabel(item))} (${item.handles.length})</option>`).join("");
  $("#collection-filter").value = state.collection;
}

async function loadCollections(params) {
  $("#retry-collections").hidden = true;
  try {
    state.collections = await dataClient.getCollections();
    const type = params.get("collection_type"), id = params.get("collection");
    state.collectionType = Object.hasOwn(collectionTypes, type) ? type : "";
    state.collection = collectionOptions(state.collections, state.collectionType).some((item) => item.id === id) ? id : "";
    $("#collection-type").disabled = $("#collection-filter").disabled = false;
    $("#collection-type").value = state.collectionType;
    renderCollectionOptions();
    const invalid = (type && !state.collectionType) || (id && !state.collection);
    $("#collection-note").textContent = (invalid ? "The linked collection is unavailable. Showing all articles in the selected category. " : "") + "Verified collections only; historical coverage is incomplete. Country surveys are articles in the journal’s named series.";
  } catch {
    state.collections = null;
    state.collectionType = state.collection = "";
    $("#collection-type").disabled = $("#collection-filter").disabled = true;
    $("#collection-note").textContent = "Collections could not be loaded. Showing articles without collection filtering.";
    $("#retry-collections").hidden = false;
  }
}

function filterDescription() {
  const label = state.theme === "all" ? "All themes" : state.theme === "-1" ? "Unassigned abstracts" : state.themes.topics.find((topic) => topic.id === Number(state.theme)).label;
  const collection = state.collections?.collections.find((item) => item.id === state.collection);
  const collectionText = collection ? collectionLabel(collection) : collectionTypes[state.collectionType];
  return `${label}${collectionText ? ` · ${collectionText}` : ""}${state.yearFrom || state.yearTo ? ` · ${state.yearFrom || "earliest"}–${state.yearTo || "latest"}` : ""}`;
}

function applyIssueFilters() {
  const selected = selectedArticles();
  if (selected) {
    const counts = new Map();
    selected.forEach((article) => { const key = keyFor(article.volume, article.issue); counts.set(key, (counts.get(key) || 0) + 1); });
    state.issues = state.allIssues.filter((issue) => counts.has(issue.key)).map((issue) => ({...issue, count: counts.get(issue.key)}));
  } else state.issues = state.allIssues;
  const volumes = new Set(state.issues.map((issue) => issue.volume));
  document.querySelectorAll("#volume-list button, #volume-select option").forEach((element) => { element.disabled = !volumes.has(element.dataset.volume || element.value); });
  $("#reset-filters").hidden = state.theme === "all" && !state.yearFrom && !state.yearTo && !state.collectionType;
}

function changeFilters() {
  const type = $("#collection-type").value;
  if (type !== state.collectionType) {
    state.collectionType = type;
    state.collection = "";
    renderCollectionOptions();
  } else state.collection = $("#collection-filter").value;
  state.theme = $("#theme-filter").value;
  state.yearFrom = $("#year-from").value;
  state.yearTo = $("#year-to").value;
  if (state.yearFrom && state.yearTo && Number(state.yearFrom) > Number(state.yearTo)) {
    state.yearTo = state.yearFrom;
    $("#year-to").value = state.yearTo;
  }
  state.focusHandle = state.articleHandle = null;
  applyIssueFilters();
  if (state.mode === "search") runSearch(state.searchQuery);
  else renderBrowse();
}

function placeholder() {
  return `<div class="issue-placeholder" aria-hidden="true"><span></span><span></span><span></span></div>`;
}

function estimateHeight(issue) {
  return 82 + issue.count * (window.matchMedia("(max-width: 680px)").matches ? 156 : 124);
}

function issueShell(issue) {
  return `<section class="issue issue-shell unloaded" id="issue-${escapeHtml(issue.volume)}-${escapeHtml(issue.number)}" data-key="${escapeHtml(issue.key)}" style="height:${estimateHeight(issue)}px"><div class="issue-header"><div><span class="issue-volume">Volume ${escapeHtml(issue.volume)} · ${escapeHtml(issue.year)}</span><h3>Issue ${escapeHtml(issue.number)}</h3></div><span>${issue.count} ARTICLES</span></div><div class="issue-body">${placeholder()}</div></section>`;
}

function stopBrowse() {
  state.observer?.disconnect();
  state.observer = null;
  state.pending.forEach((controller) => controller.abort());
  state.pending.clear();
  state.loaded.clear();
  state.failed.clear();
  state.nodes.clear();
}

async function loadIssue(key) {
  if (state.mode !== "browse" || state.loaded.has(key) || state.pending.has(key) || state.failed.has(key)) return;
  const issue = state.issues.find((item) => item.key === key);
  const shell = state.nodes.get(key);
  if (!issue || !shell) return;
  const controller = new AbortController();
  const sequence = state.sequence;
  state.pending.set(key, controller);
  shell.querySelector(".issue-body").innerHTML = `<p class="issue-loading">Loading articles…</p>${placeholder()}`;
  try {
    const params = filterParams();
    params.set("volume", issue.volume); params.set("number", issue.number);
    const data = await dataClient.getIssue(params, controller.signal);
    if (sequence !== state.sequence || state.mode !== "browse" || !shell.isConnected) return;
    shell.querySelector(".issue-body").innerHTML = data.articles.map((item) => articleCard(item)).join("");
    shell.classList.remove("unloaded");
    shell.style.height = "";
    state.loaded.add(key);
    attachDetails(shell);
    if (state.focusHandle) {
      const focused = [...shell.querySelectorAll(".article-card")].find((card) => card.dataset.handle === state.focusHandle);
      if (focused) {
        focused.open = true;
        focused.scrollIntoView?.({block: "start", behavior: "auto"});
        focused.querySelector("summary").focus();
        state.focusHandle = null;
      }
    }
    scheduleScrollCheck();
  } catch (error) {
    if (error.name === "AbortError" || sequence !== state.sequence || !shell.isConnected) return;
    state.failed.add(key);
    shell.querySelector(".issue-body").innerHTML = `<div class="issue-error">${escapeHtml(error.message)} <button type="button" data-retry="${escapeHtml(key)}">Retry</button></div>`;
  } finally {
    if (state.pending.get(key) === controller) state.pending.delete(key);
  }
}

function unloadIssue(key) {
  const shell = state.nodes.get(key);
  if (!shell || !state.loaded.has(key)) return;
  shell.querySelectorAll("details[open]").forEach((details) => { details.open = false; });
  const height = shell.getBoundingClientRect().height;
  shell.style.height = `${height}px`;
  shell.querySelector(".issue-body").innerHTML = placeholder();
  shell.classList.add("unloaded");
  state.loaded.delete(key);
}

function checkViewport() {
  if (state.mode !== "browse") return;
  const viewport = window.innerHeight;
  for (const issue of state.issues) {
    const rect = state.nodes.get(issue.key).getBoundingClientRect();
    if (rect.bottom > -900 && rect.top < viewport + 900) loadIssue(issue.key);
  }
  for (const key of state.loaded) {
    const rect = state.nodes.get(key).getBoundingClientRect();
    if (rect.bottom < -2 * viewport || rect.top > 3 * viewport) unloadIssue(key);
  }
  for (const [key, controller] of state.pending) {
    const rect = state.nodes.get(key).getBoundingClientRect();
    if (rect.bottom < -2 * viewport || rect.top > 3 * viewport) controller.abort();
  }
  const active = state.issues.find((issue) => state.nodes.get(issue.key).getBoundingClientRect().bottom > 150);
  if (active && active.key !== state.active) {
    state.active = active.key;
    state.lastBrowse = active.key;
    $("#volume-select").value = active.volume;
    document.querySelectorAll(".volume-list button").forEach((button) => {
      const selected = button.dataset.volume === active.volume;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-current", selected ? "page" : "false");
    });
    updateLocation({volume: active.volume, issue: active.number});
  }
}

function scheduleScrollCheck() {
  if (state.scrollScheduled) return;
  state.scrollScheduled = true;
  requestAnimationFrame(() => { state.scrollScheduled = false; checkViewport(); });
}

function renderBrowse(targetKey = state.lastBrowse) {
  state.sequence++;
  stopBrowse();
  state.mode = "browse";
  state.active = null;
  applyIssueFilters();
  $("#view-kicker").textContent = "Browse by issue";
  $("#view-title").textContent = state.collectionType ? collectionTypes[state.collectionType] : state.theme === "all" ? "All issues" : "Articles by theme";
  const count = state.issues.reduce((sum, issue) => sum + issue.count, 0);
  $("#view-description").textContent = `${count.toLocaleString()} articles · ${filterDescription()} · newest issues first.`;
  $("#clear-search").hidden = true;
  $("#status").textContent = "";
  $("#results").innerHTML = state.issues.map(issueShell).join("");
  if (!state.issues.length) {
    $("#results").innerHTML = '<div class="empty">No articles match these filters. Choose a different collection, theme or publication period.</div>';
    updateLocation();
    return;
  }
  state.issues.forEach((issue) => state.nodes.set(issue.key, document.getElementById(`issue-${issue.volume}-${issue.number}`)));
  if ("IntersectionObserver" in window) {
    state.observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => { if (entry.isIntersecting) loadIssue(entry.target.dataset.key); });
    }, {rootMargin: "900px 0px"});
    state.nodes.forEach((node) => state.observer.observe(node));
  }
  const target = state.nodes.get(targetKey) || state.nodes.get(state.issues[0].key);
  const targetIssue = state.issues.find((issue) => state.nodes.get(issue.key) === target);
  updateLocation({volume: targetIssue.volume, issue: targetIssue.number});
  // Position before viewport tracking can replace the requested issue in the URL.
  // Instant scrolling also overrides the page's CSS smooth-scroll behaviour.
  if (targetIssue.key === state.issues[0].key) window.scrollTo({top: 0, behavior: "instant"});
  else target.scrollIntoView({block: "start", behavior: "instant"});
  scheduleScrollCheck();
}

function chooseVolume(number) {
  const issue = state.issues.find((item) => item.volume === number);
  if (!issue) return;
  state.articleHandle = state.focusHandle = null;
  $("#search-input").value = "";
  if (state.mode !== "browse") renderBrowse(issue.key);
  else {
    state.nodes.get(issue.key).scrollIntoView({block: "start", behavior: "auto"});
    scheduleScrollCheck();
  }
}

async function runSearch(query, retry = false) {
  state.sequence++;
  const sequence = state.sequence;
  stopBrowse();
  state.mode = "search";
  state.searchQuery = query;
  $("#view-kicker").textContent = "Semantic search";
  $("#view-title").textContent = `Results for “${query}”`;
  $("#view-description").textContent = `Ranked by relevance · ${filterDescription()}.`;
  $("#clear-search").hidden = false;
  $("#status").textContent = "Searching the article index…";
  $("#results").innerHTML = "";
  updateLocation({q: query});
  try {
    const params = filterParams(); params.set("q", query);
    const data = await dataClient.search(params, {retry, onProgress(message) {
      if (sequence === state.sequence) $("#status").textContent = message;
    }});
    if (sequence !== state.sequence) return;
    $("#status").textContent = "";
    if (data.mode === "keyword") {
      $("#view-kicker").textContent = "Keyword search";
      $("#status").innerHTML = 'Semantic search could not be loaded. Showing keyword results. <button class="text-button" id="retry-semantic" type="button">Retry semantic search</button>';
      $("#retry-semantic").addEventListener("click", () => runSearch(query, true));
    }
    $("#results").innerHTML = data.results.length ? `<p class="result-count">Showing ${data.results.length} relevant articles</p>${data.results.map((item) => articleCard(item, true)).join("")}` : `<div class="empty">No articles matched this search. Try a broader topic or a different author name.</div>`;
    attachDetails($("#results"));
  } catch (error) {
    if (sequence === state.sequence) $("#status").textContent = error.message;
  }
}

async function init() {
  try {
    const [catalog, source, availableThemes] = await Promise.all([dataClient.getCatalog(), dataClient.getSource().catch(() => ({})), dataClient.getThemes().catch(() => null)]);
    state.catalog = catalog;
    if (!state.catalog.count) throw new Error("The article index is being prepared. Please try again shortly.");
    $("#article-count").textContent = state.catalog.count.toLocaleString();
    $("#volume-count").textContent = state.catalog.volumes.length;
    state.allIssues = state.catalog.volumes.flatMap((volume) => [...volume.issues].sort((a, b) => parseInt(b.number, 10) - parseInt(a.number, 10)).map((issue) => ({
      key: keyFor(volume.number, issue.number), volume: volume.number, number: issue.number,
      year: volume.years.join("–"), count: issue.count,
    })));
    $("#volume-list").innerHTML = state.catalog.volumes.map((item) => `<button type="button" data-volume="${escapeHtml(item.number)}">Volume ${escapeHtml(item.number)} <small>${escapeHtml(item.years.join("–"))}</small></button>`).join("");
    $("#volume-select").innerHTML = state.catalog.volumes.map((item) => `<option value="${escapeHtml(item.number)}">Volume ${escapeHtml(item.number)} · ${escapeHtml(item.years.join("–"))}</option>`).join("");
    $("#volume-list").addEventListener("click", (event) => { const button = event.target.closest("button[data-volume]"); if (button) chooseVolume(button.dataset.volume); });
    $("#volume-select").addEventListener("change", (event) => chooseVolume(event.target.value));
    $("#results").addEventListener("click", (event) => {
      const retry = event.target.closest("button[data-retry]");
      if (retry) { state.failed.delete(retry.dataset.retry); loadIssue(retry.dataset.retry); }
    });
    if (source) {
      if (source.synced_at) $("#updated-at").textContent = `Updated ${new Date(source.synced_at).toLocaleDateString(undefined, {year:"numeric",month:"long",day:"numeric"})}.`;
    }
    const params = new URLSearchParams(location.search);
    let themes;
    themes = availableThemes;
    if (themes?.status === "ready") {
      state.themes = themes;
      $("#theme-filter").innerHTML = '<option value="all">All themes · includes articles without abstracts</option>' + themes.topics.map((topic) => `<option value="${topic.id}">${escapeHtml(topic.label)} (${topic.count})</option>`).join("") + `<option value="-1">Unassigned abstracts (${themes.articles.filter((article) => article.topic === -1).length})</option>`;
      const observedYears = themes.articles.map((article) => Number(article.year)).filter((year) => Number.isInteger(year) && year >= 1500 && year <= 2500);
      const linkedYears = [params.get("year_from"), params.get("year_to")].filter((year) => /^\d{4}$/.test(year || "") && Number(year) >= 1500 && Number(year) <= 2500).map(Number);
      const years = [...new Set([...observedYears, ...linkedYears])].sort((a, b) => a - b).map(String);
      for (const id of (['#year-from', '#year-to'])) $(id).innerHTML = '<option value="">Any year</option>' + years.map((year) => `<option value="${year}">${year}</option>`).join("");
      const validTheme = params.get("theme") === "-1" || themes.topics.some((topic) => String(topic.id) === params.get("theme"));
      const oldRun = params.has("theme") && ![themes.model_id || themes.run_id, themes.run_id].includes(params.get("run"));
      state.theme = validTheme && !oldRun ? params.get("theme") : "all";
      state.yearFrom = years.includes(params.get("year_from")) ? params.get("year_from") : "";
      state.yearTo = years.includes(params.get("year_to")) ? params.get("year_to") : "";
      if (state.yearFrom && state.yearTo && Number(state.yearFrom) > Number(state.yearTo)) state.yearTo = state.yearFrom;
      $("#theme-filter").value = state.theme; $("#year-from").value = state.yearFrom; $("#year-to").value = state.yearTo;
      document.querySelectorAll("#theme-filter, #year-from, #year-to").forEach((select) => { select.disabled = false; });
      $("#theme-note").textContent = oldRun ? "The linked theme belongs to an earlier model. Showing all themes; choose a current theme above." : `${themes.stale ? "The catalog has changed since modelling. " : ""}Themes use the last published model. Articles without a model assignment remain available under All themes.`;
    } else $("#theme-note").textContent = themes?.status === "not_built" ? "Theme filtering will be available after the first topic model is built." : "Theme filtering is temporarily unavailable. Issue browsing and search remain available.";
    state.collectionRequest = new URLSearchParams(params);
    await loadCollections(params);
    state.focusHandle = state.articleHandle = params.get("article");
    applyIssueFilters();
    const deepLink = keyFor(params.get("volume"), params.get("issue"));
    const target = state.issues.find((item) => item.key === deepLink)?.key || state.issues.find((item) => item.volume === params.get("volume"))?.key || state.issues[0]?.key;
    state.lastBrowse = target;
    if (params.get("q")) { $("#search-input").value = params.get("q"); runSearch(params.get("q")); }
    else renderBrowse(target);
  } catch (error) {
    $("#view-title").textContent = "Index unavailable";
    $("#status").textContent = error.message;
  }
}

$("#search-form").addEventListener("submit", (event) => { event.preventDefault(); const query = $("#search-input").value.trim(); if (query.length >= 2) runSearch(query); });
$("#clear-search").addEventListener("click", () => { $("#search-input").value = ""; renderBrowse(); });
document.querySelectorAll(".article-filters select").forEach((select) => select.addEventListener("change", changeFilters));
$("#reset-filters").addEventListener("click", () => {
  state.collectionRequest = new URLSearchParams();
  $("#collection-type").value = ""; $("#collection-filter").value = "";
  $("#theme-filter").value = "all"; $("#year-from").value = $("#year-to").value = ""; changeFilters();
});
$("#retry-collections").addEventListener("click", async () => {
  await loadCollections(state.collectionRequest || new URLSearchParams());
  changeFilters();
});
window.addEventListener("scroll", scheduleScrollCheck, {passive: true});
window.addEventListener("resize", () => {
  if (state.mode !== "browse") return;
  state.issues.forEach((issue) => {
    if (!state.loaded.has(issue.key)) state.nodes.get(issue.key).style.height = `${estimateHeight(issue)}px`;
  });
  scheduleScrollCheck();
});
init();
