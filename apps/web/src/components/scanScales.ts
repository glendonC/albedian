import type { CatalogReference, DisplayOrientation } from '@albedian/contracts';
import { orientStoredPoint, orientedSize } from './sourcePhotoRect.js';

/**
 * The scales behind the star scan card's pictures. Nothing here estimates a value: these functions only place
 * values a finding already carries on a log rail, so a picture never shows more than the record holds.
 */

export interface LogRail {
  min: number;
  max: number;
  ticks: readonly number[];
}

export const DISTANCE_RAIL_LY: LogRail = { min: 1, max: 10000, ticks: [1, 10, 100, 1000, 10000] };
export const LUMINOSITY_RAIL_SUN: LogRail = { min: 0.01, max: 10000, ticks: [0.01, 1, 100, 10000] };
export const TEMPERATURE_RAIL_K: LogRail = { min: 2000, max: 40000, ticks: [2000, 40000] };

/** The Sun's effective temperature, IAU 2015 Resolution B3 nominal value. */
export const SUN_TEFF_K = 5772;
export const SUN_LUMINOSITY = 1;

/** Gaia DR3 astrometry with a renormalised unit weight error above this is usually read as a poor fit. */
export const RUWE_CAUTION_ABOVE = 1.4;

/** A positive value to three significant figures, with thousands separators: 26.17 to "26.2", 1234 to "1,230". */
export function formatSignificant(value: number, digits = 3): string {
  const rounded = Number(value.toPrecision(digits));
  const decimals = Math.max(0, digits - 1 - Math.floor(Math.log10(Math.abs(rounded))));
  return rounded.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export interface RailPlacement {
  /** 0 at the rail's low end, 1 at its high end; a value past an end sits on that end and is flagged. */
  fraction: number;
  offScale: 'below' | 'above' | null;
}

export function placeOnLogRail(value: number, rail: LogRail): RailPlacement | null {
  if (!Number.isFinite(value) || value <= 0) return null;
  const raw = (Math.log10(value) - Math.log10(rail.min)) / (Math.log10(rail.max) - Math.log10(rail.min));
  if (value < rail.min) return { fraction: 0, offScale: 'below' };
  if (value > rail.max) return { fraction: 1, offScale: 'above' };
  return { fraction: Math.min(1, Math.max(0, raw)), offScale: null };
}

export interface ScanInterval {
  median: number;
  lower?: number;
  upper?: number;
}

export interface IntervalPlacement {
  median: RailPlacement;
  lower: RailPlacement | null;
  upper: RailPlacement | null;
}

export function placeIntervalOnLogRail(interval: ScanInterval, rail: LogRail): IntervalPlacement | null {
  const median = placeOnLogRail(interval.median, rail);
  if (!median) return null;
  return {
    median,
    lower: interval.lower === undefined ? null : placeOnLogRail(interval.lower, rail),
    upper: interval.upper === undefined ? null : placeOnLogRail(interval.upper, rail),
  };
}

/**
 * Blackbody colours for the temperature strip: Planck's law times the CIE 1931 2-degree observer (the Wyman, Sloan
 * and Shirley 2013 multi-lobe fit), summed from 380 to 780 nm, to linear sRGB, scaled so the brightest channel is 1,
 * then sRGB-encoded. Computed by .lanetools/blackbody.py; scanScales.test.ts recomputes them independently.
 */
export const BLACKBODY_STOPS: readonly { temperatureK: number; hex: string }[] = [
  { temperatureK: 2000, hex: '#ff8d15' },
  { temperatureK: 2500, hex: '#ffa64a' },
  { temperatureK: 3000, hex: '#ffb96e' },
  { temperatureK: 3500, hex: '#ffc88c' },
  { temperatureK: 4000, hex: '#ffd4a5' },
  { temperatureK: 5000, hex: '#ffe7d0' },
  { temperatureK: 5772, hex: '#fff1ea' },
  { temperatureK: 6000, hex: '#fff4f1' },
  { temperatureK: 7000, hex: '#f4f2ff' },
  { temperatureK: 8000, hex: '#e3e7ff' },
  { temperatureK: 10000, hex: '#cdd9ff' },
  { temperatureK: 15000, hex: '#b5c9ff' },
  { temperatureK: 20000, hex: '#abc1ff' },
  { temperatureK: 30000, hex: '#a2bbff' },
  { temperatureK: 40000, hex: '#9fb8ff' },
];

export function blackbodyGradientCss(): string {
  const stops = BLACKBODY_STOPS.map((stop) => {
    const at = placeOnLogRail(stop.temperatureK, TEMPERATURE_RAIL_K)?.fraction ?? 0;
    return `${stop.hex} ${(at * 100).toFixed(2)}%`;
  });
  return `linear-gradient(to right, ${stops.join(', ')})`;
}

export interface ScanAstrophysics {
  temperatureK: ScanInterval | null;
  luminositySolar: ScanInterval | null;
  ruwe: number | null;
}

const positive = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0;

function intervalOf(value: unknown): ScanInterval | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  if (!positive(v.median)) return null;
  return {
    median: v.median,
    ...(positive(v.lower) ? { lower: v.lower } : {}),
    ...(positive(v.upper) ? { upper: v.upper } : {}),
  };
}

/**
 * The astrophysics block on a catalog reference (temperature and luminosity as intervals, and RUWE), read with a
 * runtime check. The block arrives with the solver lane's contract unit; until then, and for any part that is not a
 * positive finite value, the card draws nothing for it.
 */
export function astrophysicsOf(catalogRef: CatalogReference | undefined): ScanAstrophysics | null {
  const block = (catalogRef as (CatalogReference & { astrophysics?: unknown }) | undefined)?.astrophysics;
  if (!block || typeof block !== 'object') return null;
  const b = block as Record<string, unknown>;
  const out: ScanAstrophysics = {
    temperatureK: intervalOf(b.temperatureK),
    luminositySolar: intervalOf(b.luminositySolar),
    ruwe: positive(b.ruwe) ? b.ruwe : null,
  };
  return out.temperatureK || out.luminositySolar || out.ruwe !== null ? out : null;
}

/**
 * Where to put the shown photo inside a square crop box so the detection sits at the box's centre, with
 * `sourcePxAcross` source pixels filling `boxPx`. The detector reports centroids with pixel centres at whole numbers,
 * so the image position is the centroid plus 0.5; the photo is shown upright, so the point is turned by the asset's
 * display orientation (mirror, then quarter turns) and the size is the upright size.
 */
export function cropPlacement(
  centroid: { xPx: number; yPx: number },
  stored: { width: number; height: number },
  orientation: DisplayOrientation | undefined,
  boxPx: number,
  sourcePxAcross: number
): { left: number; top: number; width: number; height: number } | null {
  if (![centroid.xPx, centroid.yPx].every(Number.isFinite)) return null;
  if (!positive(stored.width) || !positive(stored.height) || !positive(boxPx) || !positive(sourcePxAcross)) return null;
  const upright = orientedSize(stored.width, stored.height, orientation);
  const shown = orientStoredPoint(centroid.xPx + 0.5, centroid.yPx + 0.5, stored.width, stored.height, orientation);
  const scale = boxPx / sourcePxAcross;
  return {
    left: -(shown.xPx * scale - boxPx / 2),
    top: -(shown.yPx * scale - boxPx / 2),
    width: upright.widthPx * scale,
    height: upright.heightPx * scale,
  };
}

/** The contract's distance methods, named for people, with the words for their range. */
export function distanceMethodLabel(method: string): { name: string; range: string } {
  switch (method) {
    case 'photogeometric_bailer_jones_edr3':
      return { name: 'Bailer-Jones 2021 photogeometric', range: '16th to 84th percentile' };
    case 'geometric_parallax':
      return { name: 'Inverse parallax', range: 'range from the parallax error' };
    case 'ephemeris_au':
      return { name: 'Ephemeris', range: "range from the model's error bounds" };
    default:
      return { name: method, range: 'range as given' };
  }
}

/** The note shown when a value sits past a rail's end, or null when everything is on the rail. */
export function offScaleNote(placement: IntervalPlacement, rail: LogRail, unit: string): string | null {
  const parts = [placement.median, placement.lower, placement.upper];
  const end = (side: 'below' | 'above') => `${formatRailEnd(side === 'below' ? rail.min : rail.max)}${unit}`;
  if (placement.median.offScale) {
    return placement.median.offScale === 'above'
      ? `Past the ${end('above')} end of this scale`
      : `Below the ${end('below')} end of this scale`;
  }
  if (parts.some((p) => p?.offScale === 'above')) return `The range runs past the ${end('above')} end of this scale`;
  if (parts.some((p) => p?.offScale === 'below')) return `The range runs below the ${end('below')} end of this scale`;
  return null;
}

const formatRailEnd = (value: number) => (value < 1 ? String(value) : value.toLocaleString('en-US', { maximumFractionDigits: 0 }));
