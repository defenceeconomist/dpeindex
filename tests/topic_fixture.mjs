export const papers = [
  {handle: "paper-a", title: "Civil war and peace", authors: ["Author A"], year: "2025", volume: "37", issue: "1", pages: "1–5", doi: "", topic: 0, original_topic: 0, x: 1, y: 2, reassigned: false},
  {handle: "paper-b", title: "Arms <trade>", authors: ["Author B"], year: "2024", volume: "36", issue: "1", pages: "6–9", doi: "", topic: 1, original_topic: -1, x: 3, y: 4, reassigned: true},
  {handle: "paper-c", title: "An ambiguous abstract", authors: ["Author C"], year: "2023", volume: "35", issue: "1", pages: "10–15", doi: "", topic: -1, original_topic: -1, x: 2, y: 3, reassigned: false},
];
const metric = {topic_count: 2, outliers: 1, coherence: .2, silhouette: .1, diversity: .8, coverage: 2 / 3};
export const fixture = {
  status: "ready", stale: false,
  metadata: {run_id: "test-run", built_at: "2026-10-15T12:00:00Z", model_revision: "test-revision", corpus_fingerprint: "test-corpus", abstract_count: 3, missing_abstract_count: 1, seeds: [42, 7, 21], params: {n_neighbors: 10, n_components: 5, min_cluster_size: 20, min_samples: 1},
    token_coverage: {checked_abstracts: 3, max_tokens: 414, sequence_limit: 32768}, outliers: {before: 2, after: 1, agreement: 1, accepted_validation: 40}},
  documents: papers,
  topics: [{id: 0, count: 1, label: "war · peace", keywords: [{term: "war", weight: .2}], representatives: ["paper-a"]},
    {id: 1, count: 1, label: "arms · trade", keywords: [{term: "trade", weight: .2}], representatives: ["paper-b"]}],
  hierarchy: [{id: "0", label: "war · peace", topics: [0], children: [], distance: 0},
    {id: "1", label: "arms · trade", topics: [1], children: [], distance: 0},
    {id: "2", label: "All related themes", topics: [0, 1], children: ["0", "1"], distance: 1}],
  report: {trials: [], baseline: metric, selected_before_reassignment: metric, selected_after_reassignment: metric},
};
fixture.timeline = {status: "ready", run_id: "test-run", built_at: "2026-10-15", first_year: 2023, last_year: 2025, undated_abstract_count: 0,
  method: {assignments: "Fixed published assignments.", denominator: "All abstracts including unassigned.", keywords: "Smoothed period keywords."}, groups: {}};
for (const [name, width] of Object.entries({year: 1, five_years: 5, decade: 10})) {
  const periods = [], cells = [];
  for (let start = Math.floor(2023 / width) * width; start <= 2025; start += width) {
    const end = start + width - 1;
    const rows = papers.filter((paper) => Number(paper.year) >= start && Number(paper.year) <= end);
    const missing = start <= 2024 && end >= 2024 ? 1 : 0;
    periods.push({start, end, label: width === 1 ? String(start) : `${start}–${end}`, abstract_count: rows.length, missing_abstract_count: missing,
      record_count: rows.length + missing, observed_years: [...new Set(rows.map((paper) => Number(paper.year)))], partial: start < 2023 || end > 2025});
    for (const id of [0, 1, -1]) {
      const count = rows.filter((paper) => paper.topic === id).length;
      cells.push({start, topic: id, count, share: rows.length ? count / rows.length * 100 : null, words: count ? [`period-${start}`, `theme-${id}`] : []});
    }
  }
  fixture.timeline.groups[name] = {width, periods, cells};
}
