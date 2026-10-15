import {env, pipeline} from "@huggingface/transformers";
import {SEARCH_MODEL, encodingOptions} from "./search-model.js";

let extractorPromise;
let queue = Promise.resolve();
const active = new Set();
self.onmessage = ({data}) => {
  active.add(data.id);
  if (!extractorPromise) {
    env.allowRemoteModels = false;
    env.allowLocalModels = true;
    env.localModelPath = data.modelBase;
    env.backends.onnx.wasm.numThreads = 1;
    env.backends.onnx.wasm.wasmPaths = data.runtimeBase;
    extractorPromise = pipeline("feature-extraction", SEARCH_MODEL.id, {
      dtype: SEARCH_MODEL.dtype, revision: SEARCH_MODEL.revision, device: "wasm",
      progress_callback: (event) => {
        const message = event.status === "progress" ? `Loading search model… ${Math.round(event.progress)}%` : "Preparing semantic search…";
        for (const id of active) self.postMessage({id, type: "progress", message});
      },
    });
  }
  queue = queue.then(async () => {
    try {
      const extractor = await extractorPromise;
      const result = await extractor(SEARCH_MODEL.query_prefix + data.query, encodingOptions);
      const vector = new Float32Array(result.data);
      self.postMessage({id: data.id, vector}, [vector.buffer]);
    } catch (error) {
      self.postMessage({id: data.id, error: error.message});
    } finally { active.delete(data.id); }
  });
};
