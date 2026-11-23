import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { StaticFileCache } from '../../node_modules/vinext/dist/server/static-file-cache.js';

test('production static files use URL paths on every OS and keep build metadata private', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panel-static-'));
  try {
    for (const directory of ['_next/static/chunks', 'assets', '.vite']) {
      await mkdir(join(root, directory), { recursive: true });
    }
    await writeFile(join(root, '_next/static/chunks/site-a1b2c3d4.css'), 'body{color:blue}');
    await writeFile(join(root, 'assets/legacy.js'), 'export{}');
    await writeFile(join(root, '.vite/manifest.json'), '{}');
    const cache = await StaticFileCache.create(root);
    const style = cache.lookup('/_next/static/chunks/site-a1b2c3d4.css');
    assert.ok(style, 'Nested CSS must resolve using its browser URL');
    assert.equal(style.original.headers['Content-Type'], 'text/css');
    assert.match(style.original.headers['Cache-Control'], /immutable/);
    assert.ok(cache.lookup('/assets/legacy.js'));
    assert.equal(cache.lookup('/.vite/manifest.json'), undefined);
    assert.equal(cache.lookup('/../package.json'), undefined);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
