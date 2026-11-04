/**
 * SYNTHETIC PERF FIXTURE. NOT A STAR CATALOG.
 *
 * Seeded pseudo-random star positions, apparent magnitudes and effective temperatures used
 * only to load the GPU with a realistic number of HDR point sprites (about 100k). The
 * distributions are loosely shaped like an all-sky sample (counts rising with magnitude,
 * temperatures clustered around G and K types) so the HDR range and color spread are
 * representative, but no row corresponds to a real star. Never label this as data.
 * A production starfield would read Gaia DR3 / HIP rows instead.
 */

export const SYNTHETIC_STAR_FIXTURE_LABEL =
  'Synthetic perf fixture: seeded pseudo-random star positions, magnitudes and temperatures (not a catalog)';

export interface StarFixtureOptions {
  count?: number;
  seed?: number;
  /** Brightest apparent magnitude drawn. */
  magBright?: number;
  /** Faintest apparent magnitude drawn. */
  magFaint?: number;
  /** log10 slope of differential star counts per magnitude. */
  countSlope?: number;
  minRadius?: number;
  maxRadius?: number;
}

export interface StarFixture {
  label: typeof SYNTHETIC_STAR_FIXTURE_LABEL;
  synthetic: true;
  seed: number;
  count: number;
  /** xyz per star, scene units. */
  positions: Float32Array;
  /** Apparent magnitude per star. */
  magnitudes: Float32Array;
  /** Effective temperature per star, kelvin. */
  temperaturesK: Float32Array;
}

/** mulberry32: small, fast, deterministic 32-bit PRNG returning floats in [0, 1). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Temperature classes for the fixture: [weight, minK, maxK]. Weights are a rough stand-in for
// a magnitude-limited sample, chosen for color variety, not taken from a survey.
const TEMPERATURE_CLASSES: ReadonlyArray<readonly [number, number, number]> = [
  [0.04, 10000, 30000], // O and B
  [0.14, 7500, 10000], // A
  [0.2, 6000, 7500], // F
  [0.24, 5200, 6000], // G
  [0.3, 3700, 5200], // K
  [0.08, 2400, 3700], // M
];

function drawTemperature(rand: () => number): number {
  let pick = rand();
  for (const [weight, lo, hi] of TEMPERATURE_CLASSES) {
    if (pick < weight) {
      return Math.exp(Math.log(lo) + rand() * (Math.log(hi) - Math.log(lo)));
    }
    pick -= weight;
  }
  return 5000;
}

/**
 * Inverse-CDF sample of a truncated distribution with dN/dm proportional to 10^(slope * m).
 */
function drawMagnitude(rand: () => number, bright: number, faint: number, slope: number): number {
  const a = Math.pow(10, slope * bright);
  const b = Math.pow(10, slope * faint);
  return Math.log10(a + rand() * (b - a)) / slope;
}

export function makeSyntheticStarFixture(options: StarFixtureOptions = {}): StarFixture {
  const count = Math.max(0, Math.floor(options.count ?? 100_000));
  const seed = options.seed ?? 0x0a1bed1a;
  const magBright = options.magBright ?? -1.5;
  const magFaint = options.magFaint ?? 9.0;
  const countSlope = options.countSlope ?? 0.45;
  const minRadius = options.minRadius ?? 60;
  const maxRadius = options.maxRadius ?? 900;

  const rand = mulberry32(seed);
  const positions = new Float32Array(count * 3);
  const magnitudes = new Float32Array(count);
  const temperaturesK = new Float32Array(count);

  for (let i = 0; i < count; i++) {
    // Uniform direction on the sphere.
    const zDir = 2 * rand() - 1;
    const phi = 2 * Math.PI * rand();
    const ring = Math.sqrt(Math.max(0, 1 - zDir * zDir));
    // Radius uniform in volume within the shell, so parallax is visible while orbiting.
    const r0 = minRadius ** 3;
    const r1 = maxRadius ** 3;
    const r = Math.cbrt(r0 + rand() * (r1 - r0));
    positions[i * 3] = r * ring * Math.cos(phi);
    positions[i * 3 + 1] = r * zDir;
    positions[i * 3 + 2] = r * ring * Math.sin(phi);
    magnitudes[i] = drawMagnitude(rand, magBright, magFaint, countSlope);
    temperaturesK[i] = drawTemperature(rand);
  }

  return {
    label: SYNTHETIC_STAR_FIXTURE_LABEL,
    synthetic: true,
    seed,
    count,
    positions,
    magnitudes,
    temperaturesK,
  };
}
