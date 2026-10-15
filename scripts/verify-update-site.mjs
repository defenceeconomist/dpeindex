import {execFileSync} from "node:child_process";
import {createServer} from "node:net";
import {mkdir, writeFile} from "node:fs/promises";
import {join, resolve} from "node:path";

const directory = resolve(process.argv[2]);
const probe = createServer();
await new Promise((ready) => probe.listen(0, "127.0.0.1", ready));
const port = probe.address().port;
await new Promise((ready) => probe.close(ready));
const screenshots = join(directory, "screenshots");
await mkdir(screenshots, {recursive: true});
const env = {...process.env, DPE_PREVIEW_PORT: String(port), DPE_PREVIEW_DIR: join(directory, "site"),
  DPE_SCREENSHOT_DIR: screenshots};
delete env.DPE_PUBLIC_URL;
let body, failed = false;
try {
  body = execFileSync(process.execPath, ["node_modules/@playwright/test/cli.js", "test", "tests/browser/static.spec.mjs", "--reporter=json"],
    {env, maxBuffer: 16 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"]});
} catch (error) { body = error.stdout; failed = true; if (error.stderr) process.stderr.write(error.stderr); }
const report = JSON.parse(body.toString());
await writeFile(join(directory, "browser-results.json"), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report.stats));
if (failed || report.stats.unexpected) throw new Error(`Browser checks failed; inspect ${directory}/browser-results.json`);
