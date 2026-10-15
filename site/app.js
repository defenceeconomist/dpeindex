const state = { catalog: null, volume: null, query: "", sequence: 0 };
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
const articleId = (handle) => "a-" + encodeURIComponent(handle).replace(/%/g, "_");

function articleCard(article, search = false) {
  const issueLabel = search ? `<span>Vol. ${escapeHtml(article.volume)} · Issue ${escapeHtml(article.issue)}</span>` : "";
  return `<details class="article-card" data-handle="${escapeHtml(article.handle)}"><summary><div><div class="article-meta"><span>${escapeHtml(article.year)}</span><span>pp. ${escapeHtml(article.pages)}</span>${issueLabel}</div><span class="article-title">${escapeHtml(article.title)}</span><span class="article-authors">${escapeHtml(article.authors.join(", "))}</span></div><span class="expand-icon" aria-hidden="true">+</span></summary><div class="article-detail" id="${articleId(article.handle)}"><p>Loading article details…</p></div></details>`;
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
    target.innerHTML = renderDetail(await response.json());
    details.dataset.loaded = "true";
  } catch (error) {
    target.textContent = error.message;
  }
}

function attachDetails() {
  document.querySelectorAll(".article-card").forEach((element) => element.addEventListener("toggle", expandArticle));
}

function renderVolume() {
  const volume = state.catalog.volumes.find((item) => item.number === state.volume);
  if (!volume) return;
  state.query = "";
  $("#view-kicker").textContent = "Browse by issue";
  $("#view-title").textContent = `Volume ${volume.number}`;
  $("#view-description").textContent = `${volume.years.join("–")} · ${volume.count} articles · ${volume.issues.length} issues`;
  $("#clear-search").hidden = true;
  $("#status").textContent = "";
  $("#volume-select").value = state.volume;
  document.querySelectorAll(".volume-list button").forEach((button) => {
    button.classList.toggle("active", button.dataset.volume === state.volume);
    button.setAttribute("aria-current", button.dataset.volume === state.volume ? "page" : "false");
  });
  const articles = state.catalog.articles.filter((item) => item.volume === state.volume);
  $("#results").innerHTML = volume.issues.map((issue) => {
    const items = articles.filter((item) => item.issue === issue.number);
    return `<section class="issue"><div class="issue-header"><h3>Issue ${escapeHtml(issue.number)}</h3><span>${items.length} ARTICLES</span></div>${items.map((item) => articleCard(item)).join("")}</section>`;
  }).join("");
  attachDetails();
}

function chooseVolume(number) {
  state.sequence++;
  state.volume = number;
  $("#search-input").value = "";
  renderVolume();
  history.replaceState(null, "", `?volume=${encodeURIComponent(number)}`);
}

async function runSearch(query) {
  const sequence = ++state.sequence;
  state.query = query;
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
    attachDetails();
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
    $("#volume-list").innerHTML = state.catalog.volumes.map((item) => `<button type="button" data-volume="${escapeHtml(item.number)}">Volume ${escapeHtml(item.number)} <small>${escapeHtml(item.years.join("–"))}</small></button>`).join("");
    $("#volume-select").innerHTML = state.catalog.volumes.map((item) => `<option value="${escapeHtml(item.number)}">Volume ${escapeHtml(item.number)} · ${escapeHtml(item.years.join("–"))}</option>`).join("");
    $("#volume-list").addEventListener("click", (event) => { const button = event.target.closest("button[data-volume]"); if (button) chooseVolume(button.dataset.volume); });
    $("#volume-select").addEventListener("change", (event) => chooseVolume(event.target.value));
    if (sourceResponse.ok) {
      const source = await sourceResponse.json();
      if (source.synced_at) $("#updated-at").textContent = `Updated ${new Date(source.synced_at).toLocaleDateString(undefined, {year:"numeric",month:"long",day:"numeric"})}.`;
    }
    const params = new URLSearchParams(location.search);
    state.volume = state.catalog.volumes.find((item) => item.number === params.get("volume"))?.number || state.catalog.volumes[0].number;
    if (params.get("q")) { $("#search-input").value = params.get("q"); runSearch(params.get("q")); }
    else renderVolume();
  } catch (error) {
    $("#view-title").textContent = "Index unavailable";
    $("#status").textContent = error.message;
  }
}

$("#search-form").addEventListener("submit", (event) => { event.preventDefault(); const query = $("#search-input").value.trim(); if (query.length >= 2) runSearch(query); });
$("#clear-search").addEventListener("click", () => chooseVolume(state.volume));
init();
