// Validate curated memberships against the input corpus; never mutate the source release.
export function buildCollections(registry, articles) {
  if (registry.schema_version !== 1 || !Array.isArray(registry.collections)) throw new Error("Invalid collection registry");
  const byHandle = new Map(articles.map((a) => [a.handle, a]));
  const ids = new Set();
  for (const item of registry.collections) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.id) || ids.has(item.id)) throw new Error(`Invalid or duplicate collection ID: ${item.id}`);
    ids.add(item.id);
    if (!["special_issue", "country_survey"].includes(item.type) || !item.title?.trim() || !item.review_note?.trim()) throw new Error(`Invalid collection: ${item.id}`);
    if (!item.sources?.length || item.sources.some((url) => { try { return !["http:", "https:"].includes(new URL(url).protocol); } catch { return true; } })) throw new Error(`Missing or invalid source: ${item.id}`);
    if (!item.handles?.length || new Set(item.handles).size !== item.handles.length) throw new Error(`Empty or duplicate membership: ${item.id}`);
    if (item.type === "country_survey" && !item.country?.trim()) throw new Error(`Missing country: ${item.id}`);
    for (const handle of item.handles) {
      const article = byHandle.get(handle);
      if (!article) throw new Error(`Unknown collection article: ${handle}`);
      if (item.type === "special_issue" && (article.volume !== item.volume || article.issue !== item.issue || article.year !== item.year)) throw new Error(`Collection issue mismatch: ${handle}`);
    }
  }
  return {schema_version: 1, reviewed_at: registry.reviewed_at, coverage: registry.coverage, collections: registry.collections};
}
