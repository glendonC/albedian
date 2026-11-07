// The CI-only Vitest reporter fails a run in which a test did not run (scripts/vitest-no-unexpected-skips.mjs).
// Each case runs Vitest in a child process on scratch test files in a fresh temp folder, outside the repository,
// so the repository's own run never collects them. Vitest loads a custom reporter only from inside its root, so
// the reporter is copied, byte for byte, into that folder; CI loads the same file from the repository root.
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';

const here = path.dirname(fileURLToPath(import.meta.url));
const reporter = path.resolve(here, '..', 'vitest-no-unexpected-skips.mjs');
const vitestCli = path.join(path.dirname(createRequire(import.meta.url).resolve('vitest/package.json')), 'vitest.mjs');
const folders = [];
// The child's environment: no colour codes in its output, and no GitHub annotations from it
const childEnv = { ...process.env, NO_COLOR: '1', GITHUB_ACTIONS: '' };
delete childEnv.FORCE_COLOR;
const plain = (text) => text.replace(/\x1b\[[0-9;]*m/g, '');

const SKIPS = `
it('runs', () => {});
it.skip('skipped with it.skip', () => {});
describe.skip('a skipped suite', () => {
  it('inside a skipped suite', () => {});
});
describe.skip('an empty skipped suite', () => {});
describe.todo('a todo suite');
it.todo('a todo');
it.skipIf(true)('skipped with skipIf', () => {});
it.runIf(false)('left out by runIf', () => {});
it('skips itself', (context) => {
  context.skip('not ready');
});
`;
const CLEAN = `
it('runs', () => {});
describe('a suite', () => {
  it('runs too', () => {});
});
`;

/** Runs Vitest on scratch files ({ filename: source }), with or without the reporter; returns its exit status and output. */
function runVitestFiles(files, withReporter, extraArgs = []) {
  // The real path, because Vite resolves symlinks (macOS keeps the temp folder under /var -> /private/var)
  const folder = realpathSync(mkdtempSync(path.join(os.tmpdir(), 'albedian-skips-')));
  folders.push(folder);
  for (const [name, source] of Object.entries(files)) {
    writeFileSync(path.join(folder, name), source);
  }
  const reporters = ['--reporter=default'];
  if (withReporter) {
    copyFileSync(reporter, path.join(folder, 'reporter.mjs'));
    reporters.push('--reporter=./reporter.mjs');
  }
  try {
    const stdout = execFileSync(
      process.execPath,
      [vitestCli, 'run', '--root', folder, '--globals', ...reporters, ...extraArgs],
      {
        cwd: folder,
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
        env: childEnv,
      },
    );
    return { status: 0, output: plain(stdout) };
  } catch (error) {
    return { status: error.status, output: plain(`${error.stdout}${error.stderr}`) };
  }
}

/** Runs Vitest on one scratch file, with or without the reporter; returns its exit status and output. */
function runVitest(source, withReporter) {
  return runVitestFiles({ 'case.test.js': source }, withReporter);
}

afterAll(() => {
  for (const folder of folders) rmSync(folder, { recursive: true, force: true });
});

describe('the no-unexpected-skips reporter', () => {
  it('fails a run with every kind of skipped or todo test or suite and names each one', { timeout: 60_000 }, () => {
    const { status, output } = runVitest(SKIPS, true);
    expect(status).toBe(1);
    expect(output).toContain('8 tests did not run');
    for (const name of [
      'skipped with it.skip (skip)',
      'a skipped suite > inside a skipped suite (skip)',
      'an empty skipped suite (skip)',
      'a todo suite (todo)',
      'a todo (todo)',
      'skipped with skipIf (skip)',
      'left out by runIf (skip)',
      'skips itself (skipped while running: not ready)',
    ]) {
      expect(output).toContain(`case.test.js > ${name}`);
    }
  });

  it('passes the same file without the reporter, the control that shows the gap', { timeout: 60_000 }, () => {
    const { status, output } = runVitest(SKIPS, false);
    expect(status).toBe(0);
    expect(output).toMatch(/1 passed \| 5 skipped \| 1 todo/);
  });

  it('fails when a test file under root is excluded, left out by a filter, or has no tests', { timeout: 60_000 }, () => {
    const excluded = runVitestFiles(
      { 'kept.test.js': CLEAN, 'dropped.test.js': CLEAN },
      true,
      ['--exclude', 'dropped.test.js'],
    );
    expect(excluded.status).toBe(1);
    expect(excluded.output).toContain('dropped.test.js (file did not run)');

    const filtered = runVitestFiles({ 'kept.test.js': CLEAN, 'dropped.test.js': CLEAN }, true, ['kept.test.js']);
    expect(filtered.status).toBe(1);
    expect(filtered.output).toContain('dropped.test.js (file did not run)');

    const emptyFile = runVitestFiles({ 'kept.test.js': CLEAN, 'empty.test.js': '// no tests here\n' }, true);
    expect(emptyFile.status).toBe(1);
    expect(emptyFile.output).toContain('empty.test.js (no tests in file)');
  });

  it('passes a run in which every test ran', { timeout: 60_000 }, () => {
    const { status, output } = runVitest(CLEAN, true);
    expect(status).toBe(0);
    expect(output).not.toContain('did not run');
  });
});
