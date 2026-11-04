/**
 * Display tint for a catalog star from its colour index.
 *
 * This is a visual mapping for rendering, not photometry: anchors keep the palette the sky
 * used before (hot blue-white through red), interpolated over Gaia BP-RP. Hipparcos entries
 * carry Johnson B-V instead; they are placed on the same scale with BP-RP ~ 1.27 (B-V),
 * a rough slope between the Sun (0.65, 0.82) and an M0 dwarf (1.43, 1.84), which is
 * adequate for a tint and nothing more.
 */

const ANCHORS: readonly [bpRp: number, hex: number][] = [
  [-0.3, 0x9bb8ff],
  [0.0, 0xcad8ff],
  [0.3, 0xf4f6ff],
  [0.65, 0xfff4ea],
  [1.1, 0xffd6a5],
  [1.8, 0xff9e6d],
];

/** Tint for colour-less catalog entries */
const NEUTRAL = 0xf4f6ff;

export const B_MINUS_V_TO_BP_RP = 1.27;

function channels(hex: number): [number, number, number] {
  return [((hex >> 16) & 0xff) / 255, ((hex >> 8) & 0xff) / 255, (hex & 0xff) / 255];
}

/** sRGB tint in [0, 1] per channel. */
export function catalogStarTint(colorIndex: number, isBMinusV: boolean): [number, number, number] {
  if (!Number.isFinite(colorIndex)) return channels(NEUTRAL);
  const bpRp = isBMinusV ? colorIndex * B_MINUS_V_TO_BP_RP : colorIndex;
  const first = ANCHORS[0];
  const last = ANCHORS[ANCHORS.length - 1];
  if (!first || !last) return channels(NEUTRAL);
  if (bpRp <= first[0]) return channels(first[1]);
  if (bpRp >= last[0]) return channels(last[1]);
  for (let i = 1; i < ANCHORS.length; i++) {
    const hi = ANCHORS[i];
    const lo = ANCHORS[i - 1];
    if (!hi || !lo || bpRp > hi[0]) continue;
    const t = (bpRp - lo[0]) / (hi[0] - lo[0]);
    const a = channels(lo[1]);
    const b = channels(hi[1]);
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  }
  return channels(last[1]);
}
