import { realTravelYears } from '@albedian/camera-rig';
import { CRAFTS, type Craft, type CraftId } from './crafts.js';

/**
 * D26 (decided 19 Nov, UIUX-pilot-READY.md): the words and numbers of the craft picker and of the real trip under the
 * flight's two distances, every word as the record writes it. The trip is the star's catalog distance from Earth over
 * the craft's NASA-sourced speed; the pace is that over the flight's own measured travel time.
 */

/** The craft's name on the picker and over the trip. */
const CRAFT_NAMES: Readonly<Record<CraftId, string>> = {
  'voyager-1': 'Voyager 1',
  jwst: 'James Webb Space Telescope',
  astronaut: 'Astronaut',
};

/** Item 5: a craft that never travels between stars shows these instead of a number. */
const NO_TRAVEL: Readonly<Partial<Record<CraftId, string>>> = {
  jwst: 'No real travel time: Webb stays near Earth, at the Sun-Earth L2 point.',
  astronaut: 'No real travel time: a spacesuit is worn near a spacecraft, never flown between stars.',
};

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

function monthOf(isoDate: string): string {
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
  const name = CRAFT_NAMES[craft.id];
  const years = craft.speed ? realTravelYears(starDistancePc, craft.speed.kmS) : null;
  if (!craft.speed || years === null) return { name, trip: null, lines: [], none: NO_TRAVEL[craft.id] ?? null, source: null };
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
  return craft.speed ? `Speed: NASA, ${speedText(craft.speed.kmS)} relative to the Sun (${monthOf(craft.speed.asOf)}).` : null;
}

export interface CraftChoice {
  readonly id: CraftId | null;
  readonly name: string;
  readonly detail: string;
  /** NASA's own picture of the model, for the picker's row; none for your view. */
  readonly pictureUrl: string | null;
}

/** Item 3: the picker's rows, your view first. */
export const CRAFT_CHOICES: readonly CraftChoice[] = [
  { id: null, name: 'Your view', detail: 'no craft', pictureUrl: null },
  ...(['voyager-1', 'jwst', 'astronaut'] as const).map((id) => ({
    id,
    name: CRAFT_NAMES[id],
    detail: CRAFTS[id].speed ? speedText(CRAFTS[id].speed!.kmS) : 'near Earth',
    pictureUrl: CRAFTS[id].modelUrl.replace(/\.glb$/, '.png'),
  })),
];

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
