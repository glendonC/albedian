// @vitest-environment node
// The built web app's content security policy, report-only (web-csp-1): what it allows, written out here so a change
// to it is a change to this file; the page's inline style and its one inline script held to their hashes; and the web app's config sending it from
// `vite preview` only. That the preview server sends it on its answers is held in fieldLan.test.mjs, and that the built
// page holds nothing inline it would refuse, in check-built-page.test.mjs.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { DIRECTIVES, POLICY, POLICY_HEADER, SCRIPT_HASHES, STYLE_HASHES, scriptHash, styleHash } from '../content-policy.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const page = fs.readFileSync(path.join(repoRoot, 'apps/web/index.html'), 'utf8');

describe("the built app's content security policy", () => {
  it('is sent report-only, and allows exactly these sources', () => {
    expect(POLICY_HEADER).toBe('Content-Security-Policy-Report-Only');
    expect(POLICY).toBe(
      [
        "default-src 'self'",
        `script-src 'self' 'wasm-unsafe-eval' ${SCRIPT_HASHES.join(' ')}`,
        "worker-src 'self' blob:",
        `style-src 'self' ${STYLE_HASHES.join(' ')}`,
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
      ].join('; '),
    );
  });

  it('names no host, no scheme of the web and nothing inline beyond the hashes of the page\'s own style and script', () => {
    const sources = DIRECTIVES.flatMap(([, s]) => s);
    for (const source of sources) {
      expect(source, source).not.toMatch(/^(\*|https?:|wss?:|'unsafe-inline'|'unsafe-eval'|'unsafe-hashes'|'strict-dynamic')$/);
      expect(source, source).not.toMatch(/\./);
    }
    expect(new Set(DIRECTIVES.map(([name]) => name)).size).toBe(DIRECTIVES.length);
  });

  it("holds the page's inline style to its hash, so a change to the style is a change to the policy", () => {
    const styles = [...page.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style\s*>/gi)].map((m) => m[1]);
    expect(styles.length).toBeGreaterThan(0);
    expect(styles.map(styleHash)).toEqual(STYLE_HASHES);
  });

  it("holds the page's one inline script to its hash, so a change to the script is a change to the policy", () => {
    // first-frame-inline-one (DECIDED-BY-LEAD 02:59 on 28 Nov): the first frame's script is the page's own, not a file
    const scripts = [...page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)].filter((m) => !/\bsrc\s*=/i.test(m[1])).map((m) => m[2]);
    expect(scripts).toHaveLength(1);
    expect(scripts.map(scriptHash)).toEqual(SCRIPT_HASHES);
  });

  it('hashes a script as a browser does, as it hashes a style: sha256 of its text, in base64', () => {
    expect(scriptHash('')).toBe("'sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU='");
    expect(scriptHash('/* µ */')).toBe(styleHash('/* µ */'));
    expect(scriptHash('x()')).not.toBe(scriptHash('x() '));
  });

  it('hashes a style as a browser does: sha256 of its text, in base64', () => {
    expect(styleHash('')).toBe("'sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU='");
    expect(styleHash('/* \u00b5 */')).toBe("'sha256-SY/wMp0QQN/9E35JSm7rgNYYIvPevu7/mtJyYzK73m4='");
    expect(styleHash('body{}')).not.toBe(styleHash('body{ }'));
  });

  it("is in the web app's config for vite preview, and not for the dev server", () => {
    const config = fs.readFileSync(path.join(repoRoot, 'apps/web/vite.config.ts'), 'utf8');
    expect(config).toContain("import { POLICY, POLICY_HEADER } from '../../scripts/content-policy.mjs';");
    expect(config).toMatch(/preview: \{\n\s+headers: \{ \[POLICY_HEADER\]: POLICY \},\n\s+\}/);
    expect(config.match(/POLICY_HEADER\]/g)).toHaveLength(1);
  });
});
