import {test, expect} from "@playwright/test";
import {mkdir} from "node:fs/promises";
import {join} from "node:path";
async function screenshot(page, testInfo, name) {
  if (!process.env.DPE_SCREENSHOT_DIR) return;
  await mkdir(process.env.DPE_SCREENSHOT_DIR, {recursive: true});
  await page.screenshot({path: join(process.env.DPE_SCREENSHOT_DIR, `${testInfo.project.name}-${name}.png`)});
}
const prefix = "/dpeindex/";
test("footer links and published Python documentation work under the Pages prefix", async ({page}, info) => {
  for (const path of ["?collection_type=country_survey&collection=country-canada", "topics/", "citations/"]) {
    await page.goto(prefix + path);
    const footer = page.getByRole("navigation", {name: "Footer navigation"});
    await expect(footer.getByRole("link", {name: "GitHub", exact: true})).toHaveAttribute("href", "https://github.com/defenceeconomist/dpeindex");
    await expect(footer.getByRole("link", {name: "defenceeconomist.io", exact: true})).toHaveAttribute("href", "https://defenceeconomist.github.io/");
    await expect(footer.getByRole("link", {name: "Python docs"})).toHaveAttribute("href", /python-docs\/$/);
    await expect(footer.getByRole("link", {name: "Tool guide slides"})).toHaveAttribute("href", /slides\/$/);
    await expect(footer.getByRole("link", {name: "Research report"})).toHaveAttribute("href", /research-note\/$/);
    await expect(page.getByRole("navigation", {name: "Main navigation"}).getByRole("link", {name: /Research/})).toHaveCount(0);
  }
  await page.setViewportSize({width: 390, height: 844});
  await page.locator("footer").scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await screenshot(page, info, "footer-mobile");
  await page.locator("footer").getByRole("link", {name: "Python docs"}).click();
  await expect(page.locator(".body h1")).toContainText("DPE Python documentation");
  await page.goto(prefix + "python-docs/api/mcp_server.html");
  await page.locator(".viewcode-link").first().click();
  await expect(page).toHaveURL(/python-docs\/_modules\/app\/mcp_server.html#dpe_fetch_redif/);
  await expect(page.locator(".viewcode-block").first()).toContainText("async def dpe_fetch_redif");
  await page.goto(prefix + "python-docs/search.html?q=parse_redif");
  await expect(page.locator("#search-results").getByRole("link").first()).toBeVisible();
});

test("verified collections combine with years, survive reload and fit mobile screens", async ({page}, info) => {
  await page.goto(prefix);
  await page.selectOption("#collection-type", "special_issue");
  await page.selectOption("#collection-filter", "special-27-2");
  await expect(page.locator(".article-card")).toHaveCount(6);
  await expect(page.locator("#view-description")).toContainText("6 articles");
  await expect(page.locator("#results")).not.toContainText("Walter Oi");
  await expect(page).toHaveURL(/collection=special-27-2/);
  await page.reload();
  await expect(page.locator("#collection-filter")).toHaveValue("special-27-2");
  await expect(page.locator(".article-card")).toHaveCount(6);
  await page.selectOption("#year-from", "2026");
  await expect(page.locator(".empty")).toContainText("No articles match");
  await page.locator("#reset-filters").click();
  await expect(page.locator("#collection-choice")).toBeHidden();
  await expect(page).not.toHaveURL(/collection/);
  await page.selectOption("#collection-type", "country_survey");
  await page.selectOption("#collection-filter", "country-taiwan");
  await expect(page.locator(".article-card")).toHaveCount(1);
  await expect(page.locator("#results")).toContainText("Military Spending in Taiwan");
  await page.setViewportSize({width: 375, height: 812});
  await expect(page.locator("#collection-filter")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator("#collection-filter").scrollIntoViewIfNeeded();
  await screenshot(page, info, "collections-mobile");
});

test("collection loading recovers and filters work without topic data", async ({page}) => {
  await page.route("**/data/themes.json", route => route.abort());
  await page.route("**/data/collections.json", route => route.abort());
  await page.goto(prefix + "?collection_type=country_survey&collection=country-canada");
  await expect(page.locator("#collection-type")).toBeDisabled();
  await expect(page.locator("#retry-collections")).toBeVisible();
  await expect(page.locator(".article-card").first()).toBeVisible();
  await page.unroute("**/data/collections.json");
  await page.locator("#retry-collections").click();
  await expect(page.locator("#collection-filter")).toHaveValue("country-canada");
  await expect(page.locator(".article-card")).toHaveCount(1);
  await expect(page.locator("#results")).toContainText("Canadian Military Expenditure");
  await expect(page.locator("#theme-filter")).toBeDisabled();
});

test("bare URL resets an older issue's scroll position and explicit issue links still work", async ({page}) => {
  await page.goto(prefix + "?volume=37&issue=5");
  await expect(page.locator("#issue-37-5 .article-card").first()).toBeVisible();
  await expect(page).toHaveURL(/issue=5/);
  await page.evaluate(() => history.replaceState(null, "", location.pathname));
  await page.reload();
  await expect(page.locator("#issue-37-6 .article-card")).toHaveCount(7);
  await expect(page).toHaveURL(/volume=37&issue=6/);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.goto(prefix + "?volume=37&issue=5");
  await expect(page.locator("#issue-37-5 .article-card").first()).toBeVisible();
  await expect(page).toHaveURL(/issue=5/);
});

test("all public views, article details, filters and downloads work on a static server", async ({page, request}, info) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const requests = [];
  page.on("request", (r) => requests.push(r.url()));
  await page.goto(prefix);
  const releaseInfo = await (await request.get(prefix + "release.json")).json();
  const datasetInfo = await (await request.get(prefix + releaseInfo.manifest)).json();
  await expect(page.locator("#article-count")).toHaveText(datasetInfo.article_count.toLocaleString("en-US"));
  await expect(page.locator(".article-card").first()).toBeVisible();
  await page.locator(".article-card summary").first().click();
  await expect(page.locator(".article-card[open] .detail-grid")).toBeVisible();
  expect(requests.some((r) => r.includes(".onnx"))).toBe(false);
  await screenshot(page, info, "articles");
  await page.selectOption("#theme-filter", "-1");
  await expect(page.locator("#view-title")).toHaveText("Articles by theme");
  await page.goto(prefix + "?theme=0&run=old-run");
  await expect(page.locator("#theme-note")).toContainText("earlier model");
  await page.goto(prefix + "topics/");
  await expect(page.locator("#datamap .plot-container")).toBeVisible();
  await expect(page.locator("#topic-timeline .plot-container").first()).toBeVisible();
  await screenshot(page, info, "topics");
  await page.goto(prefix + "citations/");
  if (datasetInfo.citation_run_id) {
  await expect(page.locator("#citation-network .plot-container")).toBeVisible();
  await expect(page.locator("#citation-ranking-rows tr")).toHaveCount(25);
  await screenshot(page, info, "citations");
  await page.selectOption("#citation-page-size", "50");
  await expect(page.locator("#citation-ranking-rows tr")).toHaveCount(50);
  const release = await (await request.get(prefix + "release.json")).json();
  const manifest = await (await request.get(prefix + release.manifest)).json();
  const download = await request.get(prefix + `api/citations/${manifest.citation_run_id}/download/bundle.zip`);
  expect(download.ok()).toBe(true);
  expect((await download.body()).length).toBeGreaterThan(1000);
  } else {
    await expect(page.locator("#citation-status")).toContainText("not included");
  }
  expect(requests.some((r) => /\/api\/(catalog|article|issue|topics|citations)(?:\?|$)/.test(r))).toBe(false);
  expect(errors).toEqual([]);
  if (!process.env.DPE_PUBLIC_URL) {
    await page.goto("/"); await expect(page.locator("#article-count")).toHaveText(datasetInfo.article_count.toLocaleString("en-US"));
  }
});

test("real WASM inference, repeat search, cache, encoder consistency and rapid queries", async ({page}, info) => {
  let inferenceBytes = 0;
  let weightRequests = 0;
  page.on("request", (request) => { if (request.url().endsWith("model_quantized.onnx")) weightRequests++; });
  page.on("response", (response) => {
    if (/\/models\/|\/vendor\/onnx\//.test(response.url())) inferenceBytes += Number(response.headers()["content-length"] || 0);
  });
  await page.goto(prefix);
  const start = Date.now();
  await page.locator("#search-input").fill("economic effects of civil war");
  await page.locator("#search-form button").click();
  await expect(page.locator(".result-count")).toBeVisible({timeout: 100_000});
  await expect(page.locator("#view-kicker")).toHaveText("Semantic search");
  const first = Date.now() - start;
  const initialBytes = inferenceBytes;
  await page.locator("#search-input").fill("military expenditure");
  const repeatStart = Date.now(); await page.locator("#search-form button").click();
  await expect(page.locator(".result-count")).toBeVisible();
  const repeat = Date.now() - repeatStart;
  await page.locator("#search-input").fill("terrorism"); await page.locator("#search-form button").click();
  await page.locator("#search-input").fill("arms trade"); await page.locator("#search-form button").click();
  await expect(page.locator("#view-title")).toHaveText("Results for “arms trade”");
  await expect(page.locator(".result-count")).toBeVisible();
  const beforeCachedEncoder = weightRequests;
  const consistency = await page.evaluate(async (prefix) => {
    const config = (await import(prefix + "site-config.js")).default;
    const dataBase = prefix + config.datasetBase;
    const ref = await (await fetch(dataBase + "data/search-reference.json")).json();
    const worker = new Worker(prefix + "search-worker.js", {type: "module"});
    const vector = await new Promise((resolve, reject) => {
      worker.onmessage = ({data}) => { if (data.vector) resolve(data.vector); else if (data.error) reject(new Error(data.error)); };
      worker.onerror = reject;
      worker.postMessage({id: 1, query: ref.query, modelBase: new URL(prefix + config.modelBase, location.origin).href, runtimeBase: new URL(prefix + "vendor/onnx/", location.origin).href});
    });
    worker.terminate();
    const dot = vector.reduce((sum, value, i) => sum + value * ref.vector[i], 0);
    return {dot, cacheEntries: (await caches.keys()).length};
  }, prefix);
  expect(consistency.dot).toBeGreaterThan(.995);
  expect(consistency.cacheEntries).toBeGreaterThan(0);
  expect(weightRequests).toBe(beforeCachedEncoder);
  await page.selectOption("#collection-type", "country_survey");
  await page.selectOption("#collection-filter", "country-canada");
  await expect(page.locator(".article-card")).toHaveCount(1);
  await expect(page.locator("#results")).toContainText("Canadian Military Expenditure");
  await expect(page.locator("#view-kicker")).toHaveText("Semantic search");
  await info.attach("search-timings", {body: JSON.stringify({first_ms: first, repeat_ms: repeat, initial_inference_bytes: initialBytes, ...consistency}), contentType: "application/json"});
  console.log(`${info.project.name}: first search ${first}ms, repeat ${repeat}ms, initial model/runtime ${(initialBytes / 1e6).toFixed(1)} MB, encoder cosine ${consistency.dot}`);
});

test("model failure offers keyword search and successful retry", async ({page}) => {
  await page.route("**/models/**", (route) => route.abort());
  await page.goto(prefix);
  await page.locator("#search-input").fill("terrorism"); await page.locator("#search-form button").click();
  await expect(page.locator("#view-kicker")).toHaveText("Keyword search");
  await expect(page.locator(".article-card").first()).toBeVisible();
  await page.unroute("**/models/**");
  await page.locator("#retry-semantic").click();
  await expect(page.locator(".result-count")).toBeVisible({timeout: 100_000});
  await expect(page.locator("#view-kicker")).toHaveText("Semantic search");
});

test("mobile navigation and article filters remain usable", async ({page}, info) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto(prefix);
  await expect(page.locator("#volume-select")).toBeVisible();
  await expect(page.locator("#theme-filter")).toBeEnabled();
  await page.selectOption("#year-from", "2020");
  await page.selectOption("#year-to", "2024");
  await expect(page.locator(".article-card").first()).toBeVisible();
  await page.getByRole("link", {name: "Topics", exact: true}).click();
  await expect(page.locator("#datamap .plot-container")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await screenshot(page, info, "mobile");
});


test("Quarto guide opens from the footer and includes a PDF download", async ({page}) => {
  await page.goto(prefix + "?collection_type=country_survey&collection=country-canada");
  await expect(page.locator("#theme-filter")).toBeEnabled();
  await expect(page.locator("#collection-type")).toBeEnabled();
  await expect(page.locator("#results .article-card").first()).toBeVisible();
  await page.locator("footer").getByRole("link", {name: "Tool guide slides"}).click();
  await expect(page).toHaveURL(/slides\//);
  await page.waitForFunction(() => window.Reveal?.isReady());
  expect(await page.locator(".reveal .slides > section").count()).toBe(12);
  await page.evaluate(() => window.Reveal.slide(11));
  await expect(page.getByRole("link", {name: "Download these slides as PDF"})).toBeVisible();
  const pdf = await page.request.get(prefix + "slides/tool-guide.pdf");
  expect(pdf.ok()).toBe(true);
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
});
