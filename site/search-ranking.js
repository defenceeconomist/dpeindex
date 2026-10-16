export function matchesFilters(article, params, themes, handles = null) {
  if (handles && !handles.has(article.handle)) return false;
  const get = (key) => params.get(key);
  if (get("year_from") && get("year_to") && Number(get("year_from")) > Number(get("year_to"))) throw new Error("The first year must not exceed the last year.");
  if (get("year_from") && (!/^\d{4}$/.test(article.year) || Number(article.year) < Number(get("year_from")))) return false;
  if (get("year_to") && (!/^\d{4}$/.test(article.year) || Number(article.year) > Number(get("year_to")))) return false;
  if (get("theme") !== null) {
    if (themes.status !== "ready" || ![themes.model_id || themes.run_id, themes.run_id].includes(get("run"))) throw new Error("The topic model has changed. Reload the page to use its current themes.");
    if (!themes.topics.some((t) => t.id === Number(get("theme"))) && get("theme") !== "-1") throw new Error("Theme not found in this model.");
    return themes.articles.find((a) => a.handle === article.handle)?.topic === Number(get("theme"));
  }
  return true;
}

export function rankArticles(articles, query, params, themes, vector = null, vectors = null, handles = null) {
  const text = query.toLowerCase();
  const terms = text.match(/[\p{L}\p{N}_’-]+/gu) || [];
  const scored = [];
  articles.forEach((a, row) => {
    if (!matchesFilters(a, params, themes, handles)) return;
    const title = a.title.toLowerCase(), authors = a.authors.join(" ").toLowerCase();
    const exactTokens = terms.length && terms.every((term) => `${title} ${authors}`.includes(term));
    let score = 0;
    if (vector) {
      for (let j = 0; j < vector.length; j++) score += vector[j] * vectors[row * vector.length + j];
    } else {
      const abstract = (a.abstract || "").toLowerCase();
      score = terms.reduce((sum, term) => sum + (title.includes(term) ? 4 : 0) + (authors.includes(term) ? 4 : 0) + (abstract.includes(term) ? 1 : 0), 0);
    }
    scored.push({article: a, score, exactTokens, author: Number(authors.includes(text)), title: Number(title.includes(text))});
  });
  // Match the API: semantic top 40 plus exact title/author token matches, then
  // prioritise complete author/title phrases and apply the final limit.
  const semantic = vector ? new Set([...scored].sort((a, b) => b.score - a.score).slice(0, 40)) : null;
  const selected = scored.filter((s) => vector ? semantic.has(s) || s.exactTokens : s.score > 0);
  selected.sort((a, b) => b.author - a.author || b.title - a.title || b.score - a.score || a.article.handle.localeCompare(b.article.handle));
  const fields = ["handle", "title", "authors", "volume", "issue", "year", "pages", "doi"];
  return {query, total: selected.length, results: selected.slice(0, 40).map(({article}) => Object.fromEntries(fields.map((key) => [key, article[key]])))};
}
