/**
 * Bundles the wallet island to `pathia/static/wallet.js` + `wallet.css`.
 *
 * The output is committed. That is a deliberate exception to "no compiled
 * artifacts in the repo" and it is bounded by two things:
 *
 *   - The deploy has no Node step. `api/index.py` is a Python function on
 *     Vercel; adding a JS build to that pipeline to produce one static file
 *     would be a second toolchain in the deploy path for no gain.
 *   - `--check` recomputes the hash of every source file and compares it to
 *     the manifest beside the bundle, so a stale artifact fails the gate suite
 *     instead of shipping silently. Run by tests/test_wallet_bundle.py.
 */

import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..', '..');
const OUT_DIR = join(REPO, 'pathia', 'static');
const MANIFEST = join(HERE, 'bundle.manifest.json');

/** Hash of every input that can change the bundle: sources plus the lockfile. */
function sourceHash() {
  const hash = createHash('sha256');
  const files = readdirSync(join(HERE, 'src')).sort();
  for (const name of files) {
    hash.update(name);
    hash.update(readFileSync(join(HERE, 'src', name)));
  }
  for (const name of ['package.json', 'build.mjs']) {
    hash.update(readFileSync(join(HERE, name)));
  }
  return hash.digest('hex');
}

async function run() {
  const checkOnly = process.argv.includes('--check');
  const hash = sourceHash();

  if (checkOnly) {
    if (!existsSync(MANIFEST)) {
      console.error('no bundle.manifest.json — run `npm run build` in services/wallet_ui');
      process.exit(1);
    }
    const recorded = JSON.parse(readFileSync(MANIFEST, 'utf8'));
    if (recorded.sourceHash !== hash) {
      console.error(
        'pathia/static/wallet.js is stale: the sources in services/wallet_ui/src '
        + 'changed since it was built.\nRun `npm run build` in services/wallet_ui '
        + 'and commit the result.',
      );
      process.exit(1);
    }
    for (const artifact of recorded.artifacts) {
      if (!existsSync(join(OUT_DIR, artifact))) {
        console.error(`missing built artifact: pathia/static/${artifact}`);
        process.exit(1);
      }
    }
    console.log('wallet bundle is current');
    return;
  }

  const result = await build({
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
    outfile: join(OUT_DIR, 'wallet.js'),
    define: { 'process.env.NODE_ENV': '"production"' },
    loader: { '.svg': 'dataurl' },
    legalComments: 'none',
    metafile: true,
  });

  const bytes = Object.values(result.metafile.outputs).reduce((n, o) => n + o.bytes, 0);
  writeFileSync(
    MANIFEST,
    JSON.stringify(
      {
        sourceHash: hash,
        artifacts: ['wallet.js', 'wallet.css'],
        builtBytes: bytes,
        note: 'Regenerate with `npm run build` in services/wallet_ui, then commit.',
      },
      null,
      2,
    ) + '\n',
  );
  console.log(`built pathia/static/wallet.js (${(bytes / 1024).toFixed(0)} KB total)`);
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
