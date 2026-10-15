import config from "./site-config.js";
import {matchesFilters, rankArticles} from "./search-ranking.js";

export function createDataClient(settings, root = new URL("./", import.meta.url), fetcher = (...args) => fetch(...args)) {
  const cache = new Map();
  let worker, nextId = 0, failed = false, vectorsPromise;
  const jobs = new Map();
  const base = new URL(settings.datasetBase, root);
  const read = (name) => {
    if (!cache.has(name)) cache.set(name, fetcher(new URL(name, base).href).then(async (response) => {
      if (!response.ok) throw new Error("Published website data could not be loaded. Please reload the page.");
      return response.json();
    }).catch((error) => { cache.delete(name); throw error; }));
    return cache.get(name);
  };
  const getThemes = () => read("data/themes.json");
  const getCatalog = () => read("data/catalog.json");
  const requestEmbedding = (query, onProgress, retry) => {
    if (retry) {
      for (const job of jobs.values()) job.reject(new Error("Search was restarted."));
      jobs.clear(); worker?.terminate(); worker = null; failed = false;
    }
    if (failed) return Promise.reject(new Error("Semantic search is unavailable."));
    if (!worker) {
      worker = new Worker(new URL("search-worker.js", root), {type: "module"});
      worker.onmessage = ({data}) => {
        const job = jobs.get(data.id);
        if (!job) return;
        if (data.type === "progress") job.onProgress?.(data.message);
        else { jobs.delete(data.id); data.error ? job.reject(new Error(data.error)) : job.resolve(data.vector); }
      };
      worker.onerror = () => {
        failed = true;
        for (const job of jobs.values()) job.reject(new Error("The search model could not be loaded."));
        jobs.clear(); worker?.terminate(); worker = null;
      };
    }
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      jobs.set(id, {resolve, reject, onProgress});
      worker.postMessage({id, query, modelBase: new URL(settings.modelBase, root).href,
        runtimeBase: new URL("vendor/onnx/", root).href});
    });
  };
  return {
    mode: "static", getCatalog, getThemes, getSource: () => read("data/source.json"),
    getTopics: () => read("data/topics.json"), getCitations: () => read("data/citations.json"),
    async getIssue(params, signal) {
      const [manifest, themes] = await Promise.all([read("data/manifest.json"), getThemes().catch(error => { if (params.has("theme")) throw error; return {status: "not_built"}; })]);
      const path = manifest.issues[`${params.get("volume")}:${params.get("number")}`];
      if (!path) throw new Error("Issue not found.");
      const data = await read(path);
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const fields = ["handle", "title", "authors", "volume", "issue", "year", "pages", "doi"];
      return {...data, articles: data.articles.filter((a) => matchesFilters(a, params, themes)).map((a) => Object.fromEntries(fields.map((key) => [key, a[key]])))};
    },
    async getArticle(handle) {
      const catalog = await getCatalog();
      const summary = catalog.articles.find((a) => a.handle === handle);
      if (!summary) throw new Error("Article not found.");
      const manifest = await read("data/manifest.json");
      const issue = await read(manifest.issues[`${summary.volume}:${summary.issue}`]);
      return issue.articles.find((a) => a.handle === handle);
    },
    async search(params, {onProgress, retry = false} = {}) {
      const query = (params.get("q") || "").trim();
      if (query.length < 2 || query.length > 250) throw new Error("Enter between 2 and 250 characters.");
      const [articles, themes, manifest] = await Promise.all([read("data/search.json"), getThemes(), read("data/manifest.json")]);
      // Validate filters before starting a potentially expensive model download.
      articles.forEach((a) => matchesFilters(a, params, themes));
      try {
        onProgress?.("Loading semantic search for this device…");
        const vector = await requestEmbedding(query, onProgress, retry);
        vectorsPromise ||= fetcher(new URL("data/embeddings.bin", base).href).then(async (response) => {
          if (!response.ok) throw new Error("The search index could not be loaded.");
          return new Float32Array(await response.arrayBuffer());
        }).catch((error) => { vectorsPromise = null; throw error; });
        const vectors = await vectorsPromise;
        if (vectors.length !== articles.length * manifest.search.dimensions || vector.length !== manifest.search.dimensions) throw new Error("The search model and index do not match.");
        return {...rankArticles(articles, query, params, themes, vector, vectors), mode: "semantic"};
      } catch {
        failed = true;
        return {...rankArticles(articles, query, params, themes), mode: "keyword"};
      }
    },
    downloadUrl: (run, name) => new URL(`api/citations/${encodeURIComponent(run)}/download/${encodeURIComponent(name)}`, root).href,
  };
}

export const dataClient = createDataClient(config);
