import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {buildCollections} from "../scripts/build-collections.mjs";
import {collectionHandles, collectionOptions} from "../site/collection-filter.js";

const article = {handle: "a", volume: "1", issue: "1", year: "2000"};
const item = {id: "special-one", type: "special_issue", title: "One", ...article, handles: ["a"], sources: ["https://example.org/evidence"], review_note: "Verified contents"};
const registry = {schema_version: 1, collections: [item]};
test("registry rejects invalid sources, duplicate IDs/members and stale identities", () => {
  assert.equal(buildCollections(registry, [article]).collections.length, 1);
  for (const change of [{id: "bad id"}, {sources: []}, {sources: ["javascript:alert(1)"]}, {handles: ["a", "a"]}, {handles: ["unknown"]}, {issue: "2"}]) {
    assert.throws(() => buildCollections({...registry, collections: [{...item, ...change}]}, [article]));
  }
  assert.throws(() => buildCollections({...registry, collections: [item, item]}, [article]), /duplicate/);
});

test("collection membership uses a union within category and excludes other categories", () => {
  const data = {collections: [item, {...item, id: "country-z", type: "country_survey", title: "Zimbabwe", handles: ["a", "b"]}, {...item, id: "country-a", type: "country_survey", title: "Angola", handles: ["b"]}]};
  assert.deepEqual([...collectionHandles(data, new URLSearchParams({collection_type: "country_survey"}))], ["b", "a"]);
  assert.deepEqual(collectionOptions(data, "country_survey").map(c => c.id), ["country-a", "country-z"]);
  assert.throws(() => collectionHandles(data, new URLSearchParams({collection_type: "special_issue", collection: "country-a"})), /not found/);
});

test("curated mixed issues exclude unrelated papers and country surveys are individual records", async () => {
  const data = JSON.parse(await readFile(new URL("../data/collections.json", import.meta.url)));
  const inflation = data.collections.find(c => c.id === "special-27-2");
  assert.equal(inflation.handles.length, 6);
  assert.ok(inflation.handles.every(h => !h.endsWith("p:161-171") && !h.endsWith("p:280-298")));
  const norad = data.collections.find(c => c.id === "special-35-5");
  assert.equal(norad.handles.length, 5);
  assert.ok(!norad.handles.some(h => h.endsWith("p:601-621")));
  assert.equal(data.collections.find(c => c.id === "country-taiwan").handles.length, 1);
  assert.ok(!data.collections.some(c => c.id === "special-23-4"));
});
