// No #! line: a module, imported by apps/web/vite.config.ts, scripts/check-built-page.mjs and their tests.
/**
 * The built web app's content security policy, sent report-only (web-csp-1): `vite preview`, which `pnpm field --lan`
 * serves the build with, sends it on every answer under POLICY_HEADER, so a browser reports in its console each load
 * the policy would refuse and refuses nothing. Enforcing it is a later unit, once a run through every screen reports
 * nothing. Each directive and why it allows what it does, read from the code and the built bundle [proposed]:
 * - default-src 'self': everything not named below, from the app's own origin only.
 * - script-src 'self' 'wasm-unsafe-eval' and the hash of the page's one inline script (SCRIPT_HASHES): the app's own
 *   scripts, WebAssembly, which the built bundle compiles, and the first frame's script, the page's own text
 *   (first-frame-inline-one, DECIDED-BY-LEAD 02:59 on 28 Nov), which the built-page check holds to the built page.
 * - worker-src 'self' blob:: the bundle starts a worker from a blob: address it makes.
 * - style-src 'self' and the hash of each inline style element of the page (STYLE_HASHES), which the built-page check
 *   holds to the built page.
 * - img-src 'self' data: blob:: the page's icon is a data: address; images made at run time are blob: addresses.
 * - media-src 'self' blob:: a chosen recording plays from a blob: address (App.tsx).
 * - font-src 'self' and connect-src 'self': the app's own fonts, records, catalogs and API (the API and the music
 *   through the same origin's proxy).
 * - object-src 'none', base-uri 'self', form-action 'self'.
 */
import crypto from 'node:crypto';

export const POLICY_HEADER = 'Content-Security-Policy-Report-Only';

/** The sha256 of each inline style element of the app's page, as a policy source ('sha256-<base64>'). */
export const STYLE_HASHES = ["'sha256-cMUDd9wo16CsYUazHeM4DJSbU/tZMmYdiX3r9lrfRaA='"];

/** The sha256 of each inline script element of the app's page (its first frame's script), as a policy source. */
export const SCRIPT_HASHES = ["'sha256-rVGvnPiwwApFqQmMsQa9+StHMyWYrP7jDM5/EtSrGJQ='"];

/** Each directive with its sources, in the order the header gives them. */
export const DIRECTIVES = [
  ['default-src', ["'self'"]],
  ['script-src', ["'self'", "'wasm-unsafe-eval'", ...SCRIPT_HASHES]],
  ['worker-src', ["'self'", 'blob:']],
  ['style-src', ["'self'", ...STYLE_HASHES]],
  ['img-src', ["'self'", 'data:', 'blob:']],
  ['media-src', ["'self'", 'blob:']],
  ['font-src', ["'self'"]],
  ['connect-src', ["'self'"]],
  ['object-src', ["'none'"]],
  ['base-uri', ["'self'"]],
  ['form-action', ["'self'"]],
];

/** The header's value: each directive and its sources, separated by "; ". */
export const POLICY = DIRECTIVES.map(([name, sources]) => `${name} ${sources.join(' ')}`).join('; ');

/** The policy source of an inline script element's text, as a browser hashes it: the same sha256 as a style's. */
export function scriptHash(text) {
  return styleHash(text);
}

/** The policy source of an inline style element's text, as a browser hashes it. */
export function styleHash(text) {
  return `'sha256-${crypto.createHash('sha256').update(text, 'utf8').digest('base64')}'`;
}
