import { parseArgs } from 'node:util';

export const DEFAULT_CAPTURE_URL = 'http://localhost:5173';
export const DEFAULT_RENDERER = 'both';
export const DEFAULT_SCENE = '0';
export const DEFAULT_TIME = '4';
export const DEFAULT_SIZE = '1920x1080';
export const USAGE_LINE =
  'usage: node scripts/capture_landing.mjs --out <dir> [--url ...] [--renderer webgl|webgpu|both]';

/** Parses a `WxH` positive integer viewport specification into `[width, height]`. */
export function parseViewportSize(sizeText) {
  const match = /^([1-9]\d*)x([1-9]\d*)$/.exec(String(sizeText));
  if (!match) {
    throw new Error(`Invalid --size ${JSON.stringify(sizeText)}; expected positive <width>x<height>.`);
  }
  return [Number(match[1]), Number(match[2])];
}

/** Resolves `--renderer` (`webgl`, `webgpu`, or `both`) into an ordered list of renderer kinds. */
export function resolveRendererKinds(renderer) {
  if (renderer === 'both') return ['webgl', 'webgpu'];
  if (renderer === 'webgl' || renderer === 'webgpu') return [renderer];
  throw new Error(`Invalid --renderer ${JSON.stringify(renderer)}; expected webgl, webgpu, or both.`);
}

/** Builds the frozen landing URL for a single renderer capture. */
export function buildCaptureTargetUrl({ baseUrl, scene, t, kind }) {
  const params = new URLSearchParams({ scene, freeze: '1', t });
  if (kind === 'webgpu') params.set('renderer', 'webgpu');
  const trimmedBase = String(baseUrl).replace(/\/+$/, '');
  return `${trimmedBase}/?${params}`;
}

/** Parses CLI arguments, returning `null` when required `--out` is omitted. */
export function parseCaptureArgs(argv) {
  const { values } = parseArgs({
    args: argv,
    options: {
      out: { type: 'string' },
      url: { type: 'string', default: DEFAULT_CAPTURE_URL },
      renderer: { type: 'string', default: DEFAULT_RENDERER },
      scene: { type: 'string', default: DEFAULT_SCENE },
      t: { type: 'string', default: DEFAULT_TIME },
      size: { type: 'string', default: DEFAULT_SIZE },
    },
  });
  if (!values.out) return null;
  const [width, height] = parseViewportSize(values.size);
  const kinds = resolveRendererKinds(values.renderer);
  return {
    out: values.out,
    url: values.url,
    renderer: values.renderer,
    kinds,
    scene: values.scene,
    t: values.t,
    width,
    height,
  };
}
