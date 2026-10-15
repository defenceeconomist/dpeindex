import {readFileSync} from "node:fs";

export function comparisonFixture() {
  const reference = JSON.parse(readFileSync(new URL("../data/retrospectives.json", import.meta.url), "utf8"));
  const counts = [[8, 0, 1, 0, 3, 1, 5, 3, 8, 0, 1, 0, 4, 0, 0], [60, 37, 23, 20, 27, 18, 17, 17, 11, 0, 17, 8, 18, 12, 3], [68, 56, 48, 33, 15, 22, 12, 16, 5, 5, 12, 17, 12, 8, 12]];
  return {status: "ready", links_enabled: true, audited_model: reference.audited_model, method: reference.method,
    periods: reference.periods.map((period, i) => {
      const abstractCount = [45, 330, 381][i], missingCount = [3, 31, 26][i], outliers = [11, 42, 40][i];
      return {...period,
        coverage: {record_count: abstractCount + missingCount, abstract_count: abstractCount, missing_abstract_count: missingCount, unassigned_count: outliers, unassigned_share: 100 * outliers / abstractCount},
        categories: period.categories.map((row) => ({...row, share: 100 * row.count / period.classified_total})),
        model_topics: reference.audited_topics.map((row, id) => ({...row, count: counts[i][id], share: 100 * counts[i][id] / abstractCount,
          examples: counts[i][id] && id < 2 ? [{handle: id ? "paper-b" : "paper-a", title: row.name, year: "2010", authors: ["A Researcher"]}] : []})),
      };
    })};
}
