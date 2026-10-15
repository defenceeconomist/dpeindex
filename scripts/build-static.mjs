import {cp, mkdir, readFile, readdir, rename, rm, writeFile, mkdtemp} from "node:fs/promises";
import {createHash} from "node:crypto";
import {execFileSync} from "node:child_process";
import {resolve, join} from "node:path";
import {build} from "esbuild";
import {SEARCH_MODEL, encodingOptions} from "../site/search-model.js";
import {validateStatic, hashFiles} from "./validate-static.mjs";

const root = resolve(import.meta.dirname, "..");
process.chdir(root);
const cache = resolve(".static-cache");
await mkdir(cache, {recursive: true});
const dataset = resolve(".static-cache/export");
if (!process.argv.includes("--skip-export")) execFileSync(process.env.PYTHON || "python3", ["-m", "app.export_static", "--output", dataset], {stdio: "inherit"});
const manifest = JSON.parse(await readFile(join(dataset, "data/manifest.json")));
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
const vectorCache = join(cache, `embeddings-${key}.bin`);
let binary;
try { binary = await readFile(vectorCache); } catch {
  const {env, pipeline} = await import("@huggingface/transformers");
  env.allowRemoteModels = false; env.localModelPath = modelRoot + "/";
  const extractor = await pipeline("feature-extraction", SEARCH_MODEL.id, {dtype: SEARCH_MODEL.dtype, revision: SEARCH_MODEL.revision, device: "cpu"});
  const vectors = new Float32Array(search.length * SEARCH_MODEL.dimensions);
  // One document at a time avoids padding-dependent dynamic quantization and
  // bounds memory; browser queries use the same single-input encoding.
  for (let row = 0; row < search.length; row++) {
    const result = await extractor(search[row].text, encodingOptions);
    if (result.data.length !== SEARCH_MODEL.dimensions) throw new Error("Unexpected embedding dimensions");
    vectors.set(result.data, row * SEARCH_MODEL.dimensions);
    if (row % 100 === 0) console.log(`Embedded ${row}/${search.length} articles`);
  }
  await extractor.dispose();
  binary = Buffer.alloc(vectors.length * 4);
  vectors.forEach((value, index) => binary.writeFloatLE(value, index * 4));
  await writeFile(vectorCache, binary);
}
manifest.search = {...SEARCH_MODEL, format: "float32-le", handles: search.map((a) => a.handle),
  model_files: await hashFiles(modelDir)};
// Release identity includes the encoder and index, preventing mixed caches.
const release = createHash("sha256").update(manifest.release_id + key).digest("hex").slice(0, 20);
manifest.release_id = release;
const staged = await mkdtemp(join(root, ".static-cache/build-"));
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
  for (const page of ["index.html", "topics/index.html", "citations/index.html"]) {
    const path = join(staged, page);
    let html = await readFile(path, "utf8");
    html = html.replace(/\s*<a class="portal-link"[^>]*>.*?<\/a>/g, "");
    await writeFile(path, html);
  }
  await writeFile(join(staged, "site-config.js"), `export default ${JSON.stringify({mode: "static", datasetBase: `releases/${release}/`, modelBase: `models/${SEARCH_MODEL.revision}/`})};\n`);
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
  execFileSync("curl", ["-fLsS", "--retry", "3", `https://raw.githubusercontent.com/microsoft/onnxruntime/${ortCommit}/LICENSE`, "-o", join(staged, "licenses/onnxruntime.txt")]);
  execFileSync("curl", ["-fLsS", "--retry", "3", "https://huggingface.co/BAAI/bge-small-en-v1.5/raw/main/README.md", "-o", join(staged, "licenses/bge-model-card.md")]);
  execFileSync("curl", ["-fLsS", "--retry", "3", "https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE", "-o", join(staged, "licenses/bge-license.txt")]);
  await writeFile(join(staged, ".nojekyll"), "");
  await writeFile(join(staged, "release.json"), JSON.stringify({release_id: release, manifest: `releases/${release}/data/manifest.json`, model: SEARCH_MODEL, files: await hashFiles(staged)}));
  await validateStatic(staged);
  const output = resolve("dist"), backup = resolve(".static-cache/previous-build");
  await rm(backup, {recursive: true, force: true});
  try { await rename(output, backup); } catch (e) { if (e.code !== "ENOENT") throw e; }
  try { await rename(staged, output); } catch (error) { try { await rename(backup, output); } catch {} throw error; }
  await rm(backup, {recursive: true, force: true});
  console.log(`Built ${search.length} articles; release ${release} in dist/`);
} finally { await rm(staged, {recursive: true, force: true}); }
