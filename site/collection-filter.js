export const collectionTypes = {special_issue: "Special issues", country_survey: "Country surveys"};

export function collectionOptions(data, type) {
  return (data?.collections || []).filter((item) => item.type === type).sort((a, b) =>
    type === "special_issue" ? Number(b.volume) - Number(a.volume) || parseInt(b.issue, 10) - parseInt(a.issue, 10) : a.title.localeCompare(b.title));
}

export function collectionLabel(item) {
  return item.type === "special_issue" ? `Vol. ${item.volume} · Issue ${item.issue} · ${item.title}` : item.title;
}

// Compile membership once per filter operation, deduplicating overlapping collections.
export function collectionHandles(data, params) {
  const type = params.get("collection_type"), id = params.get("collection");
  if (!type && !id) return null;
  if (!data) throw new Error("Collection filtering is unavailable. Retry loading collections.");
  if (!Object.hasOwn(collectionTypes, type)) throw new Error("Unknown collection type.");
  const options = collectionOptions(data, type).filter((item) => !id || item.id === id);
  if (id && !options.length) throw new Error("Collection not found in this category.");
  return new Set(options.flatMap((item) => item.handles));
}
