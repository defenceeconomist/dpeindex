export function citationFixture() {
  const nodes = [
    {handle: "RePEc:test:alpha", title: "Growth <script>bad</script>", authors: ["A. Researcher"], year: "2000", volume: "11", issue: "1", doi: "10.1234/alpha", citation_doi: "10.1234/alpha", degree: 12, in_degree: 8, out_degree: 4, community: 0, x: 0, y: 0, identity_verified: true},
    {handle: "RePEc:test:beta", title: "Conflict and security", authors: ["B. Author"], year: "2005", volume: "16", issue: "2", doi: "10.1234/beta", citation_doi: "10.1234/beta", degree: 3, in_degree: 1, out_degree: 2, community: 1, x: .5, y: .5, identity_verified: true},
    {handle: "RePEc:test:gamma", title: "Industry", authors: ["C. Writer"], year: "2006", volume: "17", issue: "1", doi: "10.1234/gamma", citation_doi: "10.1234/gamma", degree: 0, in_degree: 0, out_degree: 0, community: -1, x: 1.3, y: 0, identity_verified: true}];
  const communities = [{id: 0, label: "Growth", count: 1, provisional: true, interpretation: "Growth studies.", leaders: [nodes[0].handle]}, {id: 1, label: "Conflict", count: 1, provisional: true, interpretation: "Conflict studies.", leaders: [nodes[1].handle]}];
  const categories = [{id: "0", label: "Spending"}, {id: "missing", label: "Without an abstract"}];
  return {status: "ready", metadata: {run_id: "20261015T120000Z-abcd1234", eligible_count: 3, article_count: 4, excluded_count: 1, edge_count: 2, baseline_edge_count: 1, isolated_count: 1, year_from: 2000, year_to: 2006, retrieved_to: "2026-10-15T12:00:00Z", algorithm: "Louvain", resolution: 1, modularity: .2}, nodes, communities,
    edges: [{source: nodes[1].handle, target: nodes[0].handle, baseline: true}, {source: nodes[0].handle, target: nodes[1].handle, baseline: false}],
    timeline: {year: [{start: 2000, end: 2000, total: 1, counts: {"0": 1}}], five_year: [{start: 2000, end: 2004, total: 1, counts: {"0": 1}}, {start: 2005, end: 2009, total: 2, partial: true, counts: {"1": 1, "-1": 1}}]},
    comparison: {status: "ready", topic_run_id: "topic-run", categories, rows: communities.map((c) => ({community: c.id, count: 1, cells: [{id: "0", label: "Spending", count: 1, share: 100}, {id: "missing", label: "Without an abstract", count: 0, share: 0}]}))},
    coverage: [{year: 2000, eligible: 3, with_references: 2, missing_reference_deposit: 1, unresolved_identities: 0, no_observed_internal_links: 1}],
    downloads: {"bundle.zip": {}, "nodes.csv": {}, "edges.csv": {}, "network.gexf": {}, "report.html": {}}};
}
