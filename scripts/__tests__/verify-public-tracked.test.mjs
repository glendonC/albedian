// The web build's public folder check (scripts/verify-public-tracked.mjs): Vite copies every file under
// apps/web/public into the build, dotfiles included, and follows symbolic links, so a file git does not track there,
// or any link, must stop the build. Scratch repositories are made outside this one; staging a file is enough for git
// to track it, so no commit is needed.
import { execFileSync, spawnSync } from 'node:child_process';
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';
import { PLUGIN_NAME, PUBLIC_DIR, publicTrackedCheck, verifyPublicTracked } from '../verify-public-tracked.mjs';

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

  it('fails on a symbolic link git tracks one folder down, since the walk looks for links at every depth', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/textures/sky.png`]: 'png', 'outside/holiday/clip-01.png': 'png' } });
    symlinkSync('../../../../outside/holiday', path.join(root, PUBLIC_DIR, 'textures', 'renders'));
    track(root, `${PUBLIC_DIR}/textures/sky.png`, `${PUBLIC_DIR}/textures/renders`);
    expect(publicTrackedCheck(root)).toEqual({
      ok: false,
      untracked: 0,
      links: 1,
      message:
        `[verify:public] FAIL: 1 entry under ${PUBLIC_DIR} is a symbolic link, which Vite follows when it copies the ` +
        `folder into the build, so the check refuses it whether git tracks it or not. ` +
        `List them on this machine with: find ${PUBLIC_DIR} -type l`,
    });
  });

  it('fails on an untracked file inside a dot folder such as .well-known/, since Vite copies dot folders too', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/kept.json`]: '{}', [`${PUBLIC_DIR}/.well-known/notes.txt`]: 'x' } });
    track(root, `${PUBLIC_DIR}/kept.json`);
    expect(publicTrackedCheck(root)).toEqual({
      ok: false,
      untracked: 1,
      links: 0,
      message:
        `[verify:public] FAIL: 1 file under ${PUBLIC_DIR} is not tracked by git, and Vite would copy it into the build. ` +
        `List them on this machine with: git status --short --ignored ${PUBLIC_DIR}`,
    });
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

/**
 * A scratch checkout laid out like this one for one app: its own Vite config and the check at the paths the config
 * imports them from, a page to build, and the app's node_modules linked outside the public folder.
 */
function appCheckout(app, files) {
  const root = scratch({ files: { [`${app}/index.html`]: '<!doctype html><title>scratch</title>\n', ...files } });
  mkdirSync(path.join(root, 'scripts'), { recursive: true });
  copyFileSync(path.join(repoRoot, 'scripts/verify-public-tracked.mjs'), path.join(root, 'scripts/verify-public-tracked.mjs'));
  copyFileSync(path.join(repoRoot, app, 'vite.config.ts'), path.join(root, app, 'vite.config.ts'));
  symlinkSync(path.join(repoRoot, app, 'node_modules'), path.join(root, app, 'node_modules'));
  return root;
}

/** Vite's own command line, run in an app's folder as a person would type vite build there. */
function viteBuild(root, app) {
  const vite = path.join(repoRoot, app, 'node_modules/vite/bin/vite.js');
  const run = spawnSync(process.execPath, [vite, 'build'], {
    cwd: path.join(root, app),
    encoding: 'utf8',
    env: { ...process.env, NO_COLOR: '1' },
  });
  return { status: run.status, output: `${run.stdout}${run.stderr}` };
}

/** Vite's JavaScript API, from the copy an app builds with. */
async function viteApi(app) {
  const require = createRequire(path.join(repoRoot, app, 'package.json'));
  const entry = path.join(path.dirname(require.resolve('vite/package.json')), 'dist/node/index.js');
  return import(pathToFileURL(entry).href);
}

async function flatPlugins(list) {
  const plugins = [];
  for (const item of list ?? []) {
    const plugin = await item;
    if (Array.isArray(plugin)) plugins.push(...(await flatPlugins(plugin)));
    else if (plugin) plugins.push(plugin);
  }
  return plugins;
}

describe('the check as a Vite plugin, at build start', () => {
  it("refuses the spike's build, as its package.json runs it, while apps/web/public holds an untracked file", () => {
    const spike = JSON.parse(readFileSync(path.join(repoRoot, 'apps/webgpu-spike/package.json'), 'utf8'));
    expect(spike.scripts.build).toBe('vite build');
    const root = appCheckout('apps/webgpu-spike', {
      [`${PUBLIC_DIR}/kept.json`]: '{}',
      [`${PUBLIC_DIR}/holiday/clip-01.png`]: 'png',
    });
    track(root, `${PUBLIC_DIR}/kept.json`);
    const dist = path.join(root, 'apps/webgpu-spike/dist');

    const refused = viteBuild(root, 'apps/webgpu-spike');
    expect(refused.status).not.toBe(0);
    expect(refused.output).toContain(`[verify:public] FAIL: 1 file under ${PUBLIC_DIR} is not tracked by git`);
    expect(refused.output).not.toMatch(/holiday|clip-01/);
    // A refusal prints its message alone: no stack trace, so no absolute path from this machine
    expect(refused.output).not.toMatch(/^\s+at /m);
    expect(existsSync(dist)).toBe(false);

    track(root, `${PUBLIC_DIR}/holiday/clip-01.png`);
    const built = viteBuild(root, 'apps/webgpu-spike');
    expect(built.status).toBe(0);
    expect(built.output).toContain(`[verify:public] OK: git tracks all 2 files under ${PUBLIC_DIR}, and none is a symbolic link.`);
    expect(existsSync(path.join(dist, 'holiday/clip-01.png'))).toBe(true);
  }, 120_000);

  it('runs at build start in every Vite config in the repository', async () => {
    const configs = execFileSync('git', ['-C', repoRoot, 'ls-files', '--', '*vite.config.*'], { encoding: 'utf8' })
      .split('\n')
      .filter(Boolean);
    expect(configs).toContain('apps/webgpu-spike/vite.config.ts');
    expect(configs).toContain('apps/web/vite.config.ts');
    for (const file of configs) {
      const app = path.dirname(file);
      const { loadConfigFromFile } = await viteApi(app);
      const loaded = await loadConfigFromFile(
        { command: 'build', mode: 'production' },
        path.join(repoRoot, file),
        path.join(repoRoot, app),
        'silent'
      );
      const plugins = await flatPlugins(loaded.config.plugins);
      expect(plugins.map((plugin) => plugin.name), file).toContain(PLUGIN_NAME);
    }
  }, 120_000);

  it("refuses a direct vite build of the app's own config, which skips pnpm build and its first step", () => {
    const root = appCheckout('apps/web', {
      [`${PUBLIC_DIR}/kept.json`]: '{}',
      [`${PUBLIC_DIR}/holiday/clip-01.png`]: 'png',
    });
    track(root, `${PUBLIC_DIR}/kept.json`);
    const refused = viteBuild(root, 'apps/web');
    expect(refused.status).not.toBe(0);
    expect(refused.output).toContain(`[verify:public] FAIL: 1 file under ${PUBLIC_DIR} is not tracked by git`);
    expect(refused.output).not.toMatch(/holiday|clip-01/);
    // A refusal prints its message alone: no stack trace, so no absolute path from this machine
    expect(refused.output).not.toMatch(/^\s+at /m);
    expect(existsSync(path.join(root, 'apps/web/dist'))).toBe(false);
  }, 120_000);

  /** The plugin's two hooks, given a resolved config as Vite passes it. */
  function start(root, publicDir, build = {}) {
    const plugin = verifyPublicTracked();
    plugin.configResolved({
      root,
      publicDir,
      logger: { info() {} },
      build: { watch: null, copyPublicDir: true, ...build },
    });
    plugin.buildStart();
  }

  it('checks the public folder its config really uses, not apps/web/public', () => {
    const root = scratch({ files: { 'site/static/kept.json': '{}', 'site/static/clip-01.png': 'png' } });
    track(root, 'site/static/kept.json');
    expect(() => start(root, path.join(root, 'site/static'))).toThrow(
      '[verify:public] FAIL: 1 file under site/static is not tracked by git'
    );
  });

  it('lets a build through that copies no public folder', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/clip-01.png`]: 'png' } });
    expect(() => start(root, '')).not.toThrow();
    expect(() => start(root, path.join(root, PUBLIC_DIR), { copyPublicDir: false })).not.toThrow();
  });

  it('refuses a watch build, which copies the public folder again before each rebuild, ahead of the check', () => {
    const root = scratch({ files: { [`${PUBLIC_DIR}/kept.json`]: '{}' } });
    track(root, PUBLIC_DIR);
    expect(() => start(root, path.join(root, PUBLIC_DIR))).not.toThrow();
    expect(() => start(root, path.join(root, PUBLIC_DIR), { watch: {} })).toThrow(
      '[verify:public] FAIL: a watch build copies the public folder again before each rebuild'
    );
  });

  it('fails closed when the public folder is outside any git checkout', () => {
    const root = scratch({ git: false, files: { [`${PUBLIC_DIR}/kept.json`]: '{}' } });
    expect(() => start(root, path.join(root, PUBLIC_DIR))).toThrow(
      '[verify:public] FAIL: git could not list the files it tracks here'
    );
  });
});
