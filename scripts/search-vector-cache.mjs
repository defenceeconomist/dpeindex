import {createHash} from "node:crypto";
import {mkdir, readFile, rename, writeFile} from "node:fs/promises";
import {join} from "node:path";

export function vectorKey(text, model) {
  return createHash("sha256").update(JSON.stringify({text, model})).digest("hex");
}

export function validVector(bytes, dimensions) {
  if (bytes.length !== dimensions * 4) return false;
  let norm = 0;
  for (let i = 0; i < dimensions; i++) {
    const value = bytes.readFloatLE(i * 4);
    if (!Number.isFinite(value)) return false;
    norm += value * value;
  }
  return Math.abs(norm - 1) < .003;
}

export async function cachedVectors(articles, model, directory, encode) {
  await mkdir(directory, {recursive: true});
  const binary = Buffer.alloc(articles.length * model.dimensions * 4);
  const metrics = {encoded: 0, cache_hits: 0, corrupt_entries: 0};
  for (let row = 0; row < articles.length; row++) {
    const path = join(directory, vectorKey(articles[row].text, model) + ".bin");
    let bytes;
    try {
      bytes = await readFile(path);
      if (!validVector(bytes, model.dimensions)) { metrics.corrupt_entries++; bytes = null; }
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    if (bytes) metrics.cache_hits++;
    else {
      const vector = await encode(articles[row].text);
      bytes = Buffer.alloc(model.dimensions * 4);
      if (vector.length !== model.dimensions) throw new Error("Unexpected embedding dimensions");
      vector.forEach((value, i) => bytes.writeFloatLE(value, i * 4));
      if (!validVector(bytes, model.dimensions)) throw new Error("Invalid encoded search vector");
      await writeFile(path + ".part", bytes); await rename(path + ".part", path);
      metrics.encoded++;
    }
    bytes.copy(binary, row * model.dimensions * 4);
  }
  return {binary, metrics};
}
