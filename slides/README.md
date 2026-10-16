# Tool guide slides

`tool-guide.qmd` is the editable Quarto source for the introductory slide deck.
It covers search and browsing, collection filters, topics, citation rankings,
research workflows, data provenance and library access to full articles.

Render both formats with:

```sh
npm run slides:render
```

Requires Quarto, the project's npm dependencies, and Playwright Chromium
(`npx playwright install chromium`). Quarto renders a self-contained Reveal.js
HTML deck. The script uses Reveal.js's print layout to export the same slides
as a PDF, preserving their appearance and links.

The screenshots in `assets/` show the real local site with the October 2026
release. Refresh them when the interface changes. The deck describes how to
check current coverage rather than promising real-time updates.

`npm run static:build` copies the reviewed HTML and PDF to `dist/slides/`.
The Articles, Topics and Citations footers link to the HTML deck; its final
slide links to the PDF. Rerender after changing the source before publishing.
