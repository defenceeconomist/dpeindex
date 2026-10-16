import assert from "node:assert/strict";
import test from "node:test";
import {createDataClient} from "../site/data-client.js";
import {rankArticles} from "../site/search-ranking.js";

const articles = Array.from({length: 45}, (_, i) => ({handle: `paper-${i}`, title: `Peace research ${i}`, authors: [i === 44 ? "Exact Author" : "Other"], abstract: "Security and economic growth", volume: "1", issue: "1", year: i === 44 ? "2001" : "2000", pages: "1", doi: ""}));
const themes = {status: "ready", run_id: "run", topics: [{id: 0}], articles: articles.map((a, i) => ({...a, topic: i === 43 ? null : i === 44 ? -1 : 0}))};
function fixture(prefix = "/dpeindex/") {
  const requests = [], root = new URL(`https://example.test${prefix}`);
  const values = {"data/collections.json": {collections: [{id: "special-test", type: "special_issue", title: "Test", handles: ["paper-43", "paper-44"]}]}, "data/catalog.json": {articles, count: articles.length}, "data/themes.json": themes,
    "data/manifest.json": {issues: {"1:1": "data/issues/one.json"}, search: {dimensions: 2}},
    "data/issues/one.json": {volume: "1", issue: "1", articles}, "data/search.json": articles};
  const fetcher = async (url) => {
    requests.push(url);
    const path = new URL(url).pathname.split("/releases/test/")[1];
    assert.ok(path, "all data stays within the deployed release");
    if (path === "data/embeddings.bin") return {ok: true, arrayBuffer: async () => new Float32Array(articles.flatMap(() => [1, 0])).buffer};
    return {ok: path in values, json: async () => structuredClone(values[path])};
  };
  return {client: createDataClient({mode: "static", datasetBase: "releases/test/", modelBase: "models/test/"}, root, fetcher), requests};
}

test("static adapter lazily loads issue records, filters missing/unassigned and rejects old model links", async () => {
  for (const prefix of ["/", "/dpeindex/"]) {
    const {client, requests} = fixture(prefix);
    await client.getCatalog();
    assert.equal(requests.length, 1);
    const data = await client.getIssue(new URLSearchParams({volume: "1", number: "1", theme: "-1", run: "run"}));
    assert.deepEqual(data.articles.map((a) => a.handle), ["paper-44"]);
    assert.equal((await client.getArticle("paper-43")).abstract, articles[43].abstract);
    assert.equal(requests.filter((r) => r.includes("issues/one")).length, 1);
    await assert.rejects(client.getIssue(new URLSearchParams({volume: "1", number: "1", theme: "0", run: "old"})), /model has changed/);
    assert.equal(new URL(client.downloadUrl("run", "bundle.zip")).pathname, `${prefix}api/citations/run/download/bundle.zip`);
    const aborted = new AbortController(); aborted.abort();
    await assert.rejects(client.getIssue(new URLSearchParams({volume: "1", number: "1"}), aborted.signal), {name: "AbortError"});
  }
});

test("semantic results filter before top 40 and preserve author priority", () => {
  const vectors = new Float32Array(articles.flatMap(() => [1, 0]));
  const result = rankArticles(articles, "Exact Author", new URLSearchParams(), themes, new Float32Array([1, 0]), vectors);
  assert.equal(result.results[0].handle, "paper-44");
  assert.equal(result.results.length, 40);
  const filtered = rankArticles(articles, "peace", new URLSearchParams({year_from: "2001"}), themes, new Float32Array([1, 0]), vectors);
  assert.deepEqual(filtered.results.map((a) => a.handle), ["paper-44"]);
});

test("failed model uses labelled keyword fallback and retry starts a fresh worker", async () => {
  const original = globalThis.Worker;
  let attempts = 0;
  globalThis.Worker = class {
    constructor() { this.attempt = ++attempts; }
    postMessage(data) { queueMicrotask(() => this.onmessage({data: this.attempt === 1 ? {id: data.id, error: "download failed"} : {id: data.id, vector: new Float32Array([1, 0])}})); }
    terminate() {}
  };
  try {
    const {client} = fixture();
    const params = new URLSearchParams({q: "economic growth"});
    assert.equal((await client.search(params)).mode, "keyword");
    assert.equal((await client.search(params, {retry: true})).mode, "semantic");
    assert.equal(attempts, 2);
  } finally { globalThis.Worker = original; }
});


test("collection membership is identical in issue browsing, keyword and semantic search", async () => {
  const original = globalThis.Worker;
  let attempts = 0;
  globalThis.Worker = class {
    constructor() { this.attempt = ++attempts; }
    postMessage(data) { queueMicrotask(() => this.onmessage({data: this.attempt === 1 ? {id: data.id, error: "offline"} : {id: data.id, vector: new Float32Array([1, 0])}})); }
    terminate() {}
  };
  try {
    const {client} = fixture();
    const params = new URLSearchParams({volume: "1", number: "1", collection_type: "special_issue", collection: "special-test", q: "peace", year_from: "2001"});
    const expected = ["paper-44"];
    assert.deepEqual((await client.getIssue(params)).articles.map(a => a.handle), expected);
    const fallback = await client.search(params);
    assert.equal(fallback.mode, "keyword");
    assert.deepEqual(fallback.results.map(a => a.handle), expected);
    const semantic = await client.search(params, {retry: true});
    assert.equal(semantic.mode, "semantic");
    assert.deepEqual(semantic.results.map(a => a.handle), expected);
    params.set("collection", "missing");
    await assert.rejects(client.search(params), /Collection not found/);
  } finally { globalThis.Worker = original; }
});
