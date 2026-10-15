import {cp, mkdir, readFile, readdir, rename, rm, writeFile, mkdtemp} from "node:fs/promises";
import {createHash} from "node:crypto";
import {execFileSync} from "node:child_process";
import {resolve, join, relative} from "node:path";
import {build} from "esbuild";
import {parseArgs} from "node:util";
import {cachedVectors} from "./search-vector-cache.mjs";
import {SEARCH_MODEL, encodingOptions} from "../site/search-model.js";
import {validateStatic, hashFiles} from "./validate-static.mjs";

const root = resolve(import.meta.dirname, "..");
process.chdir(root);
const {values: options} = parseArgs({options: {
  dataset: {type: "string"}, output: {type: "string"}, cache: {type: "string"},
  "skip-export": {type: "boolean", default: false}, metrics: {type: "string"},
}});
const started = performance.now();
const cache = resolve(options.cache || ".static-cache");
await mkdir(cache, {recursive: true});
const current = !options.dataset && !options["skip-export"] ? JSON.parse(await readFile("data/local-artifacts/current.json")) : null;
const dataset = resolve(options.dataset || (current ? join(current.release, "dataset") : ".static-cache/export"));
const output = resolve(options.output || "dist");
if (output === root || dataset === output || dataset.startsWith(output + "/") || cache === output || cache.startsWith(output + "/")) throw new Error("Unsafe output directory");
if (output === join(root, ".git") || output.startsWith(join(root, ".git") + "/") ||
    (output.startsWith(root + "/") && execFileSync("git", ["ls-files", "-z", "--", relative(root, output)]).length)) {
  throw new Error("Unsafe output directory: would replace tracked source files");
}

const manifest = JSON.parse(await readFile(join(dataset, "data/manifest.json")));
const inputFiles = await hashFiles(dataset);
for (const [path, expected] of Object.entries(manifest.files)) {
  const actual = inputFiles[path];
  if (!actual || actual.sha256 !== expected.sha256 || actual.bytes !== expected.bytes) throw new Error(`Corrupt input dataset: ${path}`);
}
const modelRoot = join(cache, "models", SEARCH_MODEL.revision);
const modelDir = join(modelRoot, SEARCH_MODEL.id);
const modelFiles = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "onnx/model_quantized.onnx"];
for (const file of modelFiles) {
  const target = join(modelDir, file);
  await mkdir(resolve(target, ".."), {recursive: true});
  try { await readFile(target); } catch {
    const pending = target + ".part";
    execFileSync("curl", ["-fLsS", "--retry", "3", `https://huggingface.co/${SEARCH_MODEL.id}/resolve/${SEARCH_MODEL.revision}/${file}`, "-o", pending], {stdio: "inherit"});
    await rename(pending, target);
  }
}
const search = JSON.parse(await readFile(join(dataset, "data/search.json")));
const key = createHash("sha256").update(JSON.stringify({search, model: SEARCH_MODEL})).digest("hex");
let extractor;
const {binary, metrics: vectorMetrics} = await cachedVectors(search, SEARCH_MODEL, join(cache, "vectors"), async (text) => {
  if (!extractor) {
    const {env, pipeline} = await import("@huggingface/transformers");
    env.allowRemoteModels = false; env.localModelPath = modelRoot + "/";
    extractor = await pipeline("feature-extraction", SEARCH_MODEL.id, {dtype: SEARCH_MODEL.dtype, revision: SEARCH_MODEL.revision, device: "cpu"});
  }
  const result = await extractor(text, encodingOptions);
  return result.data;
});
if (extractor) await extractor.dispose();
console.log(`Search vectors: ${vectorMetrics.encoded} encoded, ${vectorMetrics.cache_hits} cached`);
manifest.search = {...SEARCH_MODEL, format: "float32-le", handles: search.map((a) => a.handle),
  model_files: await hashFiles(modelDir)};
// Release identity includes the encoder and index, preventing mixed caches.
const release = createHash("sha256").update(manifest.release_id + key).digest("hex").slice(0, 20);
manifest.release_id = release;
await mkdir(resolve(output, ".."), {recursive: true});
const staged = await mkdtemp(output + ".building-");
try {
  const releaseDir = join(staged, "releases", release);
  await cp(dataset, releaseDir, {recursive: true});
  await cp(join(dataset, "api"), join(staged, "api"), {recursive: true});
  await rm(join(releaseDir, "api"), {recursive: true});
  await writeFile(join(releaseDir, "data/embeddings.bin"), binary);
  const referenceQuery = "economic effects of civil war";
  const {env, pipeline} = await import("@huggingface/transformers");
  env.allowRemoteModels = false; env.localModelPath = modelRoot + "/";
  const encoder = await pipeline("feature-extraction", SEARCH_MODEL.id, {dtype: SEARCH_MODEL.dtype, revision: SEARCH_MODEL.revision, device: "cpu"});
  const reference = await encoder(SEARCH_MODEL.query_prefix + referenceQuery, encodingOptions);
  await writeFile(join(releaseDir, "data/search-reference.json"), JSON.stringify({query: referenceQuery, vector: Array.from(reference.data)}));
  await encoder.dispose();
  manifest.files = await hashFiles(releaseDir);
  delete manifest.files["data/manifest.json"];
  await writeFile(join(releaseDir, "data/manifest.json"), JSON.stringify(manifest));
  for (const file of await readdir("site")) {
    if (["topics.html", "citations.html", "search-worker.js"].includes(file)) continue;
    await cp(join("site", file), join(staged, file));
  }
  for (const name of ["topics", "citations"]) {
    await mkdir(join(staged, name));
    await cp(`site/${name}.html`, join(staged, name, "index.html"));
  }
  // The manuscript is self-contained; publish the reviewed render without rebuilding its analysis.
  await mkdir(join(staged, "research-note"));
  const manuscript = await readFile("manuscript/manuscript.html", "utf8");
  await writeFile(join(staged, "research-note/index.html"), manuscript.replaceAll('href="../manuscript.pdf"', 'href="manuscript.pdf"'));
  await cp("manuscript/manuscript.pdf", join(staged, "research-note/manuscript.pdf"));
  for (const page of ["index.html", "topics/index.html", "citations/index.html"]) {
    const path = join(staged, page);
    let html = await readFile(path, "utf8");
    html = html.replace(/\s*<a class="portal-link"[^>]*>.*?<\/a>/g, "");
    await writeFile(path, html);
  }
  await writeFile(join(staged, "site-config.js"), `export default ${JSON.stringify({datasetBase: `releases/${release}/`, modelBase: `models/${SEARCH_MODEL.revision}/`})};\n`);
  await cp(modelRoot, join(staged, "models", SEARCH_MODEL.revision), {recursive: true});
  await mkdir(join(staged, "vendor/onnx"), {recursive: true});
  for (const file of await readdir("node_modules/onnxruntime-web/dist")) {
    if (/^ort-wasm.*\.(wasm|mjs)$/.test(file) && !file.includes(".jsep.")) await cp(join("node_modules/onnxruntime-web/dist", file), join(staged, "vendor/onnx", file));
  }
  await cp("node_modules/plotly.js-dist-min/plotly.min.js", join(staged, "vendor/plotly.min.js"));
  await build({entryPoints: ["site/search-worker.js"], outfile: join(staged, "search-worker.js"), bundle: true,
    format: "esm", platform: "browser", alias: {"onnxruntime-web": "onnxruntime-web/wasm"},
    minify: true, legalComments: "linked", target: "es2022"});
  await mkdir(join(staged, "licenses"));
  for (const [pkg, name] of [["@huggingface/transformers", "transformers"], ["plotly.js-dist-min", "plotly"]]) {
    await cp(`node_modules/${pkg}/LICENSE`, join(staged, "licenses", `${name}.txt`));
  }
  const ortCommit = (await readFile("node_modules/onnxruntime-web/__commit.txt", "utf8")).trim();
  const licenseRoot = join(cache, "licenses"); await mkdir(licenseRoot, {recursive: true});
  for (const [name, url] of [
    ["onnxruntime.txt", `https://raw.githubusercontent.com/microsoft/onnxruntime/${ortCommit}/LICENSE`],
    ["bge-model-card.md", "https://huggingface.co/BAAI/bge-small-en-v1.5/raw/main/README.md"],
    ["bge-license.txt", "https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE"],
  ]) {
    const cached = join(licenseRoot, createHash("sha256").update(url).digest("hex"));
    try { await readFile(cached); } catch {
      execFileSync("curl", ["-fLsS", "--retry", "3", url, "-o", cached + ".part"], {stdio: "inherit"});
      await rename(cached + ".part", cached);
    }
    await cp(cached, join(staged, "licenses", name));
  }
  await writeFile(join(staged, ".nojekyll"), "");
  await writeFile(join(staged, "release.json"), JSON.stringify({release_id: release, manifest: `releases/${release}/data/manifest.json`, model: SEARCH_MODEL, files: await hashFiles(staged)}));
  await validateStatic(staged);
  const backup = await mkdtemp(output + ".previous-");
  await rm(backup, {recursive: true});
  try { await rename(output, backup); } catch (e) { if (e.code !== "ENOENT") throw e; }
  try { await rename(staged, output); } catch (error) { try { await rename(backup, output); } catch {} throw error; }
  await rm(backup, {recursive: true, force: true});
  if (options.metrics) await writeFile(resolve(options.metrics), JSON.stringify({...vectorMetrics,
    article_count: search.length, elapsed_seconds: (performance.now() - started) / 1000, release_id: release,
    bytes: Object.values(await hashFiles(output)).reduce((n, f) => n + f.bytes, 0)}));
  console.log(`Built ${search.length} articles; release ${release} in ${output}`);
} finally { await rm(staged, {recursive: true, force: true}); }
