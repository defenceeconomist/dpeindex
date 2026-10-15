import {cp, mkdtemp, readdir, rm, readFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {execFileSync, spawnSync} from "node:child_process";
import {validateStatic, hashFiles} from "./validate-static.mjs";

const root = resolve(import.meta.dirname, ".."), input = resolve(process.argv[2] || join(root, "dist"));
process.chdir(root);
await validateStatic(input);
// Use the authenticated GitHub CLI without changing the user's global Git
// configuration or depending on an unlocked macOS login keychain.
const credentialArgs = spawnSync("gh", ["auth", "status"], {stdio: "ignore"}).status === 0 ?
  ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"] : [];
const git = (...args) => execFileSync("git", [...credentialArgs, ...args], {cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"]}).trim();
const remote = git("ls-remote", "--heads", "origin", "gh-pages");
const parent = await mkdtemp(join(tmpdir(), "dpe-pages-")), worktree = join(parent, "site");
let orphan;
try {
  if (remote) {
    git("fetch", "origin", "gh-pages");
    git("worktree", "add", "--detach", worktree, "FETCH_HEAD");
  } else {
    git("worktree", "add", "--detach", worktree, "HEAD");
    orphan = `static-publish-${Date.now()}`;
    git("-C", worktree, "switch", "--orphan", orphan);
  }
  const incoming = await hashFiles(input);
  let archived = {};
  try { archived = await hashFiles(join(worktree, "api")); } catch (e) { if (e.code !== "ENOENT") throw e; }
  for (const [path, hash] of Object.entries(archived)) {
    const next = incoming[`api/${path}`];
    if (next && next.sha256 !== hash.sha256) throw new Error(`Immutable download changed: ${path}`);
  }
  for (const file of await readdir(worktree)) if (![".git", "api"].includes(file)) await rm(join(worktree, file), {recursive: true, force: true});
  await cp(input, worktree, {recursive: true});
  // The .git control file belongs to the worktree, never to the uploaded site.
  await validateStatic(worktree);
  git("-C", worktree, "add", "--all");
  const changed = spawnSync("git", ["-C", worktree, "diff", "--cached", "--quiet"]).status !== 0;
  if (changed) {
    const release = JSON.parse(await readFile(join(input, "release.json")));
    git("-C", worktree, "commit", "-m", `Publish static site ${release.release_id}`);
    git("-C", worktree, "push", "origin", "HEAD:refs/heads/gh-pages");
  }
  console.log("Published gh-pages. Website: https://defenceeconomist.github.io/dpeindex/");
} finally {
  try { git("worktree", "remove", "--force", worktree); } catch {}
  if (orphan) { try { git("branch", "-D", orphan); } catch {} }
  await rm(parent, {recursive: true, force: true});
}
