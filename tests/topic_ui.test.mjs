import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import test from "node:test";
import {comparisonFixture} from "./retrospectives_fixture.mjs";
import {fixture} from "./topic_fixture.mjs";
import {installDomHelpers, settle} from "./dom_helpers.mjs";

const require = createRequire(import.meta.url);
const {parseHTML} = require(require.resolve("linkedom", {paths: [process.env.DPE_UI_TEST_MODULES || "/tmp/dpe-ui-tests/node_modules"]}));
const html = await readFile(new URL("../site/topics.html", import.meta.url), "utf8");
let scenario = 0;

async function mount(data = fixture, fail = false, mobile = false) {
  const {window} = parseHTML(html);
  globalThis.window = window; globalThis.document = window.document;
  installDomHelpers(window);
  window.matchMedia = () => ({matches: mobile});
  globalThis.location = {assign(url) { globalThis.destination = url; }, reload() { globalThis.didReload = true; }};
  const plotly = {async react(element, traces, layout) {
    element.plotData = traces; element.plotLayout = layout; element.events ||= {};
    element.on = (name, handler) => { element.events[name] = handler; }; return element;
  }};
  globalThis.Plotly = window.Plotly = plotly;
  globalThis.fetch = async (raw) => {
    assert.equal(new URL(raw, "http://example.test/dpe/topics/").pathname, "/dpe/api/topics");
    return {ok: !fail, json: async () => structuredClone(data)};
  };
  await import(new URL(`../site/topics.js?scenario=${++scenario}`, import.meta.url));
  await settle(); return window.document;
}

test("method text distinguishes guided runs and includes alignment diagnostics", async () => {
  const data = structuredClone(fixture);
  data.metadata.guidance = {manifest: {topics: Array.from({length: 20}, () => ({}))}};
  data.report.selection = "No topic-count preference.";
  data.report.comparison = {};
  data.report.trials = Array.from({length: 54}, () => ({}));
  for (const key of ["baseline", "selected_before_reassignment", "selected_after_reassignment"]) data.report[key].alignment = .5;
  const doc = await mount(data);
  const method = doc.querySelector("#model-method").textContent;
  assert.match(method, /20 consolidated themes from the three retrospectives/);
  assert.match(method, /matched unseeded search/);
  assert.match(method, /No topic-count preference/);
  assert.match(method, /Retrospective alignment/);
  assert.doesNotMatch(method, /favours 10–20/);
});

test("map and hierarchy link to Articles, with no research reader on Topics", async () => {
  const doc = await mount();
  assert.equal(doc.querySelector("#explorer").hidden, false);
  assert.equal(doc.querySelector("#paper-detail"), null);
  assert.equal(doc.querySelector("#paper-list"), null);
  assert.doesNotMatch(doc.body.textContent, /Read the research/);
  const map = doc.querySelector("#datamap");
  assert.equal(map.plotData.reduce((sum, trace) => sum + trace.x.length, 0), 3);
  assert.match(map.plotData[1].customdata[0][1], /&lt;trade&gt;/);
  doc.querySelector('[data-topic="0"]').click(); await settle();
  assert.match(doc.querySelector("#selection-summary").textContent, /war · peace · 1 abstract/);
  assert.equal(map.plotData[1].marker.opacity, .10);
  assert.match(doc.querySelector("#selection-links a").href, /theme=0&run=test-run/);
  doc.querySelector("#hierarchy").events.plotly_click({points: [{customdata: ["2"]}]}); await settle();
  assert.equal(doc.querySelectorAll("#selection-links a").length, 2);
  assert.match(doc.querySelector("#selection-summary").textContent, /All related themes · 2 abstracts/);
  map.events.plotly_click({points: [{customdata: ["paper-b"]}]}); await settle();
  const params = new URL(doc.querySelector("#selected-article-link").href, "http://example.test/dpe/topics/").searchParams;
  assert.equal(params.get("article"), "paper-b");
  assert.equal(params.get("theme"), "1");
  assert.equal(params.get("volume"), "36");
  assert.equal(doc.querySelector('[data-topic="1"]').dataset.focused, "true");
  assert.equal(map.plotData.at(-1).customdata[0][0], "paper-b");
  doc.querySelector('[data-topic="-1"]').click(); await settle();
  assert.match(doc.querySelector("#selection-links a").href, /theme=-1/);
  doc.querySelector("#reset-selection").click(); await settle();
  assert.equal(doc.querySelector("#selected-article-link").hidden, true);
});

test("temporal charts, counts/shares, periods and linked theme highlighting", async () => {
  const doc = await mount();
  const trends = doc.querySelector("#theme-trends"), heatmap = doc.querySelector("#theme-heatmap");
  assert.equal(trends.plotData.length, 3);
  assert.equal(heatmap.plotData[0].z.length, 3);
  assert.deepEqual(trends.plotData[0].y, [0, 100]);
  assert.deepEqual(trends.plotData[2].y, [50, 0]);
  assert.match(doc.querySelector("#timeline-period-summary").textContent, /2025–2029 · partial period/);
  const measure = doc.querySelector("#timeline-measure"); measure.value = "count";
  measure.dispatchEvent(new window.Event("change")); await settle();
  assert.deepEqual(trends.plotData[0].y, [0, 1]);
  const grouping = doc.querySelector("#timeline-group"); grouping.value = "year";
  grouping.dispatchEvent(new window.Event("change")); await settle();
  assert.deepEqual(trends.plotData[0].x, ["2023", "2024", "2025"]);
  heatmap.events.plotly_click({points: [{customdata: [1, 2024]}]}); await settle();
  assert.equal(doc.querySelector('[data-topic="1"]').getAttribute("aria-pressed"), "true");
  assert.equal(doc.querySelectorAll("#timeline-words tr").length, 1);
  assert.match(doc.querySelector("#timeline-words").textContent, /period-2024/);
  const link = new URL(doc.querySelector("#timeline-words a").href, "http://example.test/dpe/topics/");
  assert.equal(link.searchParams.get("year_from"), "2024");
  assert.equal(link.searchParams.get("year_to"), "2024");
  assert.equal(link.searchParams.get("run"), "test-run");
  doc.querySelector("#reset-selection").click(); await settle();
  assert.equal(doc.querySelectorAll("#timeline-words tr").length, 3);
});

test("comparison example links open the matching article on the main page", async () => {
  const doc = await mount({...fixture, comparison: comparisonFixture()});
  doc.querySelector('[data-comparison-handle="paper-b"]').click();
  const destination = new URL(globalThis.destination, "http://example.test/dpe/topics/");
  assert.equal(destination.pathname, "/dpe/");
  assert.equal(destination.searchParams.get("article"), "paper-b");
  assert.equal(destination.searchParams.get("theme"), "1");
});

test("not-built, stale, temporal failure, mobile charts and API error states", async () => {
  let doc = await mount({status: "not_built"});
  assert.match(doc.querySelector("#page-status").textContent, /being prepared/);
  doc = await mount({...fixture, stale: true, timeline: {status: "unavailable", message: "Time analysis unavailable"}}, false, true);
  assert.equal(doc.querySelector("#stale-notice").hidden, false);
  assert.match(doc.querySelector("#topic-timeline").textContent, /Time analysis unavailable/);
  assert.ok(doc.querySelector("#datamap").plotData);
  doc = await mount(fixture, false, true);
  assert.equal(doc.querySelector("#theme-heatmap").plotLayout.margin.l, 112);
  const originalError = console.error; console.error = () => {};
  try { doc = await mount(fixture, true); doc.querySelector("#retry-load").click(); assert.equal(globalThis.didReload, true); }
  finally { console.error = originalError; }
});

test("full published snapshot populates the linked charts", {skip: !process.env.DPE_TOPIC_SNAPSHOT}, async () => {
  const data = JSON.parse(await readFile(process.env.DPE_TOPIC_SNAPSHOT, "utf8"));
  const doc = await mount({...data, status: "ready", stale: false});
  const traces = doc.querySelector("#datamap").plotData;
  assert.equal(traces.reduce((sum, trace) => sum + trace.x.length, 0), data.documents.length);
  assert.equal(new Set(traces.flatMap((trace) => trace.customdata.map((row) => row[0]))).size, data.documents.length);
  assert.equal(doc.querySelector("#hierarchy").plotData[1].customdata.length, data.hierarchy.length);
  if (data.timeline?.status === "ready") {
    assert.equal(doc.querySelector("#theme-trends").plotData.length, data.topics.length + 1);
    assert.equal(doc.querySelectorAll("#timeline-words tr").length, data.topics.length + 1);
  }
});
