// The docs gate's link check (scripts/verify-docs.mjs): every relative Markdown link in README.md and docs/ must
// name a file or folder that exists. Scratch folders are made outside the repository.
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';
import { deadLinks, docsGate, markdownFiles, relativeLinks } from '../verify-docs.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const roots = [];

function scratch(files) {
  const root = realpathSync(mkdtempSync(path.join(os.tmpdir(), 'albedian-docs-')));
  roots.push(root);
  for (const [name, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    writeFileSync(path.join(root, name), text);
  }
  return root;
}

afterAll(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
});

describe('the docs gate link check', () => {
  it('reports a dead relative link by file, line and target', () => {
    const root = scratch({
      'README.md': 'See [the guide](docs/guide.md) and ![a figure](docs/figure.png).\n',
      'docs/guide.md': '# Guide\n\nBack to [the readme](../README.md).\nAnd [a gone page](./missing.md#part).\n',
      'docs/figure.png': 'not really a picture',
    });
    expect(deadLinks(root)).toEqual(['docs/guide.md:4: ./missing.md#part']);
  });

  it('follows folders and repository-absolute links, and skips URLs and fragments', () => {
    const root = scratch({
      'README.md': '[docs](docs/) [root](/docs/a/b.md) [web](https://example.org/x.md) [mail](mailto:a@b.c) [here](#top)\n',
      'docs/a/b.md': '[up two](../../README.md)\n',
    });
    expect(relativeLinks(root).map(({ target }) => target)).toEqual(['docs/', '/docs/a/b.md', '../../README.md']);
    expect(deadLinks(root)).toEqual([]);
  });

  it('reads README.md and every Markdown file under docs/, and nothing else', () => {
    const root = scratch({
      'README.md': '',
      'docs/one.md': '',
      'docs/deep/two.md': '',
      'docs/notes.txt': '[dead](missing.md)',
      'other/three.md': '[dead](missing.md)',
    });
    expect(markdownFiles(root)).toEqual(['README.md', 'docs/deep/two.md', 'docs/one.md']);
  });

  it('fails the gate on a dead link, and passes this repository', () => {
    const root = scratch({ 'docs/page.md': 'See [a gone page](gone.md).\n' });
    expect(docsGate(root).errors).toContain('Dead relative link: docs/page.md:1: gone.md');
    expect(relativeLinks(repoRoot).length).toBeGreaterThan(0);
    expect(docsGate(repoRoot).errors).toEqual([]);
  });
});
