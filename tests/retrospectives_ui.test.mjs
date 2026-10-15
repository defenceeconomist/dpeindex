import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";
import {mountComparison} from "../site/retrospectives.js";
import {comparisonFixture} from "./retrospectives_fixture.mjs";

const require = createRequire(import.meta.url);
const {parseHTML} = require(require.resolve("linkedom", {paths: [process.env.DPE_UI_TEST_MODULES || "/tmp/dpe-ui-tests/node_modules"]}));

function mount(data = comparisonFixture()) {
  const {document} = parseHTML('<section id="retrospectives" hidden></section>');
  const container = document.querySelector("section");
  const selected = [];
  mountComparison(container, data, (handle) => selected.push(handle));
  return {container, selected};
}

test("period selector, coverage, independent share charts and all crosswalk rows", () => {
  const {container} = mount();
  assert.equal(container.hidden, false);
  assert.equal(container.querySelector('[data-period="third"]').getAttribute("aria-pressed"), "true");
  let content = container.querySelector("#comparison-period-content");
  assert.match(content.textContent, /407/);
  assert.match(content.textContent, /381 usable abstracts, including 23 unassigned/);
  assert.match(content.textContent, /389 classified papers/);
  assert.equal(content.querySelectorAll(".editorial-bars .share-row").length, 18);
  assert.equal(content.querySelectorAll(".model-bars .share-row").length, 11);
  assert.match(content.querySelector(".unassigned-share").textContent, /23 \/ 381 · 6.0%/);
  assert.match(content.textContent, /not matched pairs/);
  assert.equal(content.querySelectorAll("tbody tr").length, 18);
  assert.ok(content.querySelector(".not_resolved"));
  assert.match(content.textContent, /must never be added/);
  assert.match(content.textContent, /sanctions, policing, piracy/);

  container.querySelector('[data-period="first"]').click();
  content = container.querySelector("#comparison-period-content");
  assert.equal(container.querySelector('[data-period="third"]').getAttribute("aria-pressed"), "false");
  assert.match(content.textContent, /Incomplete first-decade sample/);
  assert.match(content.textContent, /Volumes 1–7 are unavailable/);
  assert.match(content.textContent, /Volumes 10\(3–4\).*excluded/);
  assert.match(content.textContent, /45 usable abstracts, including 7 unassigned/);
  assert.match(content.textContent, /207 classified papers/);
  assert.match(content.textContent, /208/);
  assert.equal(content.querySelectorAll("tbody tr").length, 21);
  assert.match(content.querySelector(".unassigned-share").textContent, /7 \/ 45 · 15.6%/);

  container.querySelector('[data-period="second"]').click();
  content = container.querySelector("#comparison-period-content");
  assert.equal(content.querySelectorAll("tbody tr").length, 16);
  assert.match(content.textContent, /330 usable abstracts, including 26 unassigned/);
  assert.match(content.textContent, /331 classified papers/);
});

test("reviewed paper selection and primary source DOI citations", () => {
  const {container, selected} = mount();
  container.querySelector('[data-comparison-handle="paper-a"]').click();
  assert.deepEqual(selected, ["paper-a"]);
  const references = [...container.querySelectorAll(".comparison-references a")].map((link) => link.getAttribute("href"));
  assert.deepEqual(references, ["https://doi.org/10.1080/10430710008404935", "https://doi.org/10.1080/10242694.2010.512195", "https://doi.org/10.1080/10242694.2020.1761221"]);
  assert.match(container.textContent, /Table I, p. 398/);
  assert.match(container.textContent, /cannot establish the first decade’s overall thematic distribution/);
  assert.match(container.textContent, /classification accuracy/);
});

test("new active model preserves dated interpretation and disables old article mappings", () => {
  const data = {...comparisonFixture(), status: "review_required", links_enabled: false};
  const {container, selected} = mount(data);
  assert.match(container.textContent, /Review needed after a model rebuild/);
  assert.match(container.textContent, /20261015T112751Z-f48f8afb/);
  assert.match(container.textContent, /389 classified papers/);
  for (const button of container.querySelectorAll("[data-comparison-handle]")) {
    assert.equal(button.disabled, true);
    button.click();
  }
  assert.deepEqual(selected, []);
  container.querySelector('[data-period="first"]').click();
  assert.equal(container.querySelector("[data-comparison-handle]").disabled, true);
  assert.equal(container.querySelectorAll(".comparison-references a").length, 3);
});

test("missing or unavailable comparison is contained within its own section", () => {
  assert.equal(mount(undefined).container.hidden, false); // default fixture
  assert.equal(mount(null).container.hidden, true);
  assert.equal(mount({status: "not_built"}).container.hidden, true);
  const {container} = mount({status: "unavailable", message: "The archive is unavailable; the explorer remains available."});
  assert.match(container.textContent, /explorer remains available/);
  assert.equal(container.querySelectorAll("button").length, 0);
});
