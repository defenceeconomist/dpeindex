import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import test from "node:test";
import {citationFixture} from "./citation_fixture.mjs";
import {installDomHelpers, settle} from "./dom_helpers.mjs";

const require = createRequire(import.meta.url);
const {parseHTML} = require(require.resolve("linkedom", {paths: [process.env.DPE_UI_TEST_MODULES || "/tmp/dpe-ui-tests/node_modules"]}));
const html = await readFile(new URL("../site/citations.html", import.meta.url), "utf8");
let scenario = 0;
async function mount(data = citationFixture(), {fail = false, mapFail = false, prefix = "/dpe/", frames = {}, renderer = {}} = {}) {
  const {window} = parseHTML(html);
  globalThis.window = window; globalThis.document = window.document; installDomHelpers(window);
  window.location = {reload() { window.reloaded = true; }};
  frames.pending = [];
  window.requestAnimationFrame = (callback) => {
    if (frames.hold) frames.pending.push(callback);
    else setImmediate(callback);
  };
  renderer.calls = []; renderer.waiters = []; renderer.active = 0; renderer.maxActive = 0;
  const plotly = {async react(element, traces, layout) {
    if (mapFail) throw new Error("Chart unavailable");
    renderer.calls.push({traces, layout}); renderer.active++;
    renderer.maxActive = Math.max(renderer.maxActive, renderer.active);
    if (renderer.hold) await new Promise((resolve) => renderer.waiters.push(resolve));
    element.plotData = traces; element.plotLayout = layout; element.events ||= {};
    element.on = (name, handler) => { element.events[name] = handler; };
    renderer.active--;
  }};
  globalThis.Plotly = window.Plotly = plotly;
  globalThis.fetch = async (url) => { assert.equal(new URL(url, `http://example.test${prefix}citations/`).pathname, `${prefix}api/citations`); return {ok: !fail, json: async () => structuredClone(data)}; };
  await import(new URL(`../site/citations.js?scenario=${++scenario}`, import.meta.url)); await settle(); return window.document;
}
const plot = (doc) => doc.querySelector("#citation-network");
const nodeTraces = (doc) => plot(doc).plotData.filter((trace) => trace.customdata && trace.hoverinfo !== "skip");
const handles = (doc) => nodeTraces(doc).flatMap((trace) => trace.customdata.map((entry) => entry[0]));
const rankedHandles = (doc) => [...doc.querySelectorAll("#citation-ranking-rows [data-paper]")].map((button) => button.dataset.paper);
function lineSegments(doc) {
  return plot(doc).plotData.filter((trace) => trace.mode === "lines").flatMap((trace) => {
    const segments = []; let points = [];
    trace.x.forEach((x, i) => {
      if (x === null) { if (points.length) segments.push(points); points = []; }
      else points.push({x, y: trace.y[i]});
    });
    return segments;
  });
}
const arrows = (doc) => lineSegments(doc).filter((segment) => segment.length === 3);
async function input(doc, selector, value, event = "input") {
  const control = doc.querySelector(selector); control.value = String(value);
  control.dispatchEvent(new window.Event(event)); await settle();
}

test("compact page removes search and lower panels and puts labelled sliders below the chart", async () => {
  const doc = await mount();
  assert.equal(doc.querySelector("#citation-explorer").hidden, false);
  for (const selector of ["#citation-search", "#citation-papers", "#citation-timeline", "#citation-comparison", "#citation-downloads", "#citation-topic-changed"]) assert.equal(doc.querySelector(selector), null);
  assert.equal(doc.querySelector(".citation-workspace").nextElementSibling.id, "citation-rankings");
  assert.equal(doc.querySelector("#citation-rankings").nextElementSibling, null);
  assert.equal(plot(doc).nextElementSibling.className, "citation-sliders");
  for (const id of ["citation-minimum", "citation-from", "citation-to"]) {
    assert.equal(doc.querySelector(`#${id}`).type, "range");
    assert.ok(doc.querySelector(`label[for="${id}"]`));
    assert.ok(doc.querySelector(`output[for="${id}"]`));
    assert.ok(doc.querySelector(`#${id}`).getAttribute("aria-valuetext"));
  }
  assert.equal(doc.querySelectorAll("#citation-ranking-controls button[aria-pressed=true]").length, 1);
  assert.ok(doc.querySelector(".citation-table-scroll[tabindex='0']"));
  assert.equal(doc.querySelector("#citation-ranking-rows script"), null);
  assert.match(nodeTraces(doc)[0].customdata[0][1], /&lt;script&gt;/);
});

test("all visible links have stronger lines and arrows pointing from citing to cited paper", async () => {
  const data = citationFixture(), doc = await mount(data);
  await input(doc, "#citation-minimum", 0);
  const chart = plot(doc);
  assert.equal(chart.plotData.length, 5);
  assert.equal(chart.plotLayout.annotations, undefined);
  assert.equal(arrows(doc).length, data.edges.length);
  assert.equal(chart.plotData[0].line.width, 1.4);
  assert.equal(chart.plotData[0].line.color, "rgba(45,89,94,.55)");
  data.edges.forEach((edge, i) => {
    const s = data.nodes.find((n) => n.handle === edge.source), t = data.nodes.find((n) => n.handle === edge.target);
    const [left, tip, right] = arrows(doc)[i];
    const baseX = (left.x + right.x) / 2, baseY = (left.y + right.y) / 2;
    assert.ok((tip.x - baseX) * (t.x - s.x) + (tip.y - baseY) * (t.y - s.y) > 0);
    assert.equal(tip.x, s.x + (t.x - s.x) * .75);
    assert.equal(tip.y, s.y + (t.y - s.y) * .75);
  });
  await input(doc, "#citation-edges", "baseline", "change");
  assert.equal(arrows(doc).length, 1);
  assert.match(doc.querySelector("#citation-visible").textContent, /1 visible links/);
});

test("selection highlights incoming/outgoing citations and reveals a filtered paper with its reader link", async () => {
  const doc = await mount();
  doc.querySelector('#citation-ranking-rows [data-paper="RePEc:test:alpha"]').click(); await settle();
  assert.match(doc.querySelector("#citation-selection").textContent, /Cites this paper \(1\)/);
  assert.match(doc.querySelector("#citation-selection").textContent, /Cited by this paper \(1\)/);
  const link = new URL(doc.querySelector("#citation-selection a").getAttribute("href"), "http://example.test/dpe/citations/");
  assert.equal(link.pathname, "/dpe/"); assert.equal(link.searchParams.get("article"), "RePEc:test:alpha");
  assert.equal(link.searchParams.get("volume"), "11"); assert.equal(link.searchParams.has("theme"), false);
  doc.querySelector('#citation-selection [data-paper="RePEc:test:beta"]').click(); await settle();
  assert.equal(doc.querySelector("#citation-minimum").value, "0");
  assert.match(doc.querySelector("#citation-selection h3").textContent, /Conflict and security/);
  assert.deepEqual(plot(doc).plotData.filter((trace) => trace.mode === "lines" && trace.x.length).map((trace) => trace.line.color).sort(), ["#237b7b", "#c66b46"]);
  assert.equal(plot(doc).plotData.find((trace) => trace.name === "Cites this paper").line.width, 2.5);
  assert.equal(doc.querySelector('#citation-ranking-rows [data-paper="RePEc:test:beta"]').getAttribute("aria-pressed"), "true");
  doc.querySelector("#citation-clear-paper").click(); await settle();
  assert.equal(arrows(doc).length, 2);
  assert.equal(plot(doc).plotData[0].line.color, "rgba(45,89,94,.55)");
  plot(doc).events.plotly_click({points: [{customdata: ["RePEc:test:alpha"]}]}); await settle();
  assert.match(doc.querySelector("#citation-selection h3").textContent, /Growth/);
});

test("citation slider defaults to three incoming citations and supports outgoing, total, zero and reset", async () => {
  const data = citationFixture(); data.nodes[0].in_degree = 3; data.nodes[1].in_degree = 2;
  const doc = await mount(data), minimum = doc.querySelector("#citation-minimum");
  assert.equal(minimum.value, "3"); assert.equal(minimum.max, "3");
  assert.deepEqual(handles(doc), [data.nodes[0].handle]);
  await input(doc, "#citation-minimum", 2);
  assert.equal(handles(doc).length, 2); assert.equal(doc.querySelector("#citation-minimum-value").textContent, "2");
  await input(doc, "#citation-count-type", "out_degree", "change");
  await input(doc, "#citation-minimum", 4);
  assert.equal(minimum.max, "4"); assert.deepEqual(handles(doc), [data.nodes[0].handle]);
  assert.equal(doc.querySelector("#citation-minimum-label").textContent, "Minimum citations out");
  await input(doc, "#citation-count-type", "degree", "change");
  await input(doc, "#citation-minimum", 10);
  assert.equal(minimum.max, "12"); assert.equal(handles(doc).length, 1);
  assert.match(doc.querySelector("#citation-visible").textContent, /minimum 10 citations in \+ out/);
  await input(doc, "#citation-minimum", 0); assert.equal(handles(doc).length, 3);
  doc.querySelector("#citation-reset").click(); await settle();
  assert.equal(minimum.value, "3"); assert.equal(doc.querySelector("#citation-count-type").value, "in_degree");
  doc.querySelector('[data-community="-1"]').click(); await settle();
  assert.equal(minimum.value, "0"); assert.deepEqual(handles(doc), [data.nodes[2].handle]);
});

test("year sliders filter inclusively, show live years and keep the year interval ordered", async () => {
  const doc = await mount(), from = doc.querySelector("#citation-from"), to = doc.querySelector("#citation-to");
  assert.equal(from.min, "2000"); assert.equal(from.max, "2006"); assert.equal(to.value, "2006");
  await input(doc, "#citation-minimum", 0);
  await input(doc, "#citation-from", 2005);
  assert.deepEqual(handles(doc), ["RePEc:test:beta", "RePEc:test:gamma"]);
  assert.equal(doc.querySelector("#citation-from-value").textContent, "2005");
  await input(doc, "#citation-to", 2005); assert.deepEqual(handles(doc), ["RePEc:test:beta"]);
  await input(doc, "#citation-from", 2006);
  assert.equal(to.value, "2006"); assert.equal(doc.querySelector("#citation-to-value").textContent, "2006");
  await input(doc, "#citation-to", 2000);
  assert.equal(from.value, "2000"); assert.deepEqual(handles(doc), ["RePEc:test:alpha"]);
  // Year and count filters never recompute communities or coordinates.
  assert.deepEqual(nodeTraces(doc)[0].x, [0]); assert.equal(nodeTraces(doc)[0].customdata[0][5], "Growth");
  doc.querySelector("#citation-reset").click(); await settle();
  assert.equal(from.value, "2000"); assert.equal(to.value, "2006");
  await input(doc, "#citation-from", 2001); await input(doc, "#citation-to", 2001);
  assert.match(doc.querySelector("#citation-visible").textContent, /0 visible papers/);
  assert.equal(doc.querySelectorAll("#citation-ranking-rows [data-paper]").length, 3);
  doc.querySelector('#citation-ranking-rows [data-paper="RePEc:test:beta"]').click(); await settle();
  assert.equal(from.value, "2000"); assert.equal(to.value, "2006"); assert.equal(doc.querySelector("#citation-minimum").value, "0");
});

test("three top-20 rankings sort independently and remain whole-study rankings under filters", async () => {
  const data = citationFixture(), base = data.nodes[0];
  data.nodes = Array.from({length: 24}, (_, i) => ({...base, handle: `paper-${String(i).padStart(2, "0")}`, title: `Paper ${i}`, in_degree: i, out_degree: (23 - i) * 2, degree: 46 - i})).reverse();
  data.edges = [];
  data.nodes[0].in_degree = data.nodes[1].in_degree; // Tie resolved by total degree, then stable handle.
  const doc = await mount(data);
  for (const metric of ["in_degree", "out_degree", "degree"]) {
    doc.querySelector(`[data-ranking="${metric}"]`).click(); await settle();
    const expected = [...data.nodes].sort((a, b) => b[metric] - a[metric] || b.degree - a.degree || a.handle.localeCompare(b.handle)).slice(0, 20);
    assert.deepEqual(rankedHandles(doc), expected.map((node) => node.handle));
    assert.equal(doc.querySelectorAll("#citation-ranking-rows tr").length, 20);
    const cells = doc.querySelector("#citation-ranking-rows tr").children;
    assert.deepEqual([cells[2].textContent, cells[3].textContent, cells[4].textContent], [expected[0].in_degree, expected[0].out_degree, expected[0].degree].map(String));
    assert.equal(doc.querySelectorAll("#citation-ranking-controls [aria-pressed=true]").length, 1);
    const before = rankedHandles(doc);
    await input(doc, "#citation-minimum", 20);
    await input(doc, "#citation-edges", "baseline", "change");
    doc.querySelector('[data-community="1"]').click(); await settle();
    assert.deepEqual(rankedHandles(doc), before);
    assert.equal(handles(doc).length, 0);
  }
  const reader = new URL(doc.querySelector("#citation-ranking-rows a").getAttribute("href"), "http://example.test/dpe/citations/");
  assert.equal(reader.pathname, "/dpe/"); assert.equal(reader.searchParams.get("article"), rankedHandles(doc)[0]);
});

test("baseline and community filters retain coordinates and published community membership", async () => {
  const data = citationFixture(), doc = await mount(data);
  await input(doc, "#citation-minimum", 0);
  const positions = nodeTraces(doc).map((t) => ({x: t.x, y: t.y, name: t.name, colour: t.marker.color}));
  await input(doc, "#citation-edges", "baseline", "change");
  assert.deepEqual(nodeTraces(doc).map((t) => ({x: t.x, y: t.y, name: t.name, colour: t.marker.color})), positions);
  doc.querySelector('[data-community="1"]').click(); await settle();
  assert.deepEqual(handles(doc), [data.nodes[1].handle]); assert.deepEqual(nodeTraces(doc)[0].x, [.5]);
  assert.equal(doc.querySelector('[data-community="1"]').getAttribute("aria-pressed"), "true");
  assert.match(doc.querySelector("#citation-selection").textContent, /Conflict studies/);
  assert.match(doc.querySelector("#citation-stats").textContent, /2observed citation links/);
});

test("not built, service failure, map failure, catalogue change and standalone routes", async () => {
  let doc = await mount({status: "not_built"});
  assert.match(doc.querySelector("#citation-status").textContent, /being prepared/); assert.equal(doc.querySelector("#citation-explorer").hidden, true);
  doc = await mount(citationFixture(), {fail: true}); doc.querySelector("#citation-retry").click(); assert.equal(window.reloaded, true);
  doc = await mount(citationFixture(), {mapFail: true});
  assert.match(doc.querySelector("#citation-status").textContent, /top-20 rankings/); assert.equal(rankedHandles(doc).length, 3);
  const data = citationFixture(); data.stale = true; data.topic_changed = true;
  doc = await mount(data, {prefix: "/"});
  assert.equal(doc.querySelector("#citation-stale").hidden, false);
  assert.equal(doc.querySelector("#citation-topic-changed"), null);
  const link = new URL(doc.querySelector("#citation-ranking-rows a").getAttribute("href"), "http://example.test/citations/");
  assert.equal(link.pathname, "/");
});

test("published citation snapshot supplies all arrows, year bounds and exact top-20 rankings", {skip: !process.env.DPE_CITATION_SNAPSHOT}, async () => {
  const data = JSON.parse(await readFile(process.env.DPE_CITATION_SNAPSHOT, "utf8"));
  const doc = await mount(data), visible = data.nodes.filter((node) => node.in_degree >= 3), visibleHandles = new Set(visible.map((node) => node.handle));
  assert.equal(handles(doc).length, visible.length);
  assert.equal(arrows(doc).length, data.edges.filter((edge) => visibleHandles.has(edge.source) && visibleHandles.has(edge.target)).length);
  assert.equal(plot(doc).plotData.length, 5); assert.equal(plot(doc).plotLayout.annotations, undefined);
  assert.equal(doc.querySelector("#citation-from").value, "1997"); assert.equal(doc.querySelector("#citation-to").value, "2026");
  for (const metric of ["in_degree", "out_degree", "degree"]) {
    doc.querySelector(`[data-ranking="${metric}"]`).click(); await settle();
    assert.deepEqual(rankedHandles(doc), [...data.nodes].sort((a, b) => b[metric] - a[metric] || b.degree - a.degree || a.handle.localeCompare(b.handle)).slice(0, 20).map((node) => node.handle));
  }
  await input(doc, "#citation-minimum", 0);
  assert.equal(handles(doc).length, data.nodes.length);
  assert.equal(arrows(doc).length, data.edges.length);
  assert.equal(plot(doc).plotData.length, 5);
});

test("rapid slider inputs update labels immediately and coalesce into one latest frame", async () => {
  const frames = {}, renderer = {}, doc = await mount(citationFixture(), {frames, renderer});
  assert.equal(renderer.calls.length, 1);
  frames.hold = true;
  const minimum = doc.querySelector("#citation-minimum");
  for (let i = 0; i < 80; i++) {
    minimum.value = String(i % 9); minimum.dispatchEvent(new window.Event("input"));
  }
  minimum.value = "0"; minimum.dispatchEvent(new window.Event("input"));
  minimum.dispatchEvent(new window.Event("change")); // Release must not duplicate the input draw.
  assert.equal(doc.querySelector("#citation-minimum-value").textContent, "0");
  assert.match(doc.querySelector("#citation-visible").textContent, /3 visible papers/);
  assert.equal(renderer.calls.length, 1); assert.equal(frames.pending.length, 1);
  assert.equal(plot(doc).getAttribute("aria-busy"), "true");
  frames.hold = false; frames.pending.shift()(); await settle();
  assert.equal(renderer.calls.length, 2); assert.equal(handles(doc).length, 3);
  assert.equal(plot(doc).getAttribute("aria-busy"), "false");
  // Keyboard/change-only updates receive the same filtering behaviour.
  await input(doc, "#citation-from", 2005, "change");
  assert.deepEqual(handles(doc), ["RePEc:test:beta", "RePEc:test:gamma"]);
});

test("slider changes during an active redraw keep only the newest view and never overlap draws", async () => {
  const renderer = {}, doc = await mount(citationFixture(), {renderer});
  renderer.hold = true;
  await input(doc, "#citation-minimum", 0);
  assert.equal(renderer.calls.length, 2); assert.equal(renderer.waiters.length, 1);
  for (let i = 0; i < 40; i++) await input(doc, "#citation-minimum", i % 9);
  await input(doc, "#citation-minimum", 4);
  assert.equal(doc.querySelector("#citation-minimum-value").textContent, "4");
  assert.equal(renderer.calls.length, 2);
  renderer.hold = false; renderer.waiters.shift()(); await settle();
  assert.equal(renderer.calls.length, 3); assert.equal(renderer.maxActive, 1);
  assert.deepEqual(handles(doc), ["RePEc:test:alpha"]);
  assert.equal(plot(doc).getAttribute("aria-busy"), "false");
});
