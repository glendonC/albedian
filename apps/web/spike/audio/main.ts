/**
 * Listening harness for the spatial audio engine. Dev only: served by `pnpm dev` at
 * /spike/audio/ and never part of the production build. Every control is one physical input;
 * every readout is what the engine derived from it.
 */
import { SpatialAudioEngine, type EngineSnapshot } from '../../src/audio/engine/SpatialAudioEngine';
import type { BedPreference } from '../../src/audio/engine/SkyglowBed';
import {
  PARSEC_KM,
  artificialSkyglowRatio,
  ballesterosTeffFromBv,
  type ScaleAudioRegime,
} from '../../src/audio/mappings';
import {
  directionFromAzimuthElevation,
  yawQuat,
  type AudioSourceInput,
  type AudioSourceKind,
} from '../../src/audio/voicePlanning';

const engine = new SpatialAudioEngine();
// Exposed for console inspection while listening.
(window as unknown as { albedianAudio: SpatialAudioEngine }).albedianAudio = engine;

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

const AU_PER_PC = 206264.80624709636;

function formatDistance(pc: number): string {
  if (pc < 1e-5) return `${(pc * PARSEC_KM).toPrecision(3)} km`;
  if (pc < 0.05) return `${(pc * AU_PER_PC).toPrecision(3)} AU`;
  if (pc < 1e3) return `${pc.toPrecision(3)} pc`;
  if (pc < 1e6) return `${(pc / 1e3).toPrecision(3)} kpc`;
  return `${(pc / 1e6).toPrecision(3)} Mpc`;
}

function formatHz(hz: number | null | undefined): string {
  if (hz === null || hz === undefined) return 'none';
  return hz >= 1000 ? `${(hz / 1000).toFixed(2)} kHz` : `${hz.toPrecision(4)} Hz`;
}

function formatDb(db: number | null | undefined): string {
  if (db === null || db === undefined) return 'missing';
  return db === -Infinity ? '-inf dB' : `${db.toFixed(1)} dB`;
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  children: (Node | string)[] = []
): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

interface SliderSpec {
  readonly label: string;
  readonly min: number;
  readonly max: number;
  readonly step: number;
  readonly value: number;
  readonly format: (value: number) => string;
  readonly onInput: (value: number) => void;
  /** Adds a "missing" checkbox that withholds this input from the engine. */
  readonly missing?: { readonly initial: boolean; readonly onToggle: (missing: boolean) => void };
}

interface SliderHandle {
  readonly row: HTMLElement;
  set(value: number): void;
  setMissing(missing: boolean): void;
}

function slider(spec: SliderSpec): SliderHandle {
  const range = el('input', { type: 'range', min: String(spec.min), max: String(spec.max), step: String(spec.step) });
  range.value = String(spec.value);
  const output = el('output', { textContent: spec.format(spec.value) });
  const labelCell = el('label', { textContent: spec.label });
  let missingBox: HTMLInputElement | null = null;
  if (spec.missing) {
    const onToggle = spec.missing.onToggle;
    missingBox = el('input', { type: 'checkbox', checked: spec.missing.initial });
    missingBox.addEventListener('change', () => {
      range.disabled = missingBox!.checked;
      onToggle(missingBox!.checked);
    });
    range.disabled = spec.missing.initial;
    labelCell.append(el('br'), el('span', { className: 'missing' }, [missingBox, 'missing']));
  }
  range.addEventListener('input', () => {
    const value = Number(range.value);
    output.textContent = spec.format(value);
    spec.onInput(value);
  });
  return {
    row: el('div', { className: 'row' }, [labelCell, range, output]),
    set(value: number) {
      range.value = String(value);
      output.textContent = spec.format(value);
    },
    setMissing(missing: boolean) {
      if (!missingBox) return;
      missingBox.checked = missing;
      range.disabled = missing;
    },
  };
}

function selectRow<T extends string>(label: string, options: readonly T[], value: T, onChange: (v: T) => void): HTMLElement {
  const select = el('select', {}, options.map((o) => el('option', { value: o, textContent: o })));
  select.value = value;
  select.addEventListener('change', () => onChange(select.value as T));
  return el('div', { className: 'row' }, [el('label', { textContent: label }), select, el('span')]);
}

function checkRow(label: string, checked: boolean, onChange: (v: boolean) => void): HTMLElement {
  const box = el('input', { type: 'checkbox', checked });
  box.addEventListener('change', () => onChange(box.checked));
  return el('div', { className: 'row' }, [el('label', { textContent: label }), el('span', {}, [box]), el('span')]);
}

// ---------------------------------------------------------------------------
// Global controls
// ---------------------------------------------------------------------------

const panels = document.getElementById('panels')!;
const startButton = document.getElementById('start') as HTMLButtonElement;
const engineStatus = document.getElementById('engine-status')!;
const meter = document.getElementById('meter') as HTMLMeterElement;
const meterDb = document.getElementById('meter-db')!;

startButton.addEventListener('click', () => {
  // Inside the click handler: the only place an AudioContext may be created or resumed.
  if (engine.enabled) {
    engine.stop();
    startButton.textContent = 'Start audio';
  } else if (engine.start()) {
    startButton.textContent = 'Stop audio';
  }
});

const regimes: readonly ScaleAudioRegime[] = [
  'earth_capture',
  'cislunar',
  'solar_neighborhood',
  'deep_sky',
  'planetary_surface',
];
const bedPreferences: readonly BedPreference[] = ['auto', 'omnitone-foa', 'stereo-diffuse'];

const sky = { magArcsec2: 18.5, missing: false, unveil: 0 };
const skyReadout = el('div', { className: 'status mono' });

function pushSky(): void {
  engine.setSkyBrightness(sky.missing ? null : sky.magArcsec2);
  engine.setUnveil(sky.unveil);
}

const lockTone = { bv: 0.65, pan: 0 };

panels.append(
  el('section', {}, [
    el('h2', { textContent: 'Master and space' }),
    slider({ label: 'volume', min: 0, max: 1, step: 0.01, value: 0.85, format: (v) => v.toFixed(2), onInput: (v) => engine.setMasterVolume(v) }).row,
    slider({ label: 'reverb return', min: -24, max: 6, step: 0.5, value: 0, format: (v) => `${v.toFixed(1)} dB`, onInput: (v) => engine.setReverbReturnDb(v) }).row,
    selectRow('regime', regimes, 'earth_capture', (r) => engine.setRegime(r)),
    selectRow('bed renderer', bedPreferences, 'auto', (p) => engine.setBedPreference(p)),
    slider({ label: 'listener yaw', min: -180, max: 180, step: 1, value: 0, format: (v) => `${v.toFixed(0)} deg`, onInput: (v) => engine.setListenerOrientation(yawQuat(v)) }).row,
  ]),
  el('section', {}, [
    el('h2', { textContent: 'Skyglow bed' }),
    slider({
      label: 'sky brightness',
      min: 14,
      max: 22.5,
      step: 0.05,
      value: sky.magArcsec2,
      format: (v) => `${v.toFixed(2)} mag/as2`,
      onInput: (v) => {
        sky.magArcsec2 = v;
        pushSky();
      },
      missing: {
        initial: sky.missing,
        onToggle: (m) => {
          sky.missing = m;
          pushSky();
        },
      },
    }).row,
    slider({
      label: 'unveil',
      min: 0,
      max: 1,
      step: 0.01,
      value: sky.unveil,
      format: (v) => v.toFixed(2),
      onInput: (v) => {
        sky.unveil = v;
        pushSky();
      },
    }).row,
    skyReadout,
  ]),
  el('section', {}, [
    el('h2', { textContent: 'One-shots (UI, not data)' }),
    el('div', { className: 'toolbar' }, [
      (() => {
        const b = el('button', { type: 'button', textContent: 'Detent click' });
        b.addEventListener('click', () => engine.playDetentClick(1020));
        return b;
      })(),
      (() => {
        const b = el('button', { type: 'button', textContent: 'Lock tone' });
        b.addEventListener('click', () => engine.playLockTone(ballesterosTeffFromBv(lockTone.bv), lockTone.pan));
        return b;
      })(),
    ]),
    slider({
      label: 'lock B-V',
      min: -0.4,
      max: 2,
      step: 0.01,
      value: lockTone.bv,
      format: (v) => `${v.toFixed(2)} (${Math.round(ballesterosTeffFromBv(v) ?? 0)} K)`,
      onInput: (v) => {
        lockTone.bv = v;
      },
    }).row,
    slider({ label: 'lock pan', min: -1, max: 1, step: 0.01, value: lockTone.pan, format: (v) => v.toFixed(2), onInput: (v) => (lockTone.pan = v) }).row,
  ])
);
pushSky();

// ---------------------------------------------------------------------------
// Test sources
// ---------------------------------------------------------------------------

type MissingKey = 'direction' | 'distance' | 'luminosity' | 'teff' | 'spin' | 'av' | 'residual';

interface SourceState {
  readonly id: string;
  enabled: boolean;
  kind: AudioSourceKind;
  target: boolean;
  azimuthDeg: number;
  elevationDeg: number;
  log10DistancePc: number;
  log10Luminosity: number;
  log10TeffK: number;
  spinHz: number;
  avMag: number;
  residualArcsec: number;
  missing: Record<MissingKey, boolean>;
}

interface Preset {
  readonly label: string;
  readonly apply: Partial<Omit<SourceState, 'id' | 'missing'>> & { readonly missing?: Partial<Record<MissingKey, boolean>> };
}

/**
 * Pulsar presets are ATNF Pulsar Catalogue v2.6.1 values: F0, DIST and EDOT (spin-down power,
 * converted with L_sun = 3.828e33 erg/s and used here as the luminosity input).
 */
const PRESETS: readonly Preset[] = [
  {
    label: 'Sun at 10 pc',
    apply: { kind: 'star', log10DistancePc: 1, log10Luminosity: 0, log10TeffK: Math.log10(5772) },
  },
  {
    label: 'Crab pulsar',
    apply: {
      kind: 'pulsar',
      spinHz: 29.946923,
      log10DistancePc: Math.log10(2000),
      log10Luminosity: Math.log10(4.5e38 / 3.828e33),
      missing: { spin: false },
    },
  },
  {
    label: 'Vela pulsar',
    apply: {
      kind: 'pulsar',
      spinHz: 11.1946499395,
      log10DistancePc: Math.log10(280),
      log10Luminosity: Math.log10(6.9e36 / 3.828e33),
      missing: { spin: false },
    },
  },
];

function defaultMissing(overrides: Partial<Record<MissingKey, boolean>> = {}): Record<MissingKey, boolean> {
  return { direction: false, distance: false, luminosity: false, teff: false, spin: false, av: true, residual: true, ...overrides };
}

const sources: SourceState[] = [
  {
    id: 'A',
    enabled: true,
    kind: 'star',
    target: false,
    azimuthDeg: -40,
    elevationDeg: 10,
    log10DistancePc: 1,
    log10Luminosity: 0,
    log10TeffK: Math.log10(5772),
    spinHz: 29.946923,
    avMag: 0,
    residualArcsec: 0,
    missing: defaultMissing(),
  },
  {
    id: 'B',
    enabled: false,
    kind: 'star',
    target: false,
    azimuthDeg: 35,
    elevationDeg: -5,
    log10DistancePc: Math.log10(300),
    log10Luminosity: 4,
    log10TeffK: Math.log10(25000),
    spinHz: 29.946923,
    avMag: 1.5,
    residualArcsec: 40,
    missing: defaultMissing({ av: false, residual: false }),
  },
  {
    id: 'C',
    enabled: false,
    kind: 'pulsar',
    target: false,
    azimuthDeg: 80,
    elevationDeg: 20,
    log10DistancePc: Math.log10(280),
    log10Luminosity: Math.log10(6.9e36 / 3.828e33),
    log10TeffK: Math.log10(5772),
    spinHz: 11.1946499395,
    avMag: 0,
    residualArcsec: 0,
    missing: defaultMissing(),
  },
];

function toInput(s: SourceState): AudioSourceInput | null {
  if (!s.enabled) return null;
  return {
    id: s.id,
    kind: s.kind,
    isTarget: s.target,
    evidenceClass: 'simulated',
    directionWorld: s.missing.direction ? null : directionFromAzimuthElevation(s.azimuthDeg, s.elevationDeg),
    distancePc: s.missing.distance ? null : 10 ** s.log10DistancePc,
    luminositySolar: s.missing.luminosity ? null : 10 ** s.log10Luminosity,
    teffK: s.missing.teff ? null : 10 ** s.log10TeffK,
    spinFrequencyHz: s.missing.spin ? null : s.spinHz,
    extinctionAvMag: s.missing.av ? null : s.avMag,
    residualArcsec: s.missing.residual ? null : s.residualArcsec,
  };
}

function pushSources(): void {
  engine.setSources(sources.flatMap((s) => toInput(s) ?? []));
}

const statusById = new Map<string, HTMLElement>();

for (const source of sources) {
  const statusEl = el('div', { className: 'status mono', textContent: 'not sent' });
  statusById.set(source.id, statusEl);
  const handles: Partial<Record<string, SliderHandle>> = {};

  const missingFor = (key: MissingKey) => ({
    initial: source.missing[key],
    onToggle: (m: boolean) => {
      source.missing[key] = m;
      pushSources();
    },
  });

  handles.azimuth = slider({ label: 'azimuth', min: -180, max: 180, step: 1, value: source.azimuthDeg, format: (v) => `${v.toFixed(0)} deg`, onInput: (v) => { source.azimuthDeg = v; pushSources(); }, missing: missingFor('direction') });
  handles.elevation = slider({ label: 'elevation', min: -90, max: 90, step: 1, value: source.elevationDeg, format: (v) => `${v.toFixed(0)} deg`, onInput: (v) => { source.elevationDeg = v; pushSources(); } });
  handles.distance = slider({ label: 'distance', min: -13, max: 9, step: 0.01, value: source.log10DistancePc, format: (v) => formatDistance(10 ** v), onInput: (v) => { source.log10DistancePc = v; pushSources(); }, missing: missingFor('distance') });
  handles.luminosity = slider({ label: 'luminosity', min: -4, max: 6, step: 0.01, value: source.log10Luminosity, format: (v) => `${(10 ** v).toPrecision(3)} Lsun`, onInput: (v) => { source.log10Luminosity = v; pushSources(); }, missing: missingFor('luminosity') });
  handles.teff = slider({ label: 'Teff (star)', min: 3, max: 4.7, step: 0.001, value: source.log10TeffK, format: (v) => `${Math.round(10 ** v)} K`, onInput: (v) => { source.log10TeffK = v; pushSources(); }, missing: missingFor('teff') });
  handles.spin = slider({ label: 'spin (pulsar)', min: -2, max: 3, step: 0.001, value: Math.log10(source.spinHz), format: () => `${source.spinHz.toPrecision(9)} Hz`, onInput: (v) => { source.spinHz = 10 ** v; handles.spin?.set(v); pushSources(); }, missing: missingFor('spin') });
  handles.av = slider({ label: 'A_V', min: 0, max: 10, step: 0.05, value: source.avMag, format: (v) => `${v.toFixed(2)} mag`, onInput: (v) => { source.avMag = v; pushSources(); }, missing: missingFor('av') });
  handles.residual = slider({ label: 'residual', min: 0, max: 240, step: 1, value: source.residualArcsec, format: (v) => `${v.toFixed(0)} arcsec`, onInput: (v) => { source.residualArcsec = v; pushSources(); }, missing: missingFor('residual') });

  const presetButtons = PRESETS.map((preset) => {
    const button = el('button', { type: 'button', textContent: preset.label });
    button.addEventListener('click', () => {
      const { missing, ...values } = preset.apply;
      Object.assign(source, values);
      if (missing) Object.assign(source.missing, missing);
      handles.distance?.set(source.log10DistancePc);
      handles.luminosity?.set(source.log10Luminosity);
      handles.teff?.set(source.log10TeffK);
      handles.spin?.set(Math.log10(source.spinHz));
      handles.spin?.setMissing(source.missing.spin);
      kindSelect.value = source.kind;
      pushSources();
    });
    return button;
  });

  const kindRow = selectRow<AudioSourceKind>('kind', ['star', 'pulsar'], source.kind, (k) => {
    source.kind = k;
    pushSources();
  });
  const kindSelect = kindRow.querySelector('select')!;

  panels.append(
    el('section', {}, [
      el('h2', { textContent: `Source ${source.id}` }),
      checkRow('enabled', source.enabled, (v) => { source.enabled = v; pushSources(); }),
      checkRow('target', source.target, (v) => { source.target = v; pushSources(); }),
      kindRow,
      el('div', { className: 'toolbar' }, presetButtons),
      ...Object.values(handles).flatMap((h) => (h ? [h.row] : [])),
      statusEl,
    ])
  );
}
pushSources();

// ---------------------------------------------------------------------------
// Readouts
// ---------------------------------------------------------------------------

function describeSource(id: string, snapshot: EngineSnapshot): { text: string; cls: string } {
  const voice = snapshot.voices.find((v) => v.id === id);
  if (voice) {
    const lines = [
      `sounding${voice.slot === null ? ' (after start)' : ` in slot ${voice.slot}`}, identity pitch ${formatHz(voice.pitchHz)} (no data)`,
      `level ${formatDb(voice.levelDb)}  DRR ${voice.drrDb.toFixed(1)} dB  distance LP ${formatHz(voice.distanceLowpassHz)}`,
      `dust LP ${formatHz(voice.dustLowpassHz)}  beat ${voice.uncertainty ? formatHz(voice.uncertainty.beatHz) : 'none'}`,
      voice.star
        ? `Teff ${Math.round(voice.star.teffK)} K  centroid ${formatHz(voice.centroidHz)}`
        : `pulse rate ${formatHz(voice.pulsar?.pulseRateHz)}`,
    ];
    return { text: lines.join('\n'), cls: 'status mono sounding' };
  }
  const silent = snapshot.silent.find((s) => s.id === id);
  if (silent) {
    const why = silent.reason === 'missing_input' ? `missing ${silent.missing.join(', ')}` : 'below the audibility floor';
    return { text: `silent: ${why}`, cls: 'status mono silent' };
  }
  if (snapshot.notSelected.includes(id)) return { text: 'audible but outside the voice pool', cls: 'status mono' };
  return { text: 'disabled', cls: 'status mono' };
}

function refreshReadouts(): void {
  const snapshot = engine.getSnapshot();
  const reverb =
    snapshot.reverb.status === 'failed' ? `reverb failed (${snapshot.reverb.error})` : `reverb ${snapshot.reverb.status}`;
  engineStatus.textContent = `context ${snapshot.contextState}, ${reverb}, bed ${snapshot.bed.renderer}, limiter ${engine
    .getLimiterReductionDb()
    .toFixed(1)} dB`;

  const level = engine.getOutputLevelDb();
  meter.value = Number.isFinite(level) ? level : -80;
  meterDb.textContent = Number.isFinite(level) ? `${level.toFixed(1)} dBFS` : '-inf';

  const ratio = sky.missing ? null : artificialSkyglowRatio(sky.magArcsec2);
  skyReadout.textContent = [
    `artificial/natural (Falchi 2016): ${ratio === null ? 'missing' : ratio.toFixed(2)}`,
    `bed level ${formatDb(snapshot.bed.levelDb)}${snapshot.bed.silentBecause ? `, silent: ${snapshot.bed.silentBecause}` : ''}`,
    snapshot.bed.fallbackReason ? `fallback: ${snapshot.bed.fallbackReason}` : `renderer ${snapshot.bed.renderer}`,
  ].join('\n');

  for (const [id, statusEl] of statusById) {
    const { text, cls } = describeSource(id, snapshot);
    statusEl.textContent = text;
    statusEl.className = cls;
  }
}

refreshReadouts();
setInterval(refreshReadouts, 150);
