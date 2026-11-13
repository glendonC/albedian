// Every text that runs git in this repository's tracked JavaScript and TypeScript files sits in a call that gives
// git the helper's environment (scripts/git-env.mjs), so GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE or GIT_COMMON_DIR
// set by a caller never turn it on another repository, or is excused by name with its reason. Every file of those
// kinds is read with TypeScript's parser, wherever it lives: quotes, templates, shell lines and wrappers make no
// difference, and comments are not read. The Python files have the same scan in tests/integration/test_git_env.py;
// both take their rule's tables (git's commands, the texts that must and must not count, each form and case, and
// the excused texts with their reasons) from tests/fixtures/git_calls.json.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';
import { gitEnvironment } from '../git-env.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const CALLS = JSON.parse(readFileSync(path.join(repoRoot, 'tests/fixtures/git_calls.json'), 'utf8'));
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// The Python scan's rule, written the same: git alone or a path that ends in /git, or git followed by an option or
// one of git's own commands (ending where no letter, digit or hyphen follows), at the start or after a space, a
// shell separator, a quote or a path's slash; NUL stands for a template's run-time part
const GIT_COMMAND = new RegExp(
  `^(?:[^ \\t\\n\\r\\f\\v]*/)?git$|(?:^|[ \\t\\n\\r\\f\\v;&|(\`'"/])git[ \\t\\n\\r\\f\\v]+(?:-|\\x00|(?:${CALLS.commands
    .map(escape)
    .join('|')})(?![A-Za-z0-9_-]))`
);
const HELPER = 'gitEnvironment()';
const SCRIPT_KINDS = {
  '.js': ts.ScriptKind.JS,
  '.mjs': ts.ScriptKind.JS,
  '.cjs': ts.ScriptKind.JS,
  '.jsx': ts.ScriptKind.JSX,
  '.ts': ts.ScriptKind.TS,
  '.mts': ts.ScriptKind.TS,
  '.cts': ts.ScriptKind.TS,
  '.tsx': ts.ScriptKind.TSX,
};

/** A string literal's or template's text, a template's run-time parts read as NUL; null for any other node. */
function literalText(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) return node.head.text + node.templateSpans.map((span) => `\0${span.literal.text}`).join('');
  return null;
}

const isCall = (node) => ts.isCallExpression(node) || ts.isNewExpression(node) || ts.isTaggedTemplateExpression(node);
/** The name a call is known by: `spawnSync` for child_process.spawnSync, `push` for failures.push, `$` for a tag. */
function lastName(call, file) {
  const target = ts.isTaggedTemplateExpression(call) ? call.tag : call.expression;
  if (ts.isPropertyAccessExpression(target)) return target.name.text;
  return ts.isIdentifier(target) ? target.text : target.getText(file);
}

/** True when one of the call's arguments is an options object whose env names the helper. */
function passesHelper(call, file) {
  if (ts.isTaggedTemplateExpression(call)) return false;
  return (call.arguments ?? []).some(
    (argument) =>
      ts.isObjectLiteralExpression(argument) &&
      argument.properties.some(
        (property) =>
          ts.isPropertyAssignment(property) && property.name.getText(file) === 'env' && property.initializer.getText(file).includes(HELPER)
      )
  );
}

/** Each text in a source that runs git: its line, its text, the last name of the innermost call around it (null for
 * none) and whether a call around it passes the helper's environment. */
export function gitTexts(source, name = 'text.ts') {
  const file = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true, SCRIPT_KINDS[path.extname(name)] ?? ts.ScriptKind.TS);
  const found = [];
  const visit = (node) => {
    const text = literalText(node);
    if (text !== null && GIT_COMMAND.test(text)) {
      const calls = [];
      for (let up = node.parent; up; up = up.parent) if (isCall(up)) calls.push(up);
      found.push({
        line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1,
        text,
        call: calls.length ? lastName(calls[0], file) : null,
        passes: calls.some((call) => passesHelper(call, file)),
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

function tracked() {
  return execFileSync('git', ['-C', repoRoot, 'ls-files', '-z'], { encoding: 'utf8', env: gitEnvironment() })
    .split('\0')
    .filter(Boolean)
    .sort();
}

/** The texts that run git without the helper and are not excused, how many texts run git, and the excuses used. An
 * excuse names a file, the last name of the innermost call (null: outside any call) and a text (null: every text
 * in that call in that file). */
function scan(names, read = (name) => readFileSync(path.join(repoRoot, name), 'utf8'), excuses = CALLS.not_run) {
  const key = (file, call, text) => JSON.stringify([file, call, text]);
  const excused = new Set(excuses.map((entry) => key(entry.file, entry.call, entry.text)));
  const missing = [];
  const used = new Set();
  let count = 0;
  for (const name of names) {
    for (const found of gitTexts(read(name), name)) {
      count += 1;
      const at = [key(name, found.call, found.text), key(name, found.call, null)].find((each) => excused.has(each));
      if (at !== undefined) used.add(at);
      else if (!found.passes) missing.push(`${name}:${found.line} ${found.call ?? 'outside any call'}: ${JSON.stringify(found.text)}`);
    }
  }
  return { missing, count, used };
}

// The two tests that parse every tracked file of these kinds: 1.24 s at a load of 60, past Vitest's 5 s default at
// a load above 100 [tested, 2026-11-13]; 60 s is far above either and still ends a stuck parse [proposed]
const WHOLE_REPOSITORY_MS = 60_000;
const nodeFiles = () => tracked().filter((name) => CALLS.kinds.node.includes(path.extname(name)));
const nodeExcuses = () => CALLS.not_run.filter((entry) => CALLS.kinds.node.includes(path.extname(entry.file)));

/** The excuses for files of these kinds that no text in them used: each must go once its text is gone. */
const staleExcuses = (used) => nodeExcuses().filter((entry) => !used.has(JSON.stringify([entry.file, entry.call, entry.text])));

describe('git calls in the JavaScript and TypeScript files', () => {
  it("finds every text that runs git in a tracked file of those kinds inside a call that passes the helper's environment", () => {
    const names = nodeFiles();
    expect(names.length, 'the scan read too few files').toBeGreaterThan(250);
    expect(Object.keys(SCRIPT_KINDS).sort()).toEqual([...CALLS.kinds.node].sort());
    const { missing, count, used } = scan(names);
    expect(missing, "texts that run git with no call passing the helper's environment").toEqual([]);
    expect(count, 'the scan found too few texts that run git; it no longer sees them').toBeGreaterThanOrEqual(15);
    expect(staleExcuses(used), 'excused texts that are no longer there').toEqual([]);
  }, WHOLE_REPOSITORY_MS);

  it('reports an excuse no text uses, and not one that is used', () => {
    const excuses = nodeExcuses();
    expect(excuses.length).toBeGreaterThanOrEqual(2);
    expect(staleExcuses(new Set())).toEqual(excuses);
    const [first, ...rest] = excuses;
    expect(staleExcuses(new Set([JSON.stringify([first.file, first.call, first.text])]))).toEqual(rest);
  });

  it('tells a git command line from a sentence that names git, as the Python scan does', () => {
    expect(CALLS.runs.filter((text) => !GIT_COMMAND.test(text)), 'runs read as sentences').toEqual([]);
    expect(CALLS.names.filter((text) => GIT_COMMAND.test(text)), 'sentences read as runs').toEqual([]);
  });

  for (const [form, template] of Object.entries(CALLS.node_forms)) {
    for (const quote of ["'", '"', '`']) {
      it(`finds ${form} written with ${quote} and passes it only with the helper named at the call`, () => {
        const write = (env) => template.replaceAll('{q}', quote).replaceAll('{env}', env);
        expect(gitTexts(write('')).map((found) => found.passes), write('')).toEqual([false]);
        expect(gitTexts(write(`, { env: ${HELPER} }`)).map((found) => found.passes)).toEqual([true]);
        expect(gitTexts(`const env = {};\n${write(', { env }')}`).map((found) => found.passes)).toEqual([false]);
      });
    }
  }

  for (const entry of CALLS.node_cases) {
    it(`reads ${entry.what} as the fixture says`, () => {
      expect(gitTexts(entry.source)).toEqual(entry.found);
    });
  }

  it('holds an excused text to its own file and its own call', () => {
    const entry = CALLS.not_run.find(
      (each) => each.call !== null && each.text !== null && CALLS.kinds.node.includes(path.extname(each.file))
    );
    const source = readFileSync(path.join(repoRoot, entry.file), 'utf8');
    expect(scan([entry.file]).missing).toEqual([]);
    const elsewhere = scan(['scripts/elsewhere.mjs'], () => source);
    expect(elsewhere.missing.length, 'an excuse held in another file').toBeGreaterThan(0);
    const otherCall = scan([entry.file], () => `spawnSync(${JSON.stringify(entry.text)}, []);\n`);
    expect(otherCall.missing.length, 'an excuse held in another call').toBeGreaterThan(0);
    expect(otherCall.used.size).toBe(0);
  });

  it('holds an excuse for every text of a call to that file and that call', () => {
    const { excuse, source, missing } = CALLS.excuse_cases.node;
    const inFile = scan([excuse.file], () => source, [excuse]);
    expect([inFile.count, [...inFile.used]]).toEqual([3, [JSON.stringify([excuse.file, excuse.call, null])]]);
    expect(inFile.missing, 'a text of another call').toEqual(missing);
    const elsewhere = scan(['scripts/elsewhere.mjs'], () => source, [excuse]);
    expect([elsewhere.missing.length, elsewhere.used.size], 'an excuse held in another file').toEqual([3, 0]);
  });

  it("runs every program of the field check through one runner that passes the helper's environment", () => {
    const file = ts.createSourceFile('field-check.mjs', readFileSync(path.join(repoRoot, 'scripts/field-check.mjs'), 'utf8'), ts.ScriptTarget.Latest, true);
    const runners = [];
    const visit = (node) => {
      if (ts.isMethodDeclaration(node) && node.name.getText(file) === 'run') runners.push(node);
      ts.forEachChild(node, visit);
    };
    visit(file);
    expect(runners.length, 'the field check has one runner').toBe(1);
    const spawns = [];
    const inRunner = (node) => {
      if (ts.isCallExpression(node) && node.expression.getText(file) === 'spawnSync') spawns.push(node);
      ts.forEachChild(node, inRunner);
    };
    inRunner(runners[0]);
    expect(spawns.length, 'the runner starts its program once').toBe(1);
    expect(passesHelper(spawns[0], file), "the runner gives its program the helper's environment").toBe(true);
  });

  it('reaches the one helper from every file that imports it, by a path that resolves to scripts/git-env.mjs', () => {
    const importers = [];
    for (const name of nodeFiles()) {
      const file = ts.createSourceFile(name, readFileSync(path.join(repoRoot, name), 'utf8'), ts.ScriptTarget.Latest, true, SCRIPT_KINDS[path.extname(name)]);
      for (const statement of file.statements) {
        if (ts.isImportDeclaration(statement) && statement.moduleSpecifier.text.endsWith('git-env.mjs')) {
          importers.push([name, path.relative(repoRoot, path.resolve(repoRoot, path.dirname(name), statement.moduleSpecifier.text))]);
        }
      }
    }
    expect(importers.filter(([, target]) => target !== 'scripts/git-env.mjs'), 'imports that miss the helper').toEqual([]);
    const names = importers.map(([name]) => name);
    expect(names).toEqual(expect.arrayContaining(['apps/web/spike/listening/render.mjs', 'apps/web/spike/music/render.mjs']));
  }, WHOLE_REPOSITORY_MS);
});
