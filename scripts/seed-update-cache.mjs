// Import only baseline documents from the existing index; never seed held-out papers.
import {cp, mkdir, readFile, writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {resolve, join} from "node:path";
import {SEARCH_MODEL} from "../site/search-model.js";
import {vectorKey, validVector} from "./search-vector-cache.mjs";

const workspace = resolve(process.argv[2]);
const corpus = JSON.parse(await readFile(resolve(process.argv[3])));
const allowed = new Set(corpus.map((a) => a.handle));
const cache = join(workspace, "cache/static");
await mkdir(join(cache, "vectors"), {recursive: true});
try { await cp(".static-cache/models", join(cache, "models"), {recursive: true, force: false, errorOnExist: false}); }
catch (error) { if (error.code !== "ENOENT") throw error; }
let seeded = 0;
try {
  const release = JSON.parse(await readFile("dist/release.json"));
  const manifest = JSON.parse(await readFile(join("dist", release.manifest)));
  if (!Object.entries(SEARCH_MODEL).every(([key, value]) => manifest.search[key] === value)) throw new Error("Existing search model differs; cannot seed");
  const base = resolve("dist", release.manifest, "../..");
  const articles = JSON.parse(await readFile(join(base, "data/search.json")));
  const vectors = await readFile(join(base, "data/embeddings.bin"));
  for (let i = 0; i < articles.length; i++) {
    if (!allowed.has(articles[i].handle)) continue;
    const bytes = vectors.subarray(i * SEARCH_MODEL.dimensions * 4, (i + 1) * SEARCH_MODEL.dimensions * 4);
    if (!validVector(bytes, SEARCH_MODEL.dimensions)) throw new Error("Invalid seed search vector");
    const path = join(cache, "vectors", vectorKey(articles[i].text, SEARCH_MODEL) + ".bin");
    try { await readFile(path); } catch { await writeFile(path, bytes); seeded++; }
  }
} catch (error) { if (error.code !== "ENOENT") throw error; }
const ortCommit = (await readFile("node_modules/onnxruntime-web/__commit.txt", "utf8")).trim();
await mkdir(join(cache, "licenses"), {recursive: true});
for (const [name, url] of [
  ["onnxruntime.txt", `https://raw.githubusercontent.com/microsoft/onnxruntime/${ortCommit}/LICENSE`],
  ["bge-model-card.md", "https://huggingface.co/BAAI/bge-small-en-v1.5/raw/main/README.md"],
  ["bge-license.txt", "https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE"],
]) {
  try {
    const bytes = await readFile(join("dist/licenses", name));
    await writeFile(join(cache, "licenses", createHash("sha256").update(url).digest("hex")), bytes);
  } catch (error) { if (error.code !== "ENOENT") throw error; }
}
console.log(JSON.stringify({seeded_search_vectors: seeded, allowed_documents: allowed.size}));
