// The web build's public folder check (scripts/verify-public-tracked.mjs): Vite copies every file under
// apps/web/public into the build, dotfiles included, and follows symbolic links, so a file git does not track there,
// or any link, must stop the build. Scratch repositories are made outside this one; staging a file is enough for git
// to track it, so no commit is needed.
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';
import { PUBLIC_DIR, publicTrackedCheck } from '../verify-public-tracked.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const roots = [];

function scratch({ git = true, files = {} } = {}) {
  const root = realpathSync(mkdtempSync(path.join(os.tmpdir(), 'albedian-public-')));
  roots.push(root);
  if (git) execFileSync('git', ['init', '--quiet', root]);
  for (const [name, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    writeFileSync(path.join(root, name), text);
  }
  return root;
}

function track(root, ...names) {
  execFileSync('git', ['-C', root, 'add', '--', ...names]);
}

afterAll(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
});

describe('the public folder check', () => {
  it('passes when git tracks every file under the public folder', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/textures/sky.png`]: 'png', [`${PUBLIC_DIR}/index.json`]: '{}' } });
    track(root, PUBLIC_DIR);
    expect(publicTrackedCheck(root)).toEqual({
      ok: true,
      untracked: 0,
      links: 0,
      message: `[verify:public] OK: git tracks all 2 files under ${PUBLIC_DIR}, and none is a symbolic link.`,
    });
  });

  it('fails on a file git does not track, and names neither the file nor its folder', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/kept.json`]: '{}', [`${PUBLIC_DIR}/holiday/clip-01.mp4`]: 'mp4' } });
    track(root, `${PUBLIC_DIR}/kept.json`);
    const result = publicTrackedCheck(root);
    expect(result.ok).toBe(false);
    expect(result.untracked).toBe(1);
    expect(result.message).toBe(
      `[verify:public] FAIL: 1 file under ${PUBLIC_DIR} is not tracked by git, and Vite would copy it into the build. ` +
        `List them on this machine with: git status --short --ignored ${PUBLIC_DIR}`
    );
    expect(result.message).not.toMatch(/holiday|clip-01/);
  });

  it('fails on a file git ignores, since Vite copies ignored files too', () => {
    const root = scratch({
      files: {
        '.gitignore': `${PUBLIC_DIR}/private/\n`,
        [`${PUBLIC_DIR}/kept.json`]: '{}',
        [`${PUBLIC_DIR}/private/a.png`]: 'png',
        [`${PUBLIC_DIR}/private/b.png`]: 'png',
      },
    });
    track(root, '.gitignore', `${PUBLIC_DIR}/kept.json`);
    const result = publicTrackedCheck(root);
    expect(result.ok).toBe(false);
    expect(result.untracked).toBe(2);
    expect(result.message).toMatch(/^\[verify:public\] FAIL: 2 files under apps\/web\/public are not tracked by git/);
    expect(result.message).not.toMatch(/private|a\.png|b\.png/);
  });

  it('fails on an untracked dotfile such as .env, since Vite copies dotfiles too', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/kept.json`]: '{}', [`${PUBLIC_DIR}/.env`]: 'EXAMPLE=1\n' } });
    track(root, `${PUBLIC_DIR}/kept.json`);
    const result = publicTrackedCheck(root);
    expect(result.ok).toBe(false);
    expect(result.untracked).toBe(1);
    expect(result.message).toBe(
      `[verify:public] FAIL: 1 file under ${PUBLIC_DIR} is not tracked by git, and Vite would copy it into the build. ` +
        `List them on this machine with: git status --short --ignored ${PUBLIC_DIR}`
    );
  });

  it('fails on a symbolic link git tracks, even one to a folder git does not track, since Vite follows links', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/kept.json`]: '{}', 'outside/holiday/clip-01.png': 'png' } });
    symlinkSync('../../../outside/holiday', path.join(root, PUBLIC_DIR, 'renders'));
    track(root, `${PUBLIC_DIR}/kept.json`, `${PUBLIC_DIR}/renders`);
    // git tracks the link itself (mode 120000), not the folder it points to
    const staged = execFileSync('git', ['-C', root, 'ls-files', '--stage', '--', `${PUBLIC_DIR}/renders`], { encoding: 'utf8' });
    expect(staged).toMatch(/^120000 /);
    const result = publicTrackedCheck(root);
    expect(result).toEqual({
      ok: false,
      untracked: 0,
      links: 1,
      message:
        `[verify:public] FAIL: 1 entry under ${PUBLIC_DIR} is a symbolic link, which Vite follows when it copies the ` +
        `folder into the build, so the check refuses it whether git tracks it or not. ` +
        `List them on this machine with: find ${PUBLIC_DIR} -type l`,
    });
    expect(result.message).not.toMatch(/renders|outside|holiday|clip-01/);
  });

  it('counts links apart from untracked files, one line each, so its message never calls a tracked link untracked', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/kept.json`]: '{}', [`${PUBLIC_DIR}/notes.txt`]: 'notes' } });
    symlinkSync('kept.json', path.join(root, PUBLIC_DIR, 'alias.json'));
    track(root, `${PUBLIC_DIR}/kept.json`);
    expect(publicTrackedCheck(root)).toEqual({
      ok: false,
      untracked: 1,
      links: 1,
      message:
        `[verify:public] FAIL: 1 file under ${PUBLIC_DIR} is not tracked by git, and Vite would copy it into the build. ` +
        `List them on this machine with: git status --short --ignored ${PUBLIC_DIR}\n` +
        `[verify:public] FAIL: 1 entry under ${PUBLIC_DIR} is a symbolic link, which Vite follows when it copies the ` +
        `folder into the build, so the check refuses it whether git tracks it or not. ` +
        `List them on this machine with: find ${PUBLIC_DIR} -type l`,
    });
  });

  it('runs first in the web build, before anything is compiled or copied', () => {
    const web = JSON.parse(readFileSync(path.join(repoRoot, 'apps/web/package.json'), 'utf8'));
    expect(web.scripts.build).toBe('node ../../scripts/verify-public-tracked.mjs && tsc --noEmit && vite build');
  });

  it('fails outside a git checkout, since it cannot tell what the build would ship', () => {
    const root = scratch({ git: false, files: { [`${PUBLIC_DIR}/kept.json`]: '{}' } });
    const result = publicTrackedCheck(root);
    expect(result.ok).toBe(false);
    expect(result.untracked).toBeNull();
    expect(result.links).toBeNull();
    expect(result.message).toBe(
      `[verify:public] FAIL: git could not list the files it tracks here, so the build cannot tell which files under ${PUBLIC_DIR} it would ship.`
    );
  });
});
