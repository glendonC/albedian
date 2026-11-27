// The docs gate (scripts/verify-docs.mjs): every relative Markdown link in README.md and docs/ must name a file or
// folder that exists, and every Kiro spec, the twelve listed and any other folder under .kiro/specs/, must hold its
// three documents. Scratch folders are made outside the repository.
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';
import { agentsPaths, deadAgentsPaths, deadLinks, docsGate, markdownFiles, relativeLinks } from '../verify-docs.mjs';

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

// The twelve specs .kiro/specs/ holds on main 927612bd3, the four of 11 November last.
const TWELVE_SPECS = [
  'observation-session',
  'media-experiment-runtime',
  'observation-solution',
  'spatial-camera-navigation',
  'voice-native-actions',
  'design-system-accessibility',
  'export-and-provenance',
  'integration-and-testing',
  'predicted-sky-for-every-upload',
  'ios-capture-companion',
  'upload-jobs',
  'restoration-for-every-upload',
];
const LONG_ENOUGH = '# A spec document\n\nLong enough to pass the gate: fifty bytes or more of text.\n';

/** The three documents of each named spec, each long enough to pass. */
function specFiles(specs) {
  const files = {};
  for (const spec of specs) {
    for (const doc of ['requirements.md', 'design.md', 'tasks.md']) files[`.kiro/specs/${spec}/${doc}`] = LONG_ENOUGH;
  }
  return files;
}

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

  it('reads README.md, AGENTS.md, research/experiments/EXPERIMENT_REGISTRY.md and every Markdown file under docs/', () => {
    const root = scratch({
      'README.md': '',
      'AGENTS.md': '',
      'docs/one.md': '',
      'docs/deep/two.md': '',
      'research/experiments/EXPERIMENT_REGISTRY.md': '',
      'docs/notes.txt': '[dead](missing.md)',
      'other/three.md': '[dead](missing.md)',
    });
    expect(markdownFiles(root)).toEqual([
      'README.md',
      'AGENTS.md',
      'docs/deep/two.md',
      'docs/one.md',
      'research/experiments/EXPERIMENT_REGISTRY.md',
    ]);
  });

  it('checks every backticked repository path in AGENTS.md against the filesystem', () => {
    const root = scratch({
      'AGENTS.md': [
        '| Agent | Scope |',
        '| `architecture` | `packages/contracts/`, `docs/adr/`, `docs/internal/integration-ledger.md` |',
        '| `science` | `DAOStarFinder`/SEP/matched filter, `services/science/` |',
      ].join('\n'),
      'packages/contracts/index.ts': '',
      'services/science': 'a file where a directory is required',
    });
    expect(agentsPaths(root)).toEqual([
      { file: 'AGENTS.md', line: 2, target: 'packages/contracts/' },
      { file: 'AGENTS.md', line: 2, target: 'docs/adr/' },
      { file: 'AGENTS.md', line: 2, target: 'docs/internal/integration-ledger.md' },
      { file: 'AGENTS.md', line: 3, target: 'services/science/' },
    ]);
    expect(deadAgentsPaths(root)).toEqual([
      'AGENTS.md:2: docs/adr/',
      'AGENTS.md:2: docs/internal/integration-ledger.md',
      'AGENTS.md:3: services/science/',
    ]);
    expect(docsGate(root).errors).toContain('Missing path in AGENTS.md: AGENTS.md:2: docs/adr/');
  });

  it('checks the three documents of every one of the twelve Kiro specs, the four of 11 November among them', () => {
    const all = scratch(specFiles(TWELVE_SPECS));
    expect(docsGate(all).errors.filter((e) => e.startsWith('Missing or empty Kiro spec'))).toEqual([]);
    for (const spec of ['predicted-sky-for-every-upload', 'ios-capture-companion', 'upload-jobs', 'restoration-for-every-upload']) {
      const files = specFiles(TWELVE_SPECS);
      delete files[`.kiro/specs/${spec}/tasks.md`];
      files[`.kiro/specs/${spec}/design.md`] = 'too short';
      expect(docsGate(scratch(files)).errors.filter((e) => e.startsWith('Missing or empty Kiro spec'))).toEqual([
        `Missing or empty Kiro spec document: .kiro/specs/${spec}/design.md`,
        `Missing or empty Kiro spec document: .kiro/specs/${spec}/tasks.md`,
      ]);
    }
  });

  it('fails when a whole expected spec folder is gone, the four of 11 November among them', () => {
    for (const spec of ['predicted-sky-for-every-upload', 'ios-capture-companion', 'upload-jobs', 'restoration-for-every-upload']) {
      const files = specFiles(TWELVE_SPECS.filter((name) => name !== spec));
      expect(docsGate(scratch(files)).errors.filter((e) => e.startsWith('Missing or empty Kiro spec'))).toEqual([
        `Missing or empty Kiro spec document: .kiro/specs/${spec}/requirements.md`,
        `Missing or empty Kiro spec document: .kiro/specs/${spec}/design.md`,
        `Missing or empty Kiro spec document: .kiro/specs/${spec}/tasks.md`,
      ]);
    }
  });

  it('checks a spec folder that is on no list, so a new spec cannot pass unread, and reads no file there as a spec', () => {
    const files = specFiles(TWELVE_SPECS);
    files['.kiro/specs/README.md'] = LONG_ENOUGH;
    files['.kiro/specs/a-new-spec/requirements.md'] = LONG_ENOUGH;
    expect(docsGate(scratch(files)).errors.filter((e) => e.startsWith('Missing or empty Kiro spec'))).toEqual([
      'Missing or empty Kiro spec document: .kiro/specs/a-new-spec/design.md',
      'Missing or empty Kiro spec document: .kiro/specs/a-new-spec/tasks.md',
    ]);
  });

  it('counts the specs and their documents it checked in its summary', () => {
    const files = specFiles(TWELVE_SPECS);
    files['.kiro/specs/a-new-spec/requirements.md'] = LONG_ENOUGH;
    files['.kiro/specs/a-new-spec/design.md'] = LONG_ENOUGH;
    files['.kiro/specs/a-new-spec/tasks.md'] = LONG_ENOUGH;
    expect(docsGate(scratch(files)).summary).toContain('verified 13 Kiro specs (39 documents)');
    expect(docsGate(repoRoot).summary).toContain('verified 12 Kiro specs (36 documents)');
  });

  it('fails the gate on a dead link, and passes this repository', () => {
    const root = scratch({
      'docs/page.md': 'See [a gone page](gone.md).\n',
      'research/experiments/EXPERIMENT_REGISTRY.md': 'See [missing](../../docs/missing.md).\n',
    });
    const errors = docsGate(root).errors;
    expect(errors).toContain('Dead relative link: docs/page.md:1: gone.md');
    expect(errors).toContain('Dead relative link: research/experiments/EXPERIMENT_REGISTRY.md:1: ../../docs/missing.md');
    expect(relativeLinks(repoRoot).length).toBeGreaterThan(0);
    expect(agentsPaths(repoRoot).length).toBeGreaterThanOrEqual(16);
    expect(deadAgentsPaths(repoRoot)).toEqual([]);
    expect(docsGate(repoRoot).errors).toEqual([]);
  });
});
