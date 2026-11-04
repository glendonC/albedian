// Ported unchanged from apps/webgpu-spike/src/bench/stats.ts, so frame times summarize the same way.
export interface Stats {
  n: number;
  mean: number;
  median: number;
  p95: number;
  p99: number;
  min: number;
  max: number;
}

function quantile(sorted: readonly number[], q: number): number {
  if (sorted.length === 0) return Number.NaN;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const w = pos - lo;
  return sorted[lo]! * (1 - w) + sorted[hi]! * w;
}

export function summarize(samples: readonly number[]): Stats {
  const sorted = [...samples].filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  const n = sorted.length;
  const mean = n === 0 ? Number.NaN : sorted.reduce((s, v) => s + v, 0) / n;
  return {
    n,
    mean,
    median: quantile(sorted, 0.5),
    p95: quantile(sorted, 0.95),
    p99: quantile(sorted, 0.99),
    min: n === 0 ? Number.NaN : sorted[0]!,
    max: n === 0 ? Number.NaN : sorted[n - 1]!,
  };
}

export function round(value: number, digits = 3): number {
  const f = 10 ** digits;
  return Math.round(value * f) / f;
}

export function roundStats(stats: Stats, digits = 3): Stats {
  return {
    n: stats.n,
    mean: round(stats.mean, digits),
    median: round(stats.median, digits),
    p95: round(stats.p95, digits),
    p99: round(stats.p99, digits),
    min: round(stats.min, digits),
    max: round(stats.max, digits),
  };
}
