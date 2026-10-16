import {dataClient, createDataClient} from "../site/data-client.js";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {fixture, papers} from "./topic_fixture.mjs";
import {installDomHelpers, settle} from "./dom_helpers.mjs";

import {parseHTML} from "linkedom";
const html = await readFile(new URL("../site/index.html", import.meta.url), "utf8");
const all = [...papers, {...papers[1], handle: "no-abstract", title: "A missing abstract", topic: null}];
const catalog = {articles: all, count: all.length, volumes: ["37", "36", "35"].map((number) => ({number, years: [all.find((paper) => paper.volume === number).year], issues: [{number: "1", count: all.filter((paper) => paper.volume === number).length}]}))};
const themes = {status: "ready", run_id: "test-run", stale: false, topics: fixture.topics, articles: all};
const collections = {collections: [
  {id: "special-test", type: "special_issue", title: "Test issue", volume: "36", issue: "1", handles: ["paper-b", "no-abstract"]},
  {id: "country-a", type: "country_survey", title: "Country A", handles: ["paper-b"]},
  {id: "country-b", type: "country_survey", title: "Country B", handles: ["paper-b", "paper-c"]},
]};
let scenario = 0;

async function mount(query = "", options = {}) {
  const {window} = parseHTML(html);
  globalThis.window = window; globalThis.document = window.document;
  installDomHelpers(window);
  globalThis.requestAnimationFrame = (fn) => setImmediate(fn);
  globalThis.location = new URL(`http://example.test${options.prefix ?? "/dpe/"}${query}`);
  globalThis.history = {replaceState(a, b, url) { globalThis.location = new URL(url, globalThis.location); }};
  const requests = [];
  globalThis.fetch = async (raw) => {
    const url = new URL(raw, globalThis.location); requests.push(url);
    const path = url.pathname.split("/data/")[1];
    const json = (data, ok = true) => ({ok, json: async () => structuredClone(data)});
    if (path === "catalog.json") return json(catalog);
    if (path === "collections.json") return json(collections, !options.failCollections);
    if (path === "source.json") return json({});
    if (path === "themes.json") return json(options.themes || themes, !options.failThemes);
    if (path === "manifest.json") return json({issues: Object.fromEntries(["37", "36", "35"].map(v => [v + ":1", "data/issues/" + v + ".json"])), search: {dimensions: 2}});
    if (path === "search.json") return json(all);
    if (path?.startsWith("issues/")) return json({articles: all.filter(p => path === "issues/" + p.volume + ".json").map(p => ({...p, abstract: "Complete original abstract, including its FINAL WORDS.", journal: "DPE"}))});
    throw new Error(`Unexpected request ${url}`);
  };
  Object.assign(dataClient, createDataClient({datasetBase: "releases/test/", modelBase: "models/test/"}));
  await import(new URL(`../site/app.js?scenario=${++scenario}`, import.meta.url)); await settle();
  return {doc: window.document, requests};
}

function change(doc, selector, value) {
  const select = doc.querySelector(selector); select.value = value;
  select.dispatchEvent(new window.Event("change"));
}

test("theme filters browse and semantic search, with explicit unassigned and all-article choices", async () => {
  const {doc, requests} = await mount();
  assert.equal(doc.querySelectorAll(".article-card").length, 4);
  change(doc, "#theme-filter", "1"); await settle();
  assert.equal(doc.querySelectorAll(".article-card").length, 1);
  assert.match(doc.querySelector("#view-description").textContent, /arms · trade/);
  assert.equal(doc.querySelector('[data-volume="37"]').disabled, true);
  assert.ok(requests.some(url => url.pathname.includes("/data/issues/")));
  doc.querySelector("#search-input").value = "Author";
  doc.querySelector("#search-form").dispatchEvent(new window.Event("submit", {cancelable: true})); await settle();
  assert.ok(requests.some(url => url.pathname.endsWith("/data/search.json")));
  assert.equal(doc.querySelectorAll(".article-card").length, 1);
  change(doc, "#theme-filter", "-1"); await settle();
  assert.equal(doc.querySelector(".article-card").dataset.handle, "paper-c");
  doc.querySelector("#reset-filters").click(); await settle();
  assert.equal(doc.querySelectorAll(".article-card").length, 4);
  doc.querySelector("#clear-search").click(); await settle();
  assert.match(doc.querySelector("#view-title").textContent, /All issues/);
});

test("time-chart links retain date limits and main-page deep links open the complete abstract", async () => {
  const {doc} = await mount("?theme=1&run=test-run&year_from=2020&year_to=2024&volume=36&issue=1&article=paper-b");
  assert.equal(doc.querySelector("#year-from").value, "2020");
  assert.equal(doc.querySelectorAll(".article-card").length, 1);
  const card = doc.querySelector(".article-card");
  assert.equal(card.open, true);
  card.dispatchEvent(new window.Event("toggle")); await settle();
  assert.match(card.textContent, /FINAL WORDS/);
  change(doc, "#year-from", "2025"); await settle();
  assert.match(doc.querySelector("#results").textContent, /No articles match these filters/);
  assert.equal(doc.querySelector("#year-to").value, "2025");
});

test("old model links reset themes safely and absent themes leave issue browsing usable", async () => {
  let {doc} = await mount("?theme=1&run=old-model", {prefix: "/"});
  assert.equal(doc.querySelector("#theme-filter").value, "all");
  assert.match(doc.querySelector("#theme-note").textContent, /earlier model/);
  assert.equal(doc.querySelectorAll(".article-card").length, 4);
  ({doc} = await mount("", {themes: {status: "not_built"}}));
  assert.equal(doc.querySelector("#theme-filter").disabled, true);
  assert.equal(doc.querySelectorAll(".article-card").length, 4);
  ({doc} = await mount("", {failThemes: true}));
  assert.match(doc.querySelector("#theme-note").textContent, /temporarily unavailable/);
  assert.equal(doc.querySelectorAll(".article-card").length, 4);
});


test("collection browsing preserves individual membership, overlap, URL state and combined filters", async () => {
  const {doc} = await mount("?collection_type=special_issue&collection=special-test&volume=36&issue=1&article=paper-b");
  assert.equal(doc.querySelectorAll(".article-card").length, 2);
  assert.equal(doc.querySelector(".article-card").open, true);
  assert.equal(doc.querySelector("#collection-filter").value, "special-test");
  assert.match(location.search, /collection=special-test/);
  change(doc, "#theme-filter", "1"); await settle();
  assert.equal(doc.querySelectorAll(".article-card").length, 1);
  change(doc, "#collection-type", "country_survey"); await settle();
  assert.equal(doc.querySelector("#collection-filter").value, "");
  change(doc, "#theme-filter", "all"); await settle();
  assert.equal(doc.querySelectorAll(".article-card").length, 2); // Overlap is not double-counted.
  change(doc, "#year-from", "2025"); await settle();
  assert.match(doc.querySelector("#results").textContent, /No articles match/);
  doc.querySelector("#reset-filters").click(); await settle();
  assert.equal(doc.querySelectorAll(".article-card").length, 4);
  assert.equal(doc.querySelector("#collection-choice").hidden, true);
  assert.equal(location.search.includes("collection"), false);
});

test("collection links recover from invalid IDs and failed metadata; no topic data is required", async () => {
  let {doc} = await mount("?collection_type=country_survey&collection=missing");
  assert.equal(doc.querySelector("#collection-type").value, "country_survey");
  assert.equal(doc.querySelectorAll(".article-card").length, 2);
  assert.match(doc.querySelector("#collection-note").textContent, /linked collection is unavailable/);
  const options = {failCollections: true};
  ({doc} = await mount("?collection_type=country_survey&collection=country-a", options));
  assert.equal(doc.querySelectorAll(".article-card").length, 4);
  assert.equal(doc.querySelector("#collection-type").disabled, true);
  options.failCollections = false;
  doc.querySelector("#retry-collections").click(); await settle();
  assert.equal(doc.querySelectorAll(".article-card").length, 1);
  assert.equal(doc.querySelector("#collection-filter").value, "country-a");
  ({doc} = await mount("?collection_type=country_survey&collection=country-a", {failThemes: true}));
  assert.equal(doc.querySelector("#theme-filter").disabled, true);
  assert.equal(doc.querySelectorAll(".article-card").length, 1);
  doc.querySelector("#search-input").value = "Author";
  doc.querySelector("#search-form").dispatchEvent(new window.Event("submit", {cancelable: true})); await settle();
  assert.equal(doc.querySelectorAll(".article-card").length, 1);
  assert.equal(doc.querySelector(".article-card").dataset.handle, "paper-b");
});
