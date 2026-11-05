// Fails a Vitest run in which any test did not run: it.skip, describe.skip, it.todo, skipIf, runIf, ctx.skip(),
// or a test left out because another one is marked .only. CI adds it to `pnpm test` (.github/workflows/gates.yml):
//
//   pnpm test --reporter=default --reporter=github-actions --reporter=./scripts/vitest-no-unexpected-skips.mjs
//
// It is not part of `pnpm test` itself, because Vitest also marks the tests that `-t` filters out as skipped.
import path from 'node:path';

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
    for (const testModule of testModules) {
      for (const testCase of testModule.children.allTests()) {
        const result = testCase.result();
        if (result.state !== 'skipped') continue;
        const how = testCase.options.mode === 'run' ? 'skipped while running' : testCase.options.mode;
        const why = result.note ? `${how}: ${result.note}` : how;
        notRun.push(`  ${path.relative(root, testModule.moduleId)} > ${testCase.fullName} (${why})`);
      }
    }
    if (notRun.length === 0) return;
    process.exitCode = 1;
    this.vitest.logger.error(
      `${notRun.length} test${notRun.length === 1 ? '' : 's'} did not run, which this reporter forbids:\n${notRun.join('\n')}`,
    );
  }
}
