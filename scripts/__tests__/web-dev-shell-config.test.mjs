// @vitest-environment node
// Checks the web shell's HTML viewport declaration (apps/web/index.html) and Vite dev proxy configuration
// (apps/web/vite.config.ts) so safe-area insets work on notched displays and lanes can point /api at their own backend.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const webDir = path.join(repoRoot, 'apps/web');
const indexHtmlPath = path.join(webDir, 'index.html');
const viteConfigPath = path.join(webDir, 'vite.config.ts');

async function loadWebViteConfig() {
  const require = createRequire(path.join(webDir, 'package.json'));
  const entry = path.join(path.dirname(require.resolve('vite/package.json')), 'dist/node/index.js');
  const { loadConfigFromFile } = await import(pathToFileURL(entry).href);
  const loaded = await loadConfigFromFile(
    { command: 'serve', mode: 'development' },
    viteConfigPath,
    webDir,
    'silent'
  );
  return loaded.config;
}

describe('web shell index.html viewport meta tag', () => {
  it('declares width=device-width, initial-scale=1.0, and viewport-fit=cover on the viewport meta tag', () => {
    const html = readFileSync(indexHtmlPath, 'utf8');
    const metaTags = [...html.matchAll(/<meta\b[^>]*>/gi)].map((match) => match[0]);
    const viewportTag = metaTags.find((tag) => /\bname\s*=\s*["']viewport["']/i.test(tag));
    expect(viewportTag).toBeDefined();
    const contentMatch = viewportTag.match(/\bcontent\s*=\s*["']([^"']*)["']/i);
    expect(contentMatch).not.toBeNull();
    const tokens = contentMatch[1]
      .split(',')
      .map((token) => token.trim())
      .filter(Boolean);
    expect(tokens).toEqual(['width=device-width', 'initial-scale=1.0', 'viewport-fit=cover']);
  });
});

describe('web shell Vite /api dev proxy target', () => {
  const originalTarget = process.env.ALBEDIAN_API_TARGET;

  afterEach(() => {
    if (originalTarget === undefined) {
      delete process.env.ALBEDIAN_API_TARGET;
    } else {
      process.env.ALBEDIAN_API_TARGET = originalTarget;
    }
  });

  it('defaults the /api proxy target to http://127.0.0.1:8000 when ALBEDIAN_API_TARGET is unset', async () => {
    delete process.env.ALBEDIAN_API_TARGET;
    const config = await loadWebViteConfig();
    expect(config.server?.port).toBe(5173);
    expect(config.server?.proxy?.['/api']).toEqual({
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
      bypass: expect.any(Function),
    });
  });

  it('falls back to http://127.0.0.1:8000 when ALBEDIAN_API_TARGET is empty or whitespace', async () => {
    process.env.ALBEDIAN_API_TARGET = '   ';
    const config = await loadWebViteConfig();
    expect(config.server?.proxy?.['/api']).toEqual({
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
      bypass: expect.any(Function),
    });
  });

  it('reads and trims ALBEDIAN_API_TARGET when set so a lane can point /api at its own backend port', async () => {
    process.env.ALBEDIAN_API_TARGET = '  http://127.0.0.1:8100  ';
    const config = await loadWebViteConfig();
    expect(config.server?.proxy?.['/api']).toEqual({
      target: 'http://127.0.0.1:8100',
      changeOrigin: true,
      bypass: expect.any(Function),
    });
  });
});

describe("the web app's own test command (web-test-env-1)", () => {
  it('sets no default environment, as the root config does not, so a file runs in node unless its first line asks for jsdom', async () => {
    const config = await loadWebViteConfig();
    expect(config.test?.environment, 'apps/web/vite.config.ts test.environment').toBeUndefined();
    const root = (await import(pathToFileURL(path.join(repoRoot, 'vitest.config.mts')).href)).default;
    expect(root.test?.environment, 'vitest.config.mts test.environment').toBeUndefined();
  });
});
