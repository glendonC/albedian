import { realTravelYears } from '@albedian/camera-rig';
import { HANGAR_CRAFTS, type Craft, type CraftId } from './crafts.js';

/**
 * D26 (decided 19 Nov, UIUX-pilot-READY.md): the words and numbers of the craft picker and of the real trip under the
 * flight's two distances, every word as the record writes it. The trip is the star's catalog distance from Earth over
 * the craft's NASA-sourced speed; the pace is that over the flight's own measured travel time.
 */

/** D35 item 2: where a craft that never travels between stars flies, by the hangar's entry (D26's "near Earth"). */
const WHERE_IT_FLIES: Readonly<Record<CraftId, 'near Earth' | 'to the Moon' | 'around the Sun'>> = {
  jwst: 'near Earth',
  hubble: 'near Earth',
  iss: 'near Earth',
  shuttle: 'near Earth',
  gemini: 'near Earth',
  'apollo-lm': 'to the Moon',
  // Its 194.4 km/s at closest approach is a speed around the Sun, never toward a star (D35 item 5; ASSETS keeps it as a fact)
  'parker-solar-probe': 'around the Sun',
};

/** D26 item 5 and D35 item 5: a craft or suit that never travels between stars shows its sentence instead of a number. */
export function noTravelSentence(craft: Craft): string | null {
  if (craft.speed) return null;
  if (craft.kind === 'suit') return 'No real travel time: a spacesuit is worn near a spacecraft, never flown between stars.';
  if (craft.id === 'jwst') return 'No real travel time: Webb stays near Earth, at the Sun-Earth L2 point.';
  if (WHERE_IT_FLIES[craft.id] === 'to the Moon') return 'No real travel time: the Lunar Module flew between Earth and the Moon.';
  if (WHERE_IT_FLIES[craft.id] === 'around the Sun') return 'No real travel time: Parker Solar Probe orbits the Sun; 194 km/s is its speed at its closest pass.';
  if (WHERE_IT_FLIES[craft.id] === 'near Earth') return `No real travel time: ${craft.name} stays near Earth.`;
  return null;
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

const THREE = new Intl.NumberFormat('en-US', { minimumSignificantDigits: 3, maximumSignificantDigits: 3 });

/** A count of years to three figures, with "million" and "billion" in words. */
export function threeFigureYears(years: number): { value: string; unit: 'year' | 'years' } {
  // Round to three figures first, so 999,600 reads "1.00 million"
  const rounded = Number(years.toPrecision(3));
  const unit = rounded === 1 ? 'year' : 'years';
  if (rounded >= 1e9) return { value: `${THREE.format(rounded / 1e9)} billion`, unit };
  if (rounded >= 1e6) return { value: `${THREE.format(rounded / 1e6)} million`, unit };
  return { value: THREE.format(rounded), unit };
}

function speedText(kmS: number): string {
  return `${kmS.toFixed(1)} km/s`;
}

function monthOf(isoDate: string | null): string | null {
  if (!isoDate) return null;
  const [year, month] = isoDate.split('-');
  return `${MONTHS[Number(month) - 1] ?? ''} ${year}`;
}

export interface PilotReadout {
  readonly name: string;
  /** The real trip's number and its unit; none for a craft that never travels between stars. */
  readonly trip: { value: string; unit: 'year' | 'years' } | null;
  /** The lines under the trip, in order. */
  readonly lines: readonly string[];
  /** Item 5's sentence for a craft with no real travel time. */
  readonly none: string | null;
  /** The speed's source line (the last of `lines`); none without a speed. On the phone it sits in the details (D30 item 5). */
  readonly source: string | null;
}

/**
 * The readout for `craft` flying to the star named `starName`, `starDistancePc` from Earth; `flightSeconds` is how long
 * the flight's camera travelled on screen, measured, none when it did not travel (reduced motion).
 */
export function pilotReadout(craft: Craft, starName: string, starDistancePc: number, flightSeconds: number | null): PilotReadout {
  const name = craft.name;
  const years = craft.speed ? realTravelYears(starDistancePc, craft.speed.kmS) : null;
  if (!craft.speed || years === null) return { name, trip: null, lines: [], none: noTravelSentence(craft), source: null };
  const speed = speedText(craft.speed.kmS);
  const lines = [`to ${starName} at its real speed, ${speed}`];
  if (flightSeconds !== null && flightSeconds > 0) {
    lines.push(`Shown here in ${flightSeconds.toFixed(1)} s: about ${threeFigureYears(years / flightSeconds).value} years each second.`);
  }
  const source = craftSpeedSource(craft)!;
  lines.push(source);
  return { name, trip: threeFigureYears(years), lines, none: null, source };
}

/** Where the craft's speed comes from (D26 item 4), none for a craft with no speed between stars. */
export function craftSpeedSource(craft: Craft): string | null {
  if (!craft.speed) return null;
  const month = monthOf(craft.speed.asOf);
  // Where NASA's page gives no date for the figure, none is written
  return `Speed: NASA, ${speedText(craft.speed.kmS)} relative to the Sun${month ? ` (${month})` : ''}.`;
}

export interface CraftChoice {
  readonly id: CraftId | null;
  readonly name: string;
  readonly detail: string;
  /** The hangar's picture of the model, for the picker's row; none for your view. */
  readonly pictureUrl: string | null;
}

/** D35: the picker's rows under the hangar's two kinds. */
export interface CraftGroup {
  readonly heading: 'Spacecraft' | 'Spacesuits';
  readonly choices: readonly CraftChoice[];
}

function choiceOf(craft: Craft): CraftChoice {
  // D35 item 2: the speed to three figures; else where it flies; a suit, nothing
  return { id: craft.id, name: craft.name, detail: craft.speed ? speedText(craft.speed.kmS) : (WHERE_IT_FLIES[craft.id] ?? ''), pictureUrl: craft.pictureUrl };
}

/**
 * D35 item 1: under "Spacecraft" the craft leaving the Solar System, fastest first, then the other craft by name; under
 * "Spacesuits" the suits in the hangar's own order.
 */
export function craftGroups(crafts: readonly Craft[]): CraftGroup[] {
  const leaving = crafts.filter((c) => c.kind === 'craft' && c.speed).sort((a, b) => b.speed!.kmS - a.speed!.kmS);
  const others = crafts.filter((c) => c.kind === 'craft' && !c.speed).sort((a, b) => a.name.localeCompare(b.name, 'en'));
  const suits = crafts.filter((c) => c.kind === 'suit');
  const groups: CraftGroup[] = [
    { heading: 'Spacecraft', choices: [...leaving, ...others].map(choiceOf) },
    { heading: 'Spacesuits', choices: suits.map(choiceOf) },
  ];
  return groups.filter((g) => g.choices.length > 0);
}

/** D26's first row. */
export const YOUR_VIEW_CHOICE: CraftChoice = { id: null, name: 'Your view', detail: 'no craft', pictureUrl: null };

export const CRAFT_GROUPS: readonly CraftGroup[] = craftGroups(HANGAR_CRAFTS);

/** Every row, your view first, in the picker's order. */
export const CRAFT_CHOICES: readonly CraftChoice[] = [YOUR_VIEW_CHOICE, ...CRAFT_GROUPS.flatMap((g) => g.choices)];

/** The picker's foot line. */
export const CRAFT_PICKER_FOOT = "NASA's own 3D models. Drawn by the app, not measured.";

/**
 * How long the flight took on screen, measured: from the first frame of Go there's flight (either half) to arrival.
 * A flight that arrives without a flight on screen (reduced motion) has none, so no pace is claimed.
 */
export class FlightClock {
  private startedMs: number | null = null;
  private secondsValue: number | null = null;

  /** Feed each phase the session shows, with the time it was seen. */
  observe(phase: string | undefined, nowMs: number): void {
    if (phase === 'phase_a_photo_to_sky' || phase === 'phase_b_sky_to_space') {
      if (this.startedMs === null) this.startedMs = nowMs;
    } else if (phase === 'at_space') {
      if (this.startedMs !== null) this.secondsValue = (nowMs - this.startedMs) / 1000;
      this.startedMs = null;
    } else {
      this.startedMs = null;
    }
  }

  get seconds(): number | null {
    return this.secondsValue;
  }
}
