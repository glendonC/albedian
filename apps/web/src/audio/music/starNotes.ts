/**
 * The data layer of the music (AUDIO_ENGINE.md section 13): the catalog stars on screen play notes
 * inside the composed harmony. Pure.
 *
 * Each star with a Gaia DR3 temperature, luminosity and distance plays one note:
 * - its pitch is a tone of the chord sounding then, taken from the chord's tones in the instrument's
 *   register by the star's place on a log scale of temperature (cooler lower, hotter higher);
 * - its gain follows its received bolometric flux (section 4.2's 4 dB per magnitude), halved to
 *   2 dB per magnitude so faint stars stay audible in a mix, between a floor and 0 dB;
 * - its place in the sweep is its place across the screen, left to right;
 * - its pan is its horizontal place on screen.
 * A star missing any of the three values plays nothing. Nothing here is estimated or filled in.
 */

import { receivedLevelDb } from '../mappings.js';
import { conjugateQuat, type SkyView } from '../skySources.js';
import { rotateByQuat, type AudioSourceInput, type Vec3 } from '../voicePlanning.js';
import { chordTonesInRange, type PitchSet } from './harmony.js';

/** A star as the music sees it: its catalog values and its place on screen. */
export interface ScreenStar {
  readonly id: string;
  readonly teffK: number | null;
  readonly luminositySolar: number | null;
  readonly distancePc: number | null;
  /** -1 at the left edge of the screen, 1 at the right. */
  readonly screenX: number;
  /** -1 at the bottom edge, 1 at the top. */
  readonly screenY: number;
}

export interface StarNoteMapping {
  /** Temperatures at the bottom and the top of the register (log scale); outside them, clamped. */
  readonly teffLowK: number;
  readonly teffHighK: number;
  /** The instrument's register for data notes, MIDI. */
  readonly lowMidi: number;
  readonly highMidi: number;
  /** Received level (dBFS, section 4.2) that plays at 0 dB of gain. */
  readonly levelRefDb: number;
  /** dB of gain per dB of received level: 0.5 turns 4 dB per magnitude into 2. */
  readonly dbPerDb: number;
  /** The quietest a data note plays, dB. */
  readonly floorDb: number;
  /** Pan at the screen's edges (-1 to 1). */
  readonly panWidth: number;
}

/** Design choices, stated in section 13; each direction sets its own register. */
export const DEFAULT_STAR_NOTE_MAPPING: StarNoteMapping = {
  teffLowK: 3000,
  teffHighK: 30000,
  lowMidi: 60,
  highMidi: 84,
  levelRefDb: -27,
  dbPerDb: 0.5,
  floorDb: -12,
  panWidth: 0.7,
};

export interface StarNote {
  readonly starId: string;
  readonly midi: number;
  readonly gainDb: number;
  readonly pan: number;
  /** The chord (or pitch set) the note was taken from. */
  readonly chord: string;
  readonly teffK: number;
  readonly luminositySolar: number;
  readonly distancePc: number;
  /** Section 4.2's received level, dBFS; -Infinity below its floor. */
  readonly receivedLevelDb: number;
  /** The star's place on the temperature scale, 0 to 1. */
  readonly temperaturePosition: number;
  readonly screenX: number;
  readonly screenY: number;
}

const finite = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);

/** Where a direction falls on screen; null behind the camera. Off-screen points lie beyond +/-1. */
export function screenPosition(direction: Vec3, view: SkyView): { x: number; y: number } | null {
  const [x, y, z] = rotateByQuat(direction, conjugateQuat(view.orientationIcrs));
  const depth = -z;
  if (!(depth > 0)) return null;
  return { x: x / (depth * view.tanHalfFovX) + 0, y: y / (depth * view.tanHalfFovY) + 0 };
}

/** The sources that lie on screen, with their places; values pass through as the catalog gave them. */
export function screenStars(sources: readonly AudioSourceInput[], view: SkyView): ScreenStar[] {
  const out: ScreenStar[] = [];
  for (const source of sources) {
    const d = source.directionWorld;
    if (!d) continue;
    const p = screenPosition(d, view);
    if (!p || Math.abs(p.x) > 1 || Math.abs(p.y) > 1) continue;
    out.push({
      id: source.id,
      teffK: source.teffK ?? null,
      luminositySolar: source.luminositySolar ?? null,
      distancePc: source.distancePc ?? null,
      screenX: p.x,
      screenY: p.y,
    });
  }
  return out;
}

/** 0 at `teffLowK`, 1 at `teffHighK`, on a log scale, clamped. */
export function temperaturePosition(teffK: number, mapping: StarNoteMapping): number {
  const u = Math.log(teffK / mapping.teffLowK) / Math.log(mapping.teffHighK / mapping.teffLowK);
  return Math.min(1, Math.max(0, u));
}

function levelOf(star: ScreenStar): number | null {
  if (!finite(star.teffK) || !finite(star.luminositySolar) || !finite(star.distancePc)) return null;
  return receivedLevelDb(star.luminositySolar, star.distancePc);
}

/** The note a star plays in a chord; null when a value is missing or the register holds no chord tone. */
export function starNote(star: ScreenStar, chord: PitchSet, mapping: StarNoteMapping): StarNote | null {
  const level = levelOf(star);
  if (level === null) return null;
  const tones = chordTonesInRange(chord, mapping.lowMidi, mapping.highMidi);
  if (tones.length === 0) return null;
  const u = temperaturePosition(star.teffK!, mapping);
  // Each tone takes an equal share of the log-temperature scale, so the note never falls as Teff rises
  const midi = tones[Math.min(tones.length - 1, Math.floor(u * tones.length))]!;
  const gainDb = Math.max(mapping.floorDb, Math.min(0, mapping.dbPerDb * (level - mapping.levelRefDb)));
  return {
    starId: star.id,
    midi,
    gainDb: Number.isFinite(gainDb) ? gainDb : mapping.floorDb,
    pan: Math.max(-1, Math.min(1, star.screenX)) * mapping.panWidth,
    chord: chord.name,
    teffK: star.teffK!,
    luminositySolar: star.luminositySolar!,
    distancePc: star.distancePc!,
    receivedLevelDb: level,
    temperaturePosition: u,
    screenX: star.screenX,
    screenY: star.screenY,
  };
}

const byId = (a: ScreenStar, b: ScreenStar) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** The `count` stars with all three values and the highest received level (ties by id). */
export function loudestStars(stars: readonly ScreenStar[], count: number): ScreenStar[] {
  const complete = stars.flatMap((s) => {
    const level = levelOf(s);
    return level === null ? [] : [{ s, level }];
  });
  complete.sort((a, b) => (a.level !== b.level ? (a.level > b.level ? -1 : 1) : byId(a.s, b.s)));
  return complete.slice(0, Math.max(0, Math.floor(count))).map((c) => c.s);
}

/** Left to right across the screen; a column from the top down; then by id. */
export function sweepOrder(stars: readonly ScreenStar[]): ScreenStar[] {
  return [...stars].sort((a, b) => a.screenX - b.screenX || b.screenY - a.screenY || byId(a, b));
}

/** The loudest `count` complete stars as notes of `chord`, in sweep order. */
export function starSweep(stars: readonly ScreenStar[], chord: PitchSet, mapping: StarNoteMapping, count: number): StarNote[] {
  return sweepOrder(loudestStars(stars, count)).flatMap((s) => {
    const note = starNote(s, chord, mapping);
    return note ? [note] : [];
  });
}
