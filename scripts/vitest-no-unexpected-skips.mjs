// Fails a Vitest run in which any test, suite or test file did not run: it.skip, describe.skip, it.todo,
// describe.todo, skipIf, runIf, ctx.skip(), a test left out because another one is marked .only, an empty test
// file, or a test file under root left out by --exclude, config.exclude or a CLI filter. CI adds it to `pnpm test`
// (.github/workflows/gates.yml):
//
//   pnpm test --reporter=default --reporter=github-actions --reporter=./scripts/vitest-no-unexpected-skips.mjs
//
// It is not part of `pnpm test` itself, because Vitest also marks the tests that `-t` filters out as skipped.
import fs from 'node:fs';
import path from 'node:path';

const IGNORED_DIRS = new Set(['node_modules', 'dist', 'build', 'coverage']);
const TEST_FILE = /\.(?:test|spec)\.(?:[cm]?[jt]sx?)$/;

/** Every *.test.* / *.spec.* file under root (skipping dot-folders and build/dependency folders), as relative POSIX paths. */
export function findTestFiles(root) {
  const found = [];
  const walk = (dir, relDir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name.startsWith('.') || IGNORED_DIRS.has(entry.name)) continue;
      const fullPath = path.join(dir, entry.name);
      const relPath = relDir ? path.posix.join(relDir, entry.name) : entry.name;
      if (entry.isDirectory()) walk(fullPath, relPath);
      else if (entry.isFile() && TEST_FILE.test(entry.name)) found.push(relPath);
    }
  };
  if (fs.existsSync(root)) walk(root, '');
  return found;
}

/** @implements {import('vitest/node').Reporter} */
export default class NoUnexpectedSkipsReporter {
  /** @param {import('vitest/node').Vitest} vitest */
  onInit(vitest) {
    this.vitest = vitest;
  }

  /** @param {ReadonlyArray<import('vitest/node').TestModule>} testModules */
  onTestRunEnd(testModules) {
    const root = this.vitest.config.root;
    const notRun = [];
    const collectedPaths = new Set();
    for (const testModule of testModules) {
      const relModule = path.relative(root, testModule.moduleId).split(path.sep).join('/');
      collectedPaths.add(relModule);
      const tests = Array.from(testModule.children.allTests());
      const suites = Array.from(testModule.children.allSuites());
      if (tests.length === 0 && suites.length === 0) {
        notRun.push(`  ${relModule} (no tests in file)`);
        continue;
      }
      for (const suite of suites) {
        if (
          (suite.options.mode === 'skip' || suite.options.mode === 'todo') &&
          Array.from(suite.children.allTests()).length === 0
        ) {
          notRun.push(`  ${relModule} > ${suite.fullName} (${suite.options.mode})`);
        }
      }
      for (const testCase of tests) {
        const result = testCase.result();
        if (result.state !== 'skipped') continue;
        const how = testCase.options.mode === 'run' ? 'skipped while running' : testCase.options.mode;
        const why = result.note ? `${how}: ${result.note}` : how;
        notRun.push(`  ${relModule} > ${testCase.fullName} (${why})`);
      }
    }
    for (const expectedFile of findTestFiles(root)) {
      if (!collectedPaths.has(expectedFile)) {
        notRun.push(`  ${expectedFile} (file did not run)`);
      }
    }
    if (notRun.length === 0) return;
    process.exitCode = 1;
    this.vitest.logger.error(
      `${notRun.length} test${notRun.length === 1 ? '' : 's'} did not run, which this reporter forbids:\n${notRun.join('\n')}`,
    );
  }
}
