import {readFileSync} from "node:fs";

export function comparisonFixture() {
  const reference = JSON.parse(readFileSync(new URL("../data/retrospectives.json", import.meta.url), "utf8"));
  const counts = [[3, 11, 0, 17, 0, 4, 3, 0, 0, 0], [51, 82, 37, 48, 17, 20, 22, 13, 1, 13], [78, 100, 57, 32, 30, 11, 12, 24, 5, 9]];
  return {status: "ready", links_enabled: true, audited_model: reference.audited_model, method: reference.method,
    periods: reference.periods.map((period, i) => {
      const abstractCount = [45, 330, 381][i], missingCount = [3, 31, 26][i], outliers = [7, 26, 23][i];
      return {...period,
        coverage: {record_count: abstractCount + missingCount, abstract_count: abstractCount, missing_abstract_count: missingCount, unassigned_count: outliers, unassigned_share: 100 * outliers / abstractCount},
        categories: period.categories.map((row) => ({...row, share: 100 * row.count / period.classified_total})),
        model_topics: reference.audited_topics.map((row, id) => ({...row, count: counts[i][id], share: 100 * counts[i][id] / abstractCount,
          examples: counts[i][id] && id < 2 ? [{handle: id ? "paper-b" : "paper-a", title: id ? "Spending and growth" : "Civil war and peace", year: "2010", authors: ["A Researcher"]}] : []})),
      };
    })};
}
