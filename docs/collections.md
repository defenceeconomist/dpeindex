# Verified article collections

The main page offers collection type and individual special-issue/country selectors.
Selections intersect with theme, year, and search filters. Country surveys are the
journal's named editorial series, not every country-focused article. Multi-country
surveys appear under each named country and are counted once in the category total.

## Evidence and coverage

`data/collections.json` is the reviewed registry. Each collection has a stable ID,
type (`special_issue` or `country_survey`), title, explicit article handles, source
URLs and a review note. Special issues also have volume, issue and year; country
entries have a country label. The initial registry covers 28 special issues and
19 unique Country Survey articles (21 country labels), within the current corpus.

Review starts with the [publisher's special-issue archive](https://www.tandfonline.com/journals/gdpe20/special-issues),
publisher-supplied RePEc titles, and editorial introductions linked in the registry.
The newest terrorism issue is confirmed by its introduction's abstract, which
identifies six contributions plus the introduction. The archive does not yet list it.

Issue labels alone can be misleading. For example, defence inflation (27:2)
contains an obituary and other material outside the five papers described in its
introduction. The NORAD section (35:5) ends at page 600, and the Sweden section
(33:4) ends at page 474. The registry lists constituent papers explicitly, excludes
unrelated articles and book reviews, and never classifies by a title keyword at runtime.

`data/collection-candidates.json` holds unresolved candidates, not published labels.
In particular, the publisher labels 23:4 with the same theme as 23:6 despite
different catalogue contents. That issue is excluded pending stronger evidence.
Coverage is described publicly as verified and historically incomplete.

## Updating after a data refresh

1. Inspect new and corrected article titles, abstracts and issue contents after the
   MCP update. Search for named surveys and introductions to generate candidates,
   then verify against the publisher and editorial evidence.
2. Add explicit article handles and sources to the registry only after reviewing
   membership. Keep uncertain candidates separate. Keep IDs stable; add repeated
   surveys of a country to its existing entry. Record multi-country surveys under
   each relevant country.
3. Reconcile removed or corrected handles rather than silently dropping them.
   The build rejects unknown handles, issue/year mismatches, missing sources,
   duplicate IDs and repeated handles within a collection. Overlap between
   collections is allowed.
4. Run `npm test`, `npm run static:build`, and the collection browser tests:
   `npx playwright test --grep 'verified collections|collection loading'`.
   Review counts and exclusions before publishing with `npm run static:publish -- dist`.

## Static interface

The static builder adds `data/collections.json` to the staged website release,
without modifying the immutable Python export or model artifacts. Its content
contributes to the release ID and manifest hashes. Registry changes reuse search
vectors; no topic retraining or citation refresh is required.

`dataClient.getCollections()` loads the metadata. Membership is shared by issue
browsing, semantic ranking (before the top-40 cutoff), and keyword fallback.
The URL uses `collection_type=special_issue|country_survey` and an optional stable
`collection` ID. Unknown IDs fall back to the valid category with a notice;
unknown categories fall back to all articles. Reset clears both parameters.

Collection metadata is independent of topic data. Failed requests leave ordinary
browsing available with a visible notice and a retry button; retry restores the
requested selection unless the user has reset filters.
