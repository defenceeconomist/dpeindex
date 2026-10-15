const state = {
  catalog: null, issues: [], nodes: new Map(), loaded: new Set(), pending: new Map(),
  failed: new Set(), observer: null, mode: "browse", active: null, lastBrowse: null,
  sequence: 0, scrollScheduled: false,
};
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
const keyFor = (volume, issue) => `${volume}:${issue}`;

function articleCard(article, search = false) {
  const issueLabel = search ? `<span>Vol. ${escapeHtml(article.volume)} · Issue ${escapeHtml(article.issue)}</span>` : "";
  return `<details class="article-card" data-handle="${escapeHtml(article.handle)}"><summary><div><div class="article-meta"><span>${escapeHtml(article.year)}</span><span>pp. ${escapeHtml(article.pages)}</span>${issueLabel}</div><span class="article-title">${escapeHtml(article.title)}</span><span class="article-authors">${escapeHtml(article.authors.join(", "))}</span></div><span class="expand-icon" aria-hidden="true">+</span></summary><div class="article-detail"><p>Loading article details…</p></div></details>`;
}

function renderDetail(article) {
  const cells = [
    ["Journal", article.journal], ["Volume / issue", `${article.volume} / ${article.issue}`],
    ["Year", article.year], ["Pages", article.pages], ["DOI", article.doi],
    ["Keywords", article.keywords || "Not supplied"],
  ];
  const links = [["View at RePEc", article.repec_url], ["Open DOI", article.doi_url], ["Publisher page", article.file_url]]
    .filter(([, url]) => url && /^https?:\/\//i.test(url))
    .map(([label, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>`).join("");
  return `<h4>Abstract</h4><p>${escapeHtml(article.abstract || "No abstract was supplied in the RePEc record.")}</p><div class="detail-grid">${cells.map(([key, value]) => `<div><strong>${escapeHtml(key)}</strong>${escapeHtml(value || "—")}</div>`).join("")}</div>${article.file_restriction ? `<p><small>${escapeHtml(article.file_restriction)}</small></p>` : ""}<div class="detail-links">${links}</div>`;
}

async function expandArticle(event) {
  const details = event.target;
  if (!details.open || details.dataset.loaded === "true") return;
  const target = details.querySelector(".article-detail");
  try {
    const response = await fetch(`api/article?handle=${encodeURIComponent(details.dataset.handle)}`);
    if (!response.ok) throw new Error("Article details could not be loaded.");
    const article = await response.json();
    if (!details.isConnected || !details.open) return;
    target.innerHTML = renderDetail(article);
    details.dataset.loaded = "true";
  } catch (error) {
    if (details.isConnected) target.textContent = error.message;
  }
}

function attachDetails(root) {
  root.querySelectorAll(".article-card").forEach((element) => element.addEventListener("toggle", expandArticle));
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
    const response = await fetch(`api/issue?volume=${encodeURIComponent(issue.volume)}&number=${encodeURIComponent(issue.number)}`, {signal: controller.signal});
    if (!response.ok) throw new Error("Issue could not be loaded.");
    const data = await response.json();
    if (sequence !== state.sequence || state.mode !== "browse" || !shell.isConnected) return;
    shell.querySelector(".issue-body").innerHTML = data.articles.map((item) => articleCard(item)).join("");
    shell.classList.remove("unloaded");
    shell.style.height = "";
    state.loaded.add(key);
    attachDetails(shell);
    scheduleScrollCheck();
  } catch (error) {
    if (error.name === "AbortError" || sequence !== state.sequence || !shell.isConnected) return;
    state.failed.add(key);
    shell.querySelector(".issue-body").innerHTML = `<div class="issue-error">This issue could not be loaded. <button type="button" data-retry="${escapeHtml(key)}">Retry</button></div>`;
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
    history.replaceState(null, "", `?volume=${encodeURIComponent(active.volume)}&issue=${encodeURIComponent(active.number)}`);
  }
}

function scheduleScrollCheck() {
  if (state.scrollScheduled) return;
  state.scrollScheduled = true;
  requestAnimationFrame(() => { state.scrollScheduled = false; checkViewport(); });
}

function renderBrowse(targetKey = state.lastBrowse || state.issues[0].key) {
  state.sequence++;
  stopBrowse();
  state.mode = "browse";
  state.active = null;
  $("#view-kicker").textContent = "Browse by issue";
  $("#view-title").textContent = "All issues";
  $("#view-description").textContent = `${state.catalog.count.toLocaleString()} articles · newest issues first. Scroll to explore earlier volumes.`;
  $("#clear-search").hidden = true;
  $("#status").textContent = "";
  $("#results").innerHTML = state.issues.map(issueShell).join("");
  state.issues.forEach((issue) => state.nodes.set(issue.key, document.getElementById(`issue-${issue.volume}-${issue.number}`)));
  if ("IntersectionObserver" in window) {
    state.observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => { if (entry.isIntersecting) loadIssue(entry.target.dataset.key); });
    }, {rootMargin: "900px 0px"});
    state.nodes.forEach((node) => state.observer.observe(node));
  }
  const target = state.nodes.get(targetKey) || state.nodes.get(state.issues[0].key);
  if (targetKey !== state.issues[0].key) target.scrollIntoView({block: "start", behavior: "auto"});
  scheduleScrollCheck();
}

function chooseVolume(number) {
  const issue = state.issues.find((item) => item.volume === number);
  if (!issue) return;
  $("#search-input").value = "";
  if (state.mode !== "browse") renderBrowse(issue.key);
  else {
    state.nodes.get(issue.key).scrollIntoView({block: "start", behavior: "auto"});
    scheduleScrollCheck();
  }
}

async function runSearch(query) {
  state.sequence++;
  const sequence = state.sequence;
  stopBrowse();
  state.mode = "search";
  $("#view-kicker").textContent = "Semantic search";
  $("#view-title").textContent = `Results for “${query}”`;
  $("#view-description").textContent = "Ranked by relevance across article titles and abstracts.";
  $("#clear-search").hidden = false;
  $("#status").textContent = "Searching the article index…";
  $("#results").innerHTML = "";
  history.replaceState(null, "", `?q=${encodeURIComponent(query)}`);
  try {
    const response = await fetch(`api/search?q=${encodeURIComponent(query)}`);
    if (!response.ok) throw new Error("Search is unavailable right now.");
    const data = await response.json();
    if (sequence !== state.sequence) return;
    $("#status").textContent = "";
    $("#results").innerHTML = data.results.length ? `<p class="result-count">Showing ${data.results.length} relevant articles</p>${data.results.map((item) => articleCard(item, true)).join("")}` : `<div class="empty">No articles matched this search. Try a broader topic or a different author name.</div>`;
    attachDetails($("#results"));
  } catch (error) {
    if (sequence === state.sequence) $("#status").textContent = error.message;
  }
}

async function init() {
  try {
    const [catalogResponse, sourceResponse] = await Promise.all([fetch("api/catalog"), fetch("api/source")]);
    if (!catalogResponse.ok) throw new Error("The catalog could not be loaded.");
    state.catalog = await catalogResponse.json();
    if (!state.catalog.count) throw new Error("The article index is being prepared. Please try again shortly.");
    $("#article-count").textContent = state.catalog.count.toLocaleString();
    $("#volume-count").textContent = state.catalog.volumes.length;
    state.issues = state.catalog.volumes.flatMap((volume) => [...volume.issues].sort((a, b) => parseInt(b.number, 10) - parseInt(a.number, 10)).map((issue) => ({
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
    if (sourceResponse.ok) {
      const source = await sourceResponse.json();
      if (source.synced_at) $("#updated-at").textContent = `Updated ${new Date(source.synced_at).toLocaleDateString(undefined, {year:"numeric",month:"long",day:"numeric"})}.`;
    }
    const params = new URLSearchParams(location.search);
    const deepLink = keyFor(params.get("volume"), params.get("issue"));
    const target = state.issues.find((item) => item.key === deepLink)?.key || state.issues.find((item) => item.volume === params.get("volume"))?.key || state.issues[0].key;
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
window.addEventListener("scroll", scheduleScrollCheck, {passive: true});
window.addEventListener("resize", () => {
  if (state.mode !== "browse") return;
  state.issues.forEach((issue) => {
    if (!state.loaded.has(issue.key)) state.nodes.get(issue.key).style.height = `${estimateHeight(issue)}px`;
  });
  scheduleScrollCheck();
});
init();
