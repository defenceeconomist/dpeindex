import {readFile, readdir, stat} from "node:fs/promises";
import {resolve, join, dirname, relative} from "node:path";
import {parseHTML} from "linkedom";
import {createHash} from "node:crypto";

export async function hashFiles(root) {
  const result = {};
  async function visit(dir) {
    for (const entry of await readdir(dir, {withFileTypes: true})) {
      if (entry.name === ".git") continue;
      const path = join(dir, entry.name);
      if (entry.isSymbolicLink()) throw new Error(`Symbolic links are not permitted: ${path}`);
      if (entry.isDirectory()) await visit(path);
      else { const data = await readFile(path); result[relative(root, path)] = {bytes: data.length, sha256: createHash("sha256").update(data).digest("hex")}; }
    }
  }
  await visit(root); return result;
}

export async function validateStatic(directory) {
  const root = resolve(directory), files = await hashFiles(root);
  const size = Object.values(files).reduce((sum, file) => sum + file.bytes, 0);
  if (size >= 1_000_000_000) throw new Error("Published site exceeds the 1 GB Pages limit; retain archives and move large downloads before publishing.");
  for (const [path, file] of Object.entries(files)) if (file.bytes >= 100 * 1024 * 1024) throw new Error(`File exceeds Git's 100 MiB limit: ${path}`);
  const release = JSON.parse(await readFile(join(root, "release.json")));
  for (const [path, expected] of Object.entries(release.files)) {
    if (!files[path] || files[path].sha256 !== expected.sha256 || files[path].bytes !== expected.bytes) throw new Error(`Invalid website asset: ${path}`);
  }
  const manifest = JSON.parse(await readFile(join(root, release.manifest)));
  for (const [path, expected] of Object.entries(manifest.downloads)) {
    if (!files[path] || files[path].sha256 !== expected.sha256 || files[path].bytes !== expected.bytes) throw new Error(`Invalid published download: ${path}`);
  }
  const base = resolve(root, dirname(release.manifest), "..");
  for (const [path, expected] of Object.entries(manifest.files)) {
    const actual = files[relative(root, join(base, path))];
    if (!actual || actual.sha256 !== expected.sha256 || actual.bytes !== expected.bytes) throw new Error(`Invalid exported file: ${path}`);
  }
  const articles = JSON.parse(await readFile(join(base, "data/search.json")));
  const bytes = (await stat(join(base, "data/embeddings.bin"))).size;
  if (articles.length !== manifest.article_count || bytes !== articles.length * manifest.search.dimensions * 4 || articles.some((a, i) => a.handle !== manifest.search.handles[i])) throw new Error("Search vectors do not align with article identities.");
  const modelBase = join(root, "models", manifest.search.revision, manifest.search.id);
  for (const [path, expected] of Object.entries(manifest.search.model_files)) {
    const actual = files[relative(root, join(modelBase, path))];
    if (!actual || actual.sha256 !== expected.sha256) throw new Error(`Invalid model file: ${path}`);
  }
  for (const path of Object.keys(files).filter((p) => /\.html$/.test(p) && !p.startsWith("api/"))) {
    const html = await readFile(join(root, path), "utf8");
    if (html.includes("Mac mini portal")) throw new Error(`Local portal link in ${path}`);
    // Inspect real elements, not link-like strings inside embedded Reveal.js scripts.
    const {document} = parseHTML(html);
    for (const element of document.querySelectorAll("[href], [src]")) {
      for (const attribute of ["href", "src"]) {
        const target = element.getAttribute(attribute);
        if (!target || target.startsWith("#")) continue;
        if (/^(?:[a-z]+:|\/\/)/i.test(target)) continue;
        const plain = target.split(/[?#]/)[0];
        const targetPath = resolve(root, dirname(path), plain);
        if (target.startsWith("/") || (targetPath !== root && !targetPath.startsWith(root + "/"))) throw new Error(`Non-relative site link in ${path}: ${target}`);
        const name = relative(root, targetPath);
        if (!files[name] && !files[join(name, "index.html")]) throw new Error(`Broken link in ${path}: ${target}`);
      }
    }
  }
  console.log(`Validated ${Object.keys(files).length} files, ${(size / 1e6).toFixed(1)} MB`);
  return {files: Object.keys(files).length, bytes: size, articles: articles.length};
}
if (process.argv[1] && resolve(process.argv[1]) === import.meta.filename) await validateStatic(process.argv[2] || "dist");
