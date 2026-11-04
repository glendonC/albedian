export interface ImageDiff {
  /** Largest per-channel difference, in 8-bit display code values. */
  max: number;
  /** Mean per-channel absolute difference over all pixels, in code values. */
  mean: number;
  /** Fraction of pixels whose largest channel difference is at least 2 code values. */
  over2: number;
  /** Fraction of pixels whose largest channel difference is at least 8 code values. */
  over8: number;
}

/** Compares two RGBA8 images of the same size (alpha ignored). */
export function diffImages(a: Uint8ClampedArray, b: Uint8ClampedArray): ImageDiff {
  if (a.length !== b.length) throw new Error('diffImages: size mismatch');
  let max = 0;
  let sum = 0;
  let over2 = 0;
  let over8 = 0;
  const pixels = a.length / 4;
  for (let i = 0; i < a.length; i += 4) {
    const dr = Math.abs(a[i]! - b[i]!);
    const dg = Math.abs(a[i + 1]! - b[i + 1]!);
    const db = Math.abs(a[i + 2]! - b[i + 2]!);
    const m = Math.max(dr, dg, db);
    max = Math.max(max, m);
    sum += dr + dg + db;
    if (m >= 2) over2++;
    if (m >= 8) over8++;
  }
  return {
    max,
    mean: pixels ? sum / (pixels * 3) : 0,
    over2: pixels ? over2 / pixels : 0,
    over8: pixels ? over8 / pixels : 0,
  };
}

/** Reads a WebGPU or WebGL canvas right after a frame was rendered into it (same task). */
export function readCanvas(source: HTMLCanvasElement, scratch: HTMLCanvasElement): Uint8ClampedArray {
  if (scratch.width !== source.width || scratch.height !== source.height) {
    scratch.width = source.width;
    scratch.height = source.height;
  }
  const ctx = scratch.getContext('2d', { willReadFrequently: true });
  if (!ctx) throw new Error('readCanvas: no 2D context');
  ctx.clearRect(0, 0, scratch.width, scratch.height);
  ctx.drawImage(source, 0, 0);
  return ctx.getImageData(0, 0, scratch.width, scratch.height).data;
}
