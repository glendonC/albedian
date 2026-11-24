import { realTravelYears } from '@albedian/camera-rig';
import { HANGAR_CRAFTS, type Craft, type CraftId } from './crafts.js';

/**
 * D26 (decided 19 Nov, UIUX-pilot-READY.md): the words and numbers of the craft picker and of the real trip under the
 * flight's two distances, every word as the record writes it. The trip is the star's catalog distance from Earth over
 * the craft's NASA-sourced speed; the pace is that over the flight's own measured travel time.
 */

/**
 * D35 item 2: where a craft that never travels between stars flies, by the hangar's entry (D26's "near Earth"); the six
 * more craft as the design lane ruled at 17:24 (UIUX-hangarpick-READY.md), each from its NASA facts in the hangar.
 */
const WHERE_IT_FLIES: Readonly<Record<CraftId, string>> = {
  jwst: 'near Earth',
  hubble: 'near Earth',
  iss: 'near Earth',
  shuttle: 'near Earth',
  gemini: 'near Earth',
  'apollo-lm': 'to the Moon',
  // Its 194.4 km/s at closest approach is a speed around the Sun, never toward a star (D35 item 5; ASSETS keeps it as a fact)
  'parker-solar-probe': 'around the Sun',
  juno: 'around Jupiter',
  dawn: 'to Vesta and Ceres',
  messenger: 'to Mercury',
  spitzer: 'around the Sun',
  'osiris-rex': 'to Bennu',
  mro: 'around Mars',
  // The 18:00 ruling (hangar-3), from the hangar's NASA facts
  stardust: 'to comet Wild 2',
  'deep-space-1': 'to comet Borrelly',
  'mars-global-surveyor': 'around Mars',
  'near-shoemaker': 'to Eros',
  maven: 'around Mars',
  // The 18:05 ruling (cassini-1)
  cassini: 'around Saturn',
  // The 18:35 ruling (hangar-4)
  ibex: 'near Earth',
  'van-allen-probes': 'near Earth',
  // The 21 Nov 12:40 ruling (hangar-5)
  'deep-impact': 'to comet Tempel 1',
  'viking-lander': 'on Mars',
  // The 21 Nov 14:33 ruling, decided 14:38 (as-1)
  'apollo-soyuz': 'near Earth',
  // The 22 Nov 05:53 ruling (ky-1): Spitzer's orbit kind, Earth-trailing around the Sun
  kepler: 'around the Sun',
  // The 22 Nov 22:18 ruling (in-1): as the Viking Lander's
  ingenuity: 'on Mars',
  // The 23 Nov 03:38 ruling (pv-1): as the Viking Lander's and Ingenuity's
  perseverance: 'on Mars',
  // The 23 Nov 11:07 ruling (cl-1): as the Apollo Lunar Module's
  clementine: 'to the Moon',
  // The 23 Nov 11:37 ruling (td-1): as IBEX's, the Van Allen Probes' and Webb's
  tdrs: 'near Earth',
  // The 24 Nov 05:48 ruling (mo-1): as MRO's, Mars Global Surveyor's and MAVEN's
  'mars-odyssey': 'around Mars',
};

/**
 * The fact a craft's words wait for, by entry (the 18:35 ruling: IBEX's place and sentence show once its record names its
 * orbit, from its NASA page; until then only its name shows). The 21 Nov 12:40 ruling holds Deep Impact's until its
 * record names Tempel 1 and the Viking Lander's until it names the landing on Mars, each from NASA's mission page. The
 * 21 Nov 14:33 ruling holds Apollo-Soyuz's until its record names the docking in Earth orbit (NASA's project page), and
 * the 22 Nov 05:53 ruling Kepler's until it names its Earth-trailing orbit (NSSDC). The 22 Nov 22:18 ruling holds
 * Ingenuity's until it names its last flight (NASA's mission page), the 23 Nov 03:38 ruling holds Perseverance's
 * until it names its landing on Mars (NSSDC 2020-052A), the 23 Nov 11:07 ruling holds Clementine's until it names
 * its lunar orbit (NASA's mission page), and the 23 Nov 11:37 ruling holds TDRS's until it names its geosynchronous
 * orbit (NASA's TDRS mission page).
 */
const NEEDS_FACT: Readonly<Record<CraftId, string>> = {
  ibex: 'orbit',
  'deep-impact': 'tempel1Impact',
  'viking-lander': 'landedOnMars',
  'apollo-soyuz': 'orbit',
  kepler: 'orbit',
  ingenuity: 'lastFlight',
  perseverance: 'landedOnMars',
  clementine: 'lunarOrbit',
  tdrs: 'orbit',
  'mars-odyssey': 'marsOrbit',
};

/** The fact this craft's words still wait for in the hangar's record, or none. */
export function awaitingFact(craft: Craft): string | null {
  const key = NEEDS_FACT[craft.id];
  return key && !craft.factKeys.includes(key) ? key : null;
}

/** D35 item 5 and the 17:24 ruling: each named craft's sentence in place of a trip time, word for word. */
const NO_TRAVEL_SENTENCES: Readonly<Record<CraftId, string>> = {
  jwst: 'No real travel time: Webb stays near Earth, at the Sun-Earth L2 point.',
  'apollo-lm': 'No real travel time: the Lunar Module flew between Earth and the Moon.',
  'parker-solar-probe': 'No real travel time: Parker Solar Probe orbits the Sun; 194 km/s is its speed at its closest pass.',
  juno: 'No real travel time: Juno orbits Jupiter.',
  dawn: 'No real travel time: Dawn flew to Vesta and then Ceres, within the Solar System.',
  messenger: 'No real travel time: MESSENGER flew to Mercury and ended on its surface in 2015.',
  spitzer: 'No real travel time: Spitzer orbits the Sun, trailing Earth.',
  'osiris-rex': 'No real travel time: OSIRIS-REx flew to the asteroid Bennu, within the Solar System.',
  mro: 'No real travel time: the Mars Reconnaissance Orbiter orbits Mars.',
  stardust: 'No real travel time: Stardust flew to comet Wild 2, within the Solar System.',
  'deep-space-1': 'No real travel time: Deep Space 1 flew to comet Borrelly, within the Solar System.',
  'mars-global-surveyor': 'No real travel time: Mars Global Surveyor orbited Mars until contact was lost in 2006.',
  'near-shoemaker': 'No real travel time: NEAR Shoemaker flew to the asteroid Eros and landed on it in 2001.',
  maven: 'No real travel time: MAVEN orbits Mars.',
  cassini: 'No real travel time: Cassini orbited Saturn until its mission ended in 2017.',
  ibex: 'No real travel time: IBEX stays near Earth.',
  'van-allen-probes': "No real travel time: the Van Allen Probes studied Earth's radiation belts, near Earth.",
  'deep-impact': 'No real travel time: Deep Impact flew to comet Tempel 1 and then comet Hartley 2, within the Solar System.',
  'viking-lander': "No real travel time: Viking 1's lander stood on Mars until contact was lost in 1982.",
  'apollo-soyuz': 'No real travel time: the Apollo and Soyuz spacecraft docked in Earth orbit in 1975.',
  kepler: 'No real travel time: Kepler orbits the Sun, trailing Earth.',
  ingenuity: 'No real travel time: Ingenuity flew on Mars from 2021 to 2024.',
  perseverance: 'No real travel time: Perseverance landed in Jezero Crater on Mars in 2021.',
  clementine: 'No real travel time: Clementine orbited the Moon in 1994 and then entered orbit around the Sun.',
  tdrs: 'No real travel time: TDRS orbits Earth in geosynchronous orbit, relaying data for space missions.',
  'mars-odyssey': 'No real travel time: Mars Odyssey has orbited Mars since 2001, relaying data from Mars landers.',
};

/** D26 item 5 and D35 item 5: a craft or suit that never travels between stars shows its sentence instead of a number. */
export function noTravelSentence(craft: Craft): string | null {
  if (craft.speed || awaitingFact(craft)) return null;
  if (craft.kind === 'suit') return 'No real travel time: a spacesuit is worn near a spacecraft, never flown between stars.';
  if (NO_TRAVEL_SENTENCES[craft.id]) return NO_TRAVEL_SENTENCES[craft.id]!;
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

/** D35 item 2: the speed to three figures; else where it flies (none while its words wait for a fact); a suit, nothing. */
export function craftDetail(craft: Craft): string {
  if (craft.speed) return speedText(craft.speed.kmS);
  return awaitingFact(craft) ? '' : (WHERE_IT_FLIES[craft.id] ?? '');
}

function choiceOf(craft: Craft): CraftChoice {
  return { id: craft.id, name: craft.name, detail: craftDetail(craft), pictureUrl: craft.pictureUrl };
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
