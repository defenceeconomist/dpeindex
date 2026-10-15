import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {cachedVectors, vectorKey} from "../scripts/search-vector-cache.mjs";
import {matchesFilters} from "../site/search-ranking.js";
import {spawnSync} from "node:child_process";

test("custom static output cannot replace tracked website or catalogue sources", () => {
  for (const directory of ["site", "data", ".git"]) {
    const result = spawnSync(process.execPath, ["scripts/build-static.mjs", "--dataset", "/missing-dataset", "--output", directory], {encoding: "utf8"});
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Unsafe output directory/);
  }
});

test("per-document cache encodes only arrivals and corrections and repairs corrupt entries", async () => {
  const dir = await mkdtemp(join(tmpdir(), "dpe-vectors-"));
  try {
    const model = {revision: "pinned", dimensions: 2, pooling: "cls", max_length: 512};
    let calls = 0;
    const encode = async () => { calls++; return [1, 0]; };
    const first = await cachedVectors([{text: "a"}, {text: "b"}], model, dir, encode);
    assert.equal(first.metrics.encoded, 2);
    const second = await cachedVectors([{text: "a"}, {text: "b"}, {text: "c"}], model, dir, encode);
    assert.equal(second.metrics.encoded, 1); assert.equal(second.metrics.cache_hits, 2);
    assert.deepEqual(second.binary.subarray(0, first.binary.length), first.binary);
    await cachedVectors([{text: "a"}, {text: "b"}, {text: "c"}], model, dir, encode);
    assert.equal(calls, 3);
    await writeFile(join(dir, vectorKey("a", model) + ".bin"), Buffer.alloc(8));
    const repaired = await cachedVectors([{text: "a"}, {text: "b revised"}], model, dir, encode);
    assert.equal(repaired.metrics.encoded, 2); assert.equal(repaired.metrics.corrupt_entries, 1);
    const revision = await cachedVectors([{text: "a"}], {...model, revision: "new"}, dir, encode);
    assert.equal(revision.metrics.encoded, 1);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test("stable model links survive a new assignment snapshot and reject a different model", () => {
  const article = {handle: "a", year: "2026"};
  const themes = {status: "ready", model_id: "baseline", run_id: "new-snapshot", topics: [{id: 0}], articles: [{handle: "a", topic: 0}]};
  assert.equal(matchesFilters(article, new URLSearchParams({theme: "0", run: "baseline"}), themes), true);
  assert.equal(matchesFilters(article, new URLSearchParams({theme: "0", run: "new-snapshot"}), themes), true);
  assert.throws(() => matchesFilters(article, new URLSearchParams({theme: "0", run: "different-model"}), themes), /model has changed/);
});
