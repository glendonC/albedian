/**
 * The owner's listening session (AUDIO_ENGINE.md section 10): fourteen items, each a question and
 * one or more short clips rendered by the real engine.
 *
 * Every value a clip plays is one of three kinds, and the page says which:
 * - a catalog star, named by its IAU name, whose Gaia DR3 temperature and luminosity and
 *   Bailer-Jones distance the renderer reads from the release's tiles at render time;
 * - a published value with its citation (the pulsars' ATNF values);
 * - a test value, for a channel the app has no data for yet (skyglow, uncertainty, dust),
 *   labelled 'test value, not a measurement' with its reference. None of these reaches the app.
 * Placement straight ahead, a level held equal across stars, or a star moved to another distance
 * are listening devices, stated as such in each clip's facts.
 */

export const TEST_VALUE_LABEL = 'test value, not a measurement';

/** A value that is not a catalog star's, with what it is and where it comes from. */
export interface StatedValue {
  readonly value: number;
  readonly unit: string;
  readonly kind: 'published' | 'test';
  /** TEST_VALUE_LABEL for test values; what the published value is, for published ones. */
  readonly label: string;
  readonly reference: string;
}

const test = (value: number, unit: string, reference: string): StatedValue => ({
  value,
  unit,
  kind: 'test',
  label: TEST_VALUE_LABEL,
  reference,
});

const ATNF = 'ATNF Pulsar Catalogue v2.6.1 (Manchester et al. 2005, AJ 129, 1993), as in AUDIO_ENGINE.md 4.6';
const SKYGLOW_REFERENCE =
  'Chosen for listening; the bed plays the artificial part above the natural 22.0 V mag/arcsec^2 of Falchi et al. 2016 (Science Advances 2, e1600377)';

/** Where a single voice sits around the listener (listener axes; 0 degrees straight ahead). */
export interface Placement {
  readonly azimuthDeg: number;
  readonly elevationDeg: number;
}

export const AHEAD: Placement = { azimuthDeg: 0, elevationDeg: 0 };

/** A catalog star played on its own. */
export interface StarVoice {
  readonly kind: 'star';
  /** IAU name; the renderer reads the star's values from the tiles. */
  readonly star: string;
  readonly placement: Placement;
  /** Listening device: luminosity scaled so the star plays at this received level, dBFS. */
  readonly holdLevelDb?: number;
  /** Listening device: the star moved to this distance, with its level held (holdLevelDb). */
  readonly movedToPc?: number;
  readonly extinctionAvMag?: StatedValue;
  readonly residualArcsec?: StatedValue;
  /** A second id for the same star (the identity pitch item): pitch follows the id, not data. */
  readonly idSuffix?: string;
}

export interface PulsarVoice {
  readonly kind: 'pulsar';
  readonly pulsar: string;
  readonly placement: Placement;
  readonly spinFrequencyHz: StatedValue;
  readonly distancePc: StatedValue;
  /** The spin-down power EDOT in solar luminosities, the loudness input the harness uses. */
  readonly luminositySolar: StatedValue;
}

export type Voice = StarVoice | PulsarVoice;

/** The catalog stars on screen in a real view, as the app's sky feed takes them. */
export interface SkyStars {
  readonly kind: 'sky';
  /** The landing camera (both renderers start the cislunar scene looking along scene -z). */
  readonly view: 'landing' | { readonly raDeg: number; readonly decDeg: number };
  readonly fovDeg: number;
  /** Turn of the camera to the left, degrees (a pan across the sky). */
  readonly yawDeg?: number;
  /** Only the loudest of the stars on screen, for the item 0 diagnostic. */
  readonly loudestOnly?: boolean;
}

export interface Step {
  readonly atSec: number;
  /** What sounds from this moment on; omitted keeps the previous step's. */
  readonly sounds?: SkyStars | readonly Voice[];
  readonly skyBrightness?: StatedValue | null;
  /** The reveal control, 0 to 1 (a listening device until the app has an Unveil gesture). */
  readonly unveil?: number;
  /** Turns the reverb return down to silence from this moment. */
  readonly reverbOff?: boolean;
  /** Plays the UI detent at this moment. */
  readonly detent?: boolean;
}

export interface Clip {
  readonly id: string;
  readonly label: string;
  readonly durationSec: number;
  readonly regime: 'earth_capture' | 'cislunar';
  /** false: no impulse response is loaded, so the voices play dry. */
  readonly reverb: boolean;
  readonly bed: 'auto' | 'omnitone-foa' | 'stereo-diffuse';
  /** Diagnostic only (item 0): the highest harmonic a star tone carries, Hz. */
  readonly harmonicCeilingHz?: number;
  readonly steps: readonly Step[];
}

export interface ListeningItem {
  readonly id: string;
  /** The item of AUDIO_ENGINE.md section 10 this asks about; 0 is the first-listener impression. */
  readonly section10: number;
  readonly title: string;
  readonly question: string;
  readonly clips: readonly Clip[];
}

/** The app's default volume (the header slider starts at 0.42); every clip plays at it. */
export const APP_VOLUME = 0.42;
export const SAMPLE_RATE = 48000;

const LANDING_SKY: SkyStars = { kind: 'sky', view: 'landing', fovDeg: 56 };
const CITY_SKY = test(18.5, 'V mag/arcsec^2', SKYGLOW_REFERENCE);

/** A step sequence that walks a list of voices, `each` seconds apiece. */
function sequence(voices: readonly Voice[], each: number): Step[] {
  return voices.map((voice, i) => ({ atSec: i * each, sounds: [voice] }));
}

/** A voice circling the head once in `seconds`, at `stepSec` steps. */
function circling(star: string, seconds: number, stepSec: number): Step[] {
  const steps: Step[] = [];
  const count = Math.round(seconds / stepSec);
  for (let i = 0; i < count; i++) {
    const t = i * stepSec;
    const azimuthDeg = (360 * i) / count;
    steps.push({ atSec: Number(t.toFixed(3)), sounds: [{ kind: 'star', star, placement: { azimuthDeg, elevationDeg: 0 }, holdLevelDb: -24 }] });
  }
  return steps;
}

/** The landing sky panned left by `degrees` over `seconds`, at `stepSec` steps. */
function pan(degrees: number, seconds: number, stepSec: number): Step[] {
  const steps: Step[] = [];
  const count = Math.round(seconds / stepSec);
  for (let i = 0; i <= count; i++) {
    steps.push({ atSec: Number((i * stepSec).toFixed(3)), sounds: { ...LANDING_SKY, yawDeg: (degrees * i) / count } });
  }
  return steps;
}

export const LISTENING_ITEMS: readonly ListeningItem[] = [
  {
    id: 'first-listen',
    section10: 0,
    title: 'First listen: stars or static?',
    question:
      'Clip A is what played on your laptop: the landing as the app plays it. Does it sound like stars, or like static? If static, which of B, C and D sounds least like static?',
    clips: [
      { id: 'first-listen-a', label: 'A. The landing as the app plays it', durationSec: 8, regime: 'cislunar', reverb: true, bed: 'auto', steps: [{ atSec: 0, sounds: LANDING_SKY }] },
      { id: 'first-listen-b', label: 'B. The same voices, dry (no chapel reverb)', durationSec: 8, regime: 'cislunar', reverb: false, bed: 'auto', steps: [{ atSec: 0, sounds: LANDING_SKY }] },
      { id: 'first-listen-c', label: 'C. The loudest voice alone', durationSec: 8, regime: 'cislunar', reverb: true, bed: 'auto', steps: [{ atSec: 0, sounds: { ...LANDING_SKY, loudestOnly: true } }] },
      { id: 'first-listen-d', label: 'D. The same voices with harmonics cut above 4 kHz', durationSec: 8, regime: 'cislunar', reverb: true, bed: 'auto', harmonicCeilingHz: 4000, steps: [{ atSec: 0, sounds: LANDING_SKY }] },
    ],
  },
  {
    id: 'balance',
    section10: 1,
    title: 'Balance of voices, hiss and click',
    question: 'Under a bright sky, is the balance between the star voices, the skyglow hiss and the interface click right?',
    clips: [
      {
        id: 'balance',
        label: 'The landing stars under a bright test sky, with a click at 3 s and 6 s',
        durationSec: 8,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: [
          { atSec: 0, sounds: LANDING_SKY, skyBrightness: CITY_SKY },
          { atSec: 3, detent: true },
          { atSec: 6, detent: true },
        ],
      },
    ],
  },
  {
    id: 'distance',
    section10: 2,
    title: 'Distance',
    question: 'One star moved from near to far at an even level: does it sound near, then far, rather than dry, then wet? Does the far muffling fight its tone?',
    clips: [
      {
        id: 'distance',
        label: 'Achird moved to 1.5 pc, 10 pc and 300 pc, its level held at -26 dBFS',
        durationSec: 7.5,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: sequence(
          [1.5, 10, 300].map((pc): Voice => ({ kind: 'star', star: 'Achird', placement: AHEAD, movedToPc: pc, holdLevelDb: -26 })),
          2.5
        ),
      },
    ],
  },
  {
    id: 'temperature',
    section10: 3,
    title: 'Temperature as timbre',
    question: 'Seven stars from 2,829 K to 17,061 K, coolest first, at one level: is the travel from dark to bright pleasant? Do the hot ones sound buzzy?',
    clips: [
      {
        id: 'temperature',
        label: 'Proxima Centauri, Maasym, Achird, Seginus, Muliphein, Segin and Iklil, each at -24 dBFS',
        durationSec: 10.5,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: sequence(
          ['Proxima Centauri', 'Maasym', 'Achird', 'Seginus', 'Muliphein', 'Segin', 'Iklil'].map(
            (star): Voice => ({ kind: 'star', star, placement: AHEAD, holdLevelDb: -24 })
          ),
          1.5
        ),
      },
    ],
  },
  {
    id: 'separability',
    section10: 4,
    title: 'Telling the spectral channels apart',
    question: 'A hot, distant star behind dust, then a cool, near one, at one level: can you tell them apart?',
    clips: [
      {
        id: 'separability',
        label: 'Segin at 140 pc behind a test 2.0 mag of dust, then Achird at 5.9 pc, both at -26 dBFS',
        durationSec: 6,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: sequence(
          [
            { kind: 'star', star: 'Segin', placement: AHEAD, holdLevelDb: -26, extinctionAvMag: test(2, 'mag', 'Chosen for listening; the dust channel is not in the app (AUDIO_ENGINE.md 4.7)') },
            { kind: 'star', star: 'Achird', placement: AHEAD, holdLevelDb: -26 },
          ],
          3
        ),
      },
    ],
  },
  {
    id: 'loudness',
    section10: 5,
    title: 'Loudness at 4 dB per magnitude',
    question: 'Five stars, each about one bolometric magnitude fainter than the one before, at their real levels: is the range right?',
    clips: [
      {
        id: 'loudness',
        label: 'Fulu, Seginus, Sham, Cervantes and Mago at their catalog levels',
        durationSec: 9,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: sequence(
          ['Fulu', 'Seginus', 'Sham', 'Cervantes', 'Mago'].map((star): Voice => ({ kind: 'star', star, placement: AHEAD })),
          1.8
        ),
      },
    ],
  },
  {
    id: 'skyglow',
    section10: 6,
    title: 'Skyglow hiss',
    question: 'Is the hiss colour and level right? As the sky brightens, do faint voices drop out under it, and come back as it is unveiled?',
    clips: [
      {
        id: 'skyglow',
        label: 'The landing stars under test skies of 22.0 (natural, silent), 20.0 and 18.0 mag/arcsec^2, then unveiled',
        durationSec: 12,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: [
          { atSec: 0, sounds: LANDING_SKY, skyBrightness: test(22, 'V mag/arcsec^2', SKYGLOW_REFERENCE), unveil: 0 },
          { atSec: 3, skyBrightness: test(20, 'V mag/arcsec^2', SKYGLOW_REFERENCE) },
          { atSec: 6, skyBrightness: test(18, 'V mag/arcsec^2', SKYGLOW_REFERENCE) },
          { atSec: 9, unveil: 0.5 },
          { atSec: 10.5, unveil: 1 },
        ],
      },
    ],
  },
  {
    id: 'bed-renderers',
    section10: 7,
    title: 'Ambisonic bed against the plain stereo bed',
    question: 'The same test sky through Omnitone (first-order ambisonics) and through the plain stereo bed, matched in power: which do you prefer, on headphones and on speakers?',
    clips: [
      { id: 'bed-omnitone', label: 'Omnitone FOA bed, test sky 18.5 mag/arcsec^2', durationSec: 5, regime: 'earth_capture', reverb: true, bed: 'omnitone-foa', steps: [{ atSec: 0, sounds: [], skyBrightness: CITY_SKY }] },
      { id: 'bed-stereo', label: 'Plain stereo bed, the same test sky', durationSec: 5, regime: 'earth_capture', reverb: true, bed: 'stereo-diffuse', steps: [{ atSec: 0, sounds: [], skyBrightness: CITY_SKY }] },
    ],
  },
  {
    id: 'uncertainty',
    section10: 8,
    title: 'Uncertainty as beating',
    question: 'No layer, then a clean fifth at zero residual, then beating at test residuals of 30 and 120 arcsec: are the rates and the level right? Can you tell "no layer" from "clean fifth"?',
    clips: [
      {
        id: 'uncertainty',
        label: 'Achird with no residual, then test residuals of 0, 30 and 120 arcsec',
        durationSec: 10,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: sequence(
          [undefined, 0, 30, 120].map((arcsec): Voice => ({
            kind: 'star',
            star: 'Achird',
            placement: AHEAD,
            holdLevelDb: -24,
            ...(arcsec === undefined
              ? {}
              : { residualArcsec: test(arcsec, 'arcsec', 'Chosen for listening; catalog stars carry no solve residual (AUDIO_ENGINE.md 4.5)') }),
          })),
          2.5
        ),
      },
    ],
  },
  {
    id: 'pulsars',
    section10: 9,
    title: 'Pulsars at their real spin rates',
    question: 'The Crab at 29.95 pulses a second, then Vela at 11.19: does the generic pulse work? Does a pulsar sound too quiet next to a steady star?',
    clips: [
      {
        id: 'pulsars',
        label: 'Crab, then Vela, with ATNF spin frequency, distance and spin-down power',
        durationSec: 8,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: sequence(
          [
            {
              kind: 'pulsar',
              pulsar: 'Crab (J0534+2200)',
              placement: AHEAD,
              spinFrequencyHz: { value: 29.946923, unit: 'Hz', kind: 'published', label: 'F0', reference: ATNF },
              distancePc: { value: 2000, unit: 'pc', kind: 'published', label: 'DIST', reference: ATNF },
              luminositySolar: { value: 4.5e38 / 3.828e33, unit: 'Lsun', kind: 'published', label: 'EDOT 4.5e38 erg/s over 3.828e33 erg/s', reference: ATNF },
            },
            {
              kind: 'pulsar',
              pulsar: 'Vela (J0835-4510)',
              placement: AHEAD,
              spinFrequencyHz: { value: 11.1946499395, unit: 'Hz', kind: 'published', label: 'F0', reference: ATNF },
              distancePc: { value: 280, unit: 'pc', kind: 'published', label: 'DIST', reference: ATNF },
              luminositySolar: { value: 6.9e36 / 3.828e33, unit: 'Lsun', kind: 'published', label: 'EDOT 6.9e36 erg/s over 3.828e33 erg/s', reference: ATNF },
            },
          ],
          4
        ),
      },
    ],
  },
  {
    id: 'identity-pitch',
    section10: 10,
    title: 'Pitch carries no data',
    question: 'The same star under two ids gets two pitches. Did you hear the pitch as meaning something about the star?',
    clips: [
      {
        id: 'identity-pitch',
        label: 'Achird twice, under two ids, at -24 dBFS',
        durationSec: 6,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: sequence(
          [
            { kind: 'star', star: 'Achird', placement: AHEAD, holdLevelDb: -24 },
            { kind: 'star', star: 'Achird', placement: AHEAD, holdLevelDb: -24, idSuffix: 'second id' },
          ],
          3
        ),
      },
    ],
  },
  {
    id: 'reverb',
    section10: 11,
    title: 'The chapel reverb as the space around the voices',
    question: "With the chapel's 4 s tail, then without it: does the tail work as the space around the stars? Is the width right?",
    clips: [
      {
        id: 'reverb',
        label: 'Segin with the chapel reverb, then dry from 4 s',
        durationSec: 8,
        regime: 'earth_capture',
        reverb: true,
        bed: 'auto',
        steps: [
          { atSec: 0, sounds: [{ kind: 'star', star: 'Segin', placement: AHEAD }] },
          { atSec: 4, reverbOff: true },
        ],
      },
    ],
  },
  {
    id: 'hrtf',
    section10: 12,
    title: 'Direction around the head',
    question: 'On headphones, one voice circles you once, starting straight ahead and turning to your right: can you follow it? Does it flip front to back?',
    clips: [
      { id: 'hrtf', label: 'Achird circling the head once in 8 s, at -24 dBFS', durationSec: 8, regime: 'earth_capture', reverb: true, bed: 'auto', steps: circling('Achird', 8, 0.05) },
    ],
  },
  {
    id: 'transitions',
    section10: 13,
    title: 'Voices arriving and leaving',
    question: 'The camera pans 40 degrees across the landing sky: do voices enter and leave smoothly? Do the pitch glides on reassignment bother you?',
    clips: [
      { id: 'transitions', label: 'A 40 degree pan to the left across the landing sky over 8 s', durationSec: 8.5, regime: 'cislunar', reverb: true, bed: 'auto', steps: pan(40, 8, 0.1) },
    ],
  },
];
