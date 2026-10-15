import {readFile, writeFile} from "node:fs/promises";
import {resolve, join} from "node:path";
import {SEARCH_MODEL, encodingOptions} from "../site/search-model.js";
import {rankArticles} from "../site/search-ranking.js";
import {env, pipeline} from "@huggingface/transformers";
const root = resolve(process.argv[2]);
const state = JSON.parse(await readFile(join(root, "state.json")));
env.allowRemoteModels = false;
env.localModelPath = join(root, "cache/static/models", SEARCH_MODEL.revision) + "/";
const encoder = await pipeline("feature-extraction", SEARCH_MODEL.id, {dtype: SEARCH_MODEL.dtype, revision: SEARCH_MODEL.revision, device: "cpu"});
const queries = ["economic effects of civil war", "military expenditure", "terrorism", "arms trade"];
const vectors = await Promise.all(queries.map(async (q) => Array.from((await encoder(SEARCH_MODEL.query_prefix + q, encodingOptions)).data)));
await encoder.dispose();
const results = {};
for (const releasePath of state.releases) {
  const directory = resolve(releasePath);
  const release = JSON.parse(await readFile(join(directory, "site/release.json")));
  const base = resolve(directory, "site", release.manifest, "../..");
  const articles = JSON.parse(await readFile(join(base, "data/search.json")));
  const themes = JSON.parse(await readFile(join(base, "data/themes.json")));
  const bytes = await readFile(join(base, "data/embeddings.bin"));
  const matrix = new Float32Array(bytes.length / 4);
  for (let i = 0; i < matrix.length; i++) matrix[i] = bytes.readFloatLE(i * 4);
  results[directory.split("/").at(-1)] = Object.fromEntries(queries.map((q, i) => [q,
    rankArticles(articles, q, new URLSearchParams(), themes, vectors[i], matrix).results.slice(0, 10).map((a) => a.handle)]));
}
await writeFile(join(root, "search-comparison.json"), JSON.stringify(results, null, 2));
