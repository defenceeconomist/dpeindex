import {dataClient} from "./data-client.js";
export const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));

export function articleCard(article, search = false) {
  const issueLabel = search ? `<span>Vol. ${escapeHtml(article.volume)} · Issue ${escapeHtml(article.issue)}</span>` : "";
  return `<details class="article-card" data-handle="${escapeHtml(article.handle)}"><summary><div><div class="article-meta"><span>${escapeHtml(article.year)}</span><span>pp. ${escapeHtml(article.pages)}</span>${issueLabel}</div><span class="article-title">${escapeHtml(article.title)}</span><span class="article-authors">${escapeHtml(article.authors.join(", "))}</span></div><span class="expand-icon" aria-hidden="true">+</span></summary><div class="article-detail"><p>Loading article details…</p></div></details>`;
}

export function renderDetail(article) {
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
    const article = await dataClient.getArticle(details.dataset.handle);
    if (!details.isConnected || !details.open) return;
    target.innerHTML = renderDetail(article);
    details.dataset.loaded = "true";
  } catch (error) {
    if (details.isConnected) target.textContent = error.message;
  }
}

export function attachDetails(root) {
  root.querySelectorAll(".article-card").forEach((element) => element.addEventListener("toggle", expandArticle));
}
