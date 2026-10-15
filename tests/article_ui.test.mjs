import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {fixture, papers} from "./topic_fixture.mjs";
import {installDomHelpers, settle} from "./dom_helpers.mjs";

import {parseHTML} from "linkedom";
const html = await readFile(new URL("../site/index.html", import.meta.url), "utf8");
const all = [...papers, {...papers[1], handle: "no-abstract", title: "A missing abstract", topic: null}];
const catalog = {count: all.length, volumes: ["37", "36", "35"].map((number) => ({number, years: [all.find((paper) => paper.volume === number).year], issues: [{number: "1", count: all.filter((paper) => paper.volume === number).length}]}))};
const themes = {status: "ready", run_id: "test-run", stale: false, topics: fixture.topics, articles: all};
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
    const path = url.pathname.split("/api/")[1];
    const json = (data, ok = true) => ({ok, json: async () => structuredClone(data)});
    if (path === "catalog") return json(catalog);
    if (path === "source") return json({});
    if (path === "themes") return json(options.themes || themes, !options.failThemes);
    if (path === "article") {
      const paper = all.find((item) => item.handle === url.searchParams.get("handle"));
      return json({...paper, abstract: "Complete original abstract, including its FINAL WORDS.", journal: "DPE"});
    }
    let selected = all.filter((paper) => (!url.searchParams.has("theme") || paper.topic === Number(url.searchParams.get("theme"))) &&
      (!url.searchParams.has("year_from") || Number(paper.year) >= Number(url.searchParams.get("year_from"))) &&
      (!url.searchParams.has("year_to") || Number(paper.year) <= Number(url.searchParams.get("year_to"))));
    if (path === "issue") return json({articles: selected.filter((paper) => paper.volume === url.searchParams.get("volume"))});
    if (path === "search") return json({results: selected});
    throw new Error(`Unexpected request ${url}`);
  };
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
  assert.equal(requests.filter((url) => url.pathname.endsWith("/api/issue")).at(-1).searchParams.get("run"), "test-run");
  doc.querySelector("#search-input").value = "military spending";
  doc.querySelector("#search-form").dispatchEvent(new window.Event("submit", {cancelable: true})); await settle();
  const search = requests.findLast((url) => url.pathname.endsWith("/api/search"));
  assert.equal(search.searchParams.get("theme"), "1");
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
