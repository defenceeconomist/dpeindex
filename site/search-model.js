export const SEARCH_MODEL = Object.freeze({
  id: "Xenova/bge-small-en-v1.5",
  revision: "ea104dacec62c0de699686887e3f920caeb4f3e3",
  dtype: "q8", dimensions: 384, pooling: "cls", normalize: true, max_length: 512,
  query_prefix: "Represent this sentence for searching relevant passages: ",
});

export const encodingOptions = {pooling: SEARCH_MODEL.pooling, normalize: true,
  truncation: true, max_length: SEARCH_MODEL.max_length};
