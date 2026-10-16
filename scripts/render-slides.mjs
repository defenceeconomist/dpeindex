// Quarto creates the Reveal.js deck; Chromium prints its Quarto PDF layout.
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromium} from '@playwright/test';
process.chdir(resolve(import.meta.dirname, '..'));
execFileSync('quarto', ['render', 'slides/tool-guide.qmd', '--to', 'revealjs'], {stdio: 'inherit'});
const browser = await chromium.launch();
try {
  const page = await browser.newPage({viewport: {width: 1280, height: 720}});
  await page.goto(pathToFileURL(resolve('slides/tool-guide.html')).href + '?print-pdf', {waitUntil: 'networkidle'});
  await page.waitForFunction(() => window.Reveal?.isReady() && document.querySelector('.pdf-page'));
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => Promise.all([...document.images].map(i => i.decode())));
  await page.pdf({path: 'slides/tool-guide.pdf', printBackground: true, preferCSSPageSize: true});
} finally {
  await browser.close();
}
