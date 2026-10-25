/**
 * Bundles the wallet island to `pathiel/static/wallet.js` + `wallet.css`.
 *
 * The output is committed. That is a deliberate exception to "no compiled
 * artifacts in the repo" and it is bounded by three things:
 *
 *   - The deploy has no Node step. `api/index.py` is a Python function on
 *     Vercel; adding a JS build to that pipeline to produce one static file
 *     would be a second toolchain in the deploy path for no gain.
 *   - `--check` recomputes the hash of every input (sources, package.json,
 *     package-lock.json, this file) and of both committed artifacts, and
 *     compares them to the manifest beside the bundle, so a stale or
 *     hand-edited artifact fails the gate suite instead of shipping silently.
 *     Run by tests/test_wallet_bundle.py.
 *   - `--verify` rebuilds from the installed node_modules into a temp dir and
 *     fails unless the result is byte-identical to the committed files and
 *     pulls in exactly the packages the manifest lists. CI runs it after
 *     `npm ci --ignore-scripts`. That is the proof that the 3 MB of minified
 *     JS a reviewer cannot read is what the locked dependencies produce and
 *     nothing else (docs/security/SUPPLY_CHAIN_2026-10-25.md, P1-5).
 *
 * The manifest's `bundledPackages` is the list of npm packages whose code
 * ships to the browser. It is the allow-list a dependency bump has to change
 * in a reviewed diff: an update that drags a new package into the bundle
 * shows up there, by name and version.
 */

import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, readdirSync, existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..', '..');
const OUT_DIR = join(REPO, 'pathiel', 'static');
const MANIFEST = join(HERE, 'bundle.manifest.json');
const ARTIFACTS = ['wallet.js', 'wallet.css'];

/** Every file under `dir`, recursively, as repo-relative POSIX paths —
 * sorted, so the walk order never depends on the filesystem's own order. */
function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out.sort();
}

/** Hash of every input that can change the bundle: sources (nested under
 * src/lib and src/components), package.json, the lockfile, and this file.
 * The lockfile was missing from this list until 2026-10-25, so a dependency
 * bump that changed what ships did not mark the bundle stale. */
function sourceHash() {
  const hash = createHash('sha256');
  for (const path of walk(join(HERE, 'src'))) {
    hash.update(relative(HERE, path).split('\\').join('/'));
    hash.update(readFileSync(path));
  }
  for (const name of ['package.json', 'package-lock.json', 'build.mjs']) {
    hash.update(name);
    hash.update(readFileSync(join(HERE, name)));
  }
  return hash.digest('hex');
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

async function bundle(outDir) {
  return build({
    absWorkingDir: HERE,
    entryPoints: [join(HERE, 'src', 'index.tsx')],
    bundle: true,
    minify: true,
    format: 'iife',
    // Without this esbuild emits React.createElement calls into files that
    // never import React, and the bundle dies at mount with
    // "React is not defined". The automatic runtime imports jsx/jsxs itself.
    jsx: 'automatic',
    target: ['es2020'],
    platform: 'browser',
    // RainbowKit ships its stylesheet as a separate import; esbuild emits it
    // beside the JS, which the templates load as its own <link>.
    outfile: join(outDir, 'wallet.js'),
    define: { 'process.env.NODE_ENV': '"production"' },
    loader: { '.svg': 'dataurl' },
    legalComments: 'none',
    metafile: true,
    logLevel: 'error',
  });
}

/** name@version of every node_modules package with code in the output. */
function bundledPackages(metafile) {
  const seen = new Set();
  for (const output of Object.values(metafile.outputs)) {
    for (const [input, info] of Object.entries(output.inputs)) {
      if (!info.bytesInOutput) continue;
      const m = input.match(/^(.*node_modules\/)((?:@[^/]+\/)?[^/]+)\//);
      if (!m) continue;
      const pkg = JSON.parse(readFileSync(join(HERE, m[1], m[2], 'package.json'), 'utf8'));
      seen.add(`${pkg.name}@${pkg.version}`);
    }
  }
  return [...seen].sort();
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

function readManifest() {
  if (!existsSync(MANIFEST)) fail('no bundle.manifest.json: run `npm run build` in services/wallet_ui');
  return JSON.parse(readFileSync(MANIFEST, 'utf8'));
}

function check(recorded) {
  if (recorded.sourceHash !== sourceHash()) {
    fail('pathiel/static/wallet.js is stale: the sources, package.json or '
      + 'package-lock.json in services/wallet_ui changed since it was built.\n'
      + 'Run `npm run build` in services/wallet_ui and commit the result.');
  }
  for (const artifact of recorded.artifacts) {
    const path = join(OUT_DIR, artifact);
    if (!existsSync(path)) fail(`missing built artifact: pathiel/static/${artifact}`);
    const want = (recorded.artifactSha256 || {})[artifact];
    if (!want) fail(`bundle.manifest.json records no sha256 for ${artifact}; run \`npm run build\``);
    if (sha256(path) !== want) {
      fail(`pathiel/static/${artifact} does not match the sha256 its build recorded. `
        + 'Someone edited the built file by hand, or committed it from a different build. '
        + 'Run `npm run build` in services/wallet_ui and commit the result.');
    }
  }
}

async function verify(recorded) {
  const tmp = mkdtempSync(join(tmpdir(), 'wallet-verify-'));
  try {
    const result = await bundle(tmp);
    for (const artifact of ARTIFACTS) {
      const rebuilt = sha256(join(tmp, artifact));
      const committed = sha256(join(OUT_DIR, artifact));
      if (rebuilt !== committed) {
        fail(`rebuild of ${artifact} from node_modules does not match the committed `
          + `pathiel/static/${artifact} (rebuilt ${rebuilt.slice(0, 12)}, committed `
          + `${committed.slice(0, 12)}). Either node_modules is not what package-lock.json `
          + 'pins (run `npm ci --ignore-scripts`) or the committed bundle did not come from '
          + 'these sources.');
      }
    }
    const pkgs = bundledPackages(result.metafile);
    const want = recorded.bundledPackages || [];
    if (JSON.stringify(pkgs) !== JSON.stringify(want)) {
      const added = pkgs.filter((p) => !want.includes(p));
      const removed = want.filter((p) => !pkgs.includes(p));
      fail(`packages shipped in the bundle differ from the manifest: +${added.join(', +')} `
        + `-${removed.join(', -')}`);
    }
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

async function run() {
  if (process.argv.includes('--check')) {
    check(readManifest());
    console.log('wallet bundle is current');
    return;
  }
  if (process.argv.includes('--verify')) {
    const recorded = readManifest();
    check(recorded);
    await verify(recorded);
    console.log('wallet bundle is current and reproduces byte for byte from the lockfile');
    return;
  }

  const result = await bundle(OUT_DIR);
  const bytes = Object.values(result.metafile.outputs).reduce((n, o) => n + o.bytes, 0);
  const artifactSha256 = Object.fromEntries(ARTIFACTS.map((a) => [a, sha256(join(OUT_DIR, a))]));
  writeFileSync(
    MANIFEST,
    JSON.stringify(
      {
        sourceHash: sourceHash(),
        artifacts: ARTIFACTS,
        artifactSha256,
        builtBytes: bytes,
        bundledPackages: bundledPackages(result.metafile),
        note: 'Regenerate with `npm run build` in services/wallet_ui, then commit.',
      },
      null,
      2,
    ) + '\n',
  );
  console.log(`built pathiel/static/wallet.js (${(bytes / 1024).toFixed(0)} KB total)`);
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
