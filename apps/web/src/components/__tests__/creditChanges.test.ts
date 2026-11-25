// @vitest-environment node
import fs from 'node:fs';
import { realSkyChanges } from '../../reveal/RealSkyLayer.js';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { AssetProvenanceRecordSchema } from '@albedian/contracts';
import { CHANGES, creditRows, recordChangeLines, type LoadedRecord } from '../creditRows.js';
import { homeBackdropChanges } from '../homeBackdrop.js';
import { glslFloat } from '../../spatial/webgl/glslFloat.js';
import { createGlobalPhotorealisticSkyDomeMesh, createSkyDomeUniforms } from '../../spatial/webgl/skyDomeAndCinemaShader.js';
import {
  LANDING_SKY_GAIN,
  PANORAMA_GRADE,
  milkyWay3dChanges,
  milkyWayCopyDrawn,
  panoramaGradeWords,
} from '../../spatial/sky/panoramaGrade.js';
import {
  FRAME_STEP_WORDS,
  LANDING_EXPOSURE,
  WEBGL_EXPOSURE,
  WEBGL_FRAME,
  frameFinishingWords,
  saidAsList,
  webglFrameFinishingWords,
  type FrameSwitch,
} from '../../spatial/frameFinishing.js';
import { DEFAULT_COSMOS_POST, type CosmosPostOptions } from '../../spatial/webgpu/postOptions.js';

// An image the app shows changed says so, wherever it is shown changed: in the record's own words where the record
// has them, else worded from the code that changes it, and tied to that code here so the words cannot outlive it.
// Its limit, written down and not chased: the words are held to three things, the grade's one list
// (sky/panoramaGrade.ts), the whole frame's list (the WebGPU post options, every key of it a worded step or a setting
// of one, the WebGL composer, and each renderer's tone-mapping curve and exposure in spatial/frameFinishing.ts), and
// each file a script made, by its record's own sentence shown whole; a step someone adds outside those is not seen by
// these tests.

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, '../../..');
const read = (relative: string) => fs.readFileSync(path.join(web, relative), 'utf8');
const textures = AssetProvenanceRecordSchema.parse(JSON.parse(read('public/textures/PROVENANCE.json')));
const fileOf = (name: string) => textures.files.find((f) => f.path === name);
const escaped = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const count = (text: string, part: string) => text.split(part).length - 1;
/** Every source file of the app under a folder, its tests left out. */
const sourceFiles = (dir: string): string[] =>
  fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' || entry.name === 'node_modules' ? [] : sourceFiles(full);
    return /\.(ts|tsx)$/.test(entry.name) ? [full] : [];
  });

const HOME = homeBackdropChanges();
const OPTICS = 'given the camera optics (colour fringes toward the edges and a vignette)';
const TONE_MAP = 'tone-mapped (ACES at exposure 1.12 in the WebGL renderer, AgX at exposure 3 in the WebGPU renderer)';
const WEBGL_TONE_MAP = 'tone-mapped (ACES at exposure 1.12)';
const SKY =
  'In the 3D view and the predicted sky it is placed on the sky through its registration, turned and moved a few pixels to meet the catalog ' +
  'stars, then darkened by a black level of 0.042, raised to the power 1.52, tinted warm (1.22, 1.04, 0.9 in red, green ' +
  'and blue), brightened 1.32 times and laid over a deep blue base (0.002, 0.003, 0.008); then the whole finished frame, ' +
  `not this photograph alone, is bloomed, ${OPTICS}, ${TONE_MAP} and smoothed along its high-contrast edges (FXAA ` +
  'anti-aliasing) in the WebGPU renderer only.';
const COPY =
  "The 3D view's WebGPU renderer draws a copy made from it in its place, graded the same way and scaled by 0.3 " +
  "before the frame's exposure of 3 (0.9 net before the AgX tone map).";

// C29 (UIUX-webuxpieces-READY.md item 5, decided by the lead 12:59): the change noted and briefly described, after
// Creative Commons' recommended practice, with the layer's own numbers; realsky-points-one (ruled 20:35 and 20:37 on
// 24 Nov): Real sky draws the starless copy, in the flight's form, and the photograph only where that cannot be
const REAL_SKY =
  'In Real sky, the copy made from it with its own stars removed is drawn in its place at its own level, ungraded, ' +
  'with Venus and Jupiter filled from the copy around them, redrawn to line up with a photo the app has matched and ' +
  "darkened beside the photo, up to 74 percent black (78 on a phone); where that copy or the catalog's stars cannot " +
  'be drawn, the photograph itself, its pixels otherwise unchanged.';
// milkyway-flight-one (ruled 16:18 on 24 Nov): the flight shows the starless copy instead, said here
const FLIGHT =
  'In the flight, the copy made from it with its own stars removed is drawn in its place at its own level, ungraded, ' +
  'with Venus and Jupiter filled from the copy around them; while you steer it is drawn on a sky turned with the camera.';
// predsky-starless-one (ruled 18:04 on 24 Nov): the predicted sky shows the starless copy and the catalog to 10.5
const PREDSKY =
  "In the predicted sky, the copy made from it with its own stars removed is drawn in its place, placed and graded the " +
  "same, and the catalog's stars are drawn in theirs down to magnitude 10.5.";

describe('the Milky Way row states every change the app makes to the panorama, by kind', () => {
  it('says each change, where it is made: the home, the 3D view and the predicted sky, the copy the WebGPU renderer draws, Real sky and the flight', () => {
    expect(CHANGES.eso0932a?.()).toEqual([HOME, SKY, COPY, REAL_SKY, FLIGHT, PREDSKY]);
    expect(realSkyChanges()).toBe(REAL_SKY);
    // Worded from the layer's own numbers
    expect(realSkyChanges({ edge: 0.6, near: 0.4, nearPx: 82 })).toContain('up to 60 percent black');
    expect(milkyWay3dChanges()).toBe(SKY);
    expect(milkyWayCopyDrawn()).toBe(COPY);
  });

  it("keeps the grade's steps in one list and words every step in it (a step added to a shader outside the list is not seen here)", () => {
    expect(Object.keys(PANORAMA_GRADE)).toEqual(['blackLevel', 'power', 'tint', 'gain', 'base']);
    const words = panoramaGradeWords();
    expect(Object.keys(words)).toEqual(Object.keys(PANORAMA_GRADE));
    for (const [step, phrase] of Object.entries(words)) {
      expect(SKY, step).toContain(phrase);
      const value = PANORAMA_GRADE[step as keyof typeof PANORAMA_GRADE];
      for (const number of (Array.isArray(value) ? value : [value]).map(String)) expect(phrase, step).toContain(number);
    }
    // Other numbers give other words
    expect(milkyWay3dChanges({ ...PANORAMA_GRADE, gain: 2.64 })).toContain('brightened 2.64 times');
  });

  it('writes both renderers from that list, and neither holds a number of the grade of its own', () => {
    const g = PANORAMA_GRADE;
    const webgl = (createGlobalPhotorealisticSkyDomeMesh(createSkyDomeUniforms()).material as THREE.ShaderMaterial).fragmentShader;
    expect(webgl).toContain(`vec3 cleanMw = max(vec3(0.0), mwCol - vec3(${glslFloat(g.blackLevel)}));`);
    expect(webgl).toContain(
      `vec3 galacticBulge = pow(cleanMw, vec3(${glslFloat(g.power)})) * vec3(${g.tint.map(glslFloat).join(', ')}) * ${glslFloat(g.gain)};`
    );
    expect(webgl).toContain(`vec3 deepSpaceBase = vec3(${g.base.map(glslFloat).join(', ')});`);
    expect(webgl).toContain('vec3 opticalSky = deepSpaceBase + galacticBulge;');
    const webgpu = read('src/spatial/webgpu/cislunar/skyBackground.ts');
    expect(webgpu).toContain('const cleanMw = max(vec3(0.0), mwCol.sub(PANORAMA_GRADE.blackLevel))');
    expect(webgpu).toContain('const galacticBulge = pow(cleanMw, vec3(PANORAMA_GRADE.power)).mul(vec3(...PANORAMA_GRADE.tint)).mul(PANORAMA_GRADE.gain);');
    expect(webgpu).toContain('const deepSpaceBase = vec3(...PANORAMA_GRADE.base);');
    // The base is added, in WebGPU as in WebGL, as the words say it is laid under the panorama
    expect(webgpu).toContain('const sky = deepSpaceBase.add(galacticBulge).mul(gain);');
    for (const source of [read('src/spatial/webgl/skyDomeAndCinemaShader.ts'), webgpu]) {
      for (const number of ['0.042', '1.52', '1.32', '1.22', '0.002, 0.003, 0.008']) expect(source, number).not.toContain(number);
    }
  });

  it('scales the WebGPU copy by the landing gain and exposure the words state, with their net product, and draws each file the words say', () => {
    expect(String(LANDING_SKY_GAIN)).toBe('0.3');
    expect(String(LANDING_EXPOSURE)).toBe('3');
    expect(COPY).toContain(`scaled by ${LANDING_SKY_GAIN} before the frame's exposure of ${LANDING_EXPOSURE} (0.9 net before the AgX tone map).`);
    expect(milkyWayCopyDrawn(0.25, 2.5)).toContain("scaled by 0.25 before the frame's exposure of 2.5 (0.625 net before the AgX tone map).");
    expect(milkyWayCopyDrawn(0.333, 3.0004)).toContain('(0.999 net before the AgX tone map).');
    const nodes = read('src/spatial/webgpu/cislunar/cislunarSceneNodes.ts');
    expect(nodes).toContain('createSkyBackgroundNode(milkyWay, registration, LANDING_SKY_GAIN)');
    expect(nodes).toContain("load('/textures/eso_milkyway_diffuse_6k.webp', SRGBColorSpace, true)");
    expect(read('src/spatial/webgl/skyDomeAndCinemaShader.ts')).toContain("loadTex('/textures/eso_milkyway_panorama.jpg', resolve)");
  });


  it('applies the frame-wide treatment the words name, by kind, in both renderers', () => {
    const spark = read('src/spatial/SparkPortalRenderer.ts');
    for (const part of ['new UnrealBloomPass(', 'createCinemaOpticsPass(', 'new OutputPass()', 'THREE.ACESFilmicToneMapping']) expect(spark, part).toContain(part);
    const optics = read('src/spatial/webgl/skyDomeAndCinemaShader.ts');
    for (const part of ['vec2 caOffset = centered * r2 * caStrength;', 'float vignette = smoothstep(0.92, 0.18, r2);', 'vec3 finalCol = baseCol * mix(0.76, 1.0, vignette);']) expect(optics, part).toContain(part);
    const chain = read('src/spatial/webgpu/postChain.ts');
    expect(chain).toContain('stages.push(`bloom(');
    expect(chain).toContain("stages.push('cinema optics(CA, vignette)');");
    expect(read('src/spatial/webgpu/WebGpuHost.ts')).toContain('renderer.toneMapping = AgXToneMapping;');
    for (const phrase of ['bloomed', 'colour fringes toward the edges', 'a vignette', 'tone-mapped']) expect(SKY, phrase).toContain(phrase);
    expect(SKY).not.toMatch(/streak/);
    // Placement, said once plainly: both renderers sample through the registration
    expect(read('src/spatial/webgl/skyDomeAndCinemaShader.ts')).toContain('uSceneToPanorama.value.set(...sceneToPanoramaRows(registration))');
    expect(read('src/spatial/webgpu/cislunar/skyBackground.ts')).toContain('sceneToPanoramaRows(registration)');
  });
});

describe("the whole finished frame's finishing is a list too, and the row words every step of it", () => {
  const switches = Object.entries(DEFAULT_COSMOS_POST)
    .filter(([, value]) => typeof value !== 'number')
    .map(([key]) => key);

  it('words every switch of the post options, so a step switched on with no word fails here', () => {
    expect([...switches].sort()).toEqual(Object.keys(FRAME_STEP_WORDS).sort());
    const options = DEFAULT_COSMOS_POST as unknown as Record<string, never>;
    const words = FRAME_STEP_WORDS as unknown as Record<string, (value: never) => string | null>;
    for (const key of switches) {
      const word = words[key]!(options[key]!);
      if (word !== null) expect(SKY, key).toContain(word);
    }
    // Anti-aliasing is named because the list holds it switched on
    expect(DEFAULT_COSMOS_POST.aa).toBe('fxaa');
    expect(SKY).toContain('smoothed along its high-contrast edges (FXAA anti-aliasing) in the WebGPU renderer only');
    // Said once to be the whole frame's, and not this photograph's alone
    expect(count(SKY, 'the whole finished frame, not this photograph alone, is')).toBe(1);
    // Other options give other words
    expect(saidAsList(frameFinishingWords({ ...DEFAULT_COSMOS_POST, aa: 'none' }))).toBe(`bloomed, ${OPTICS} and ${TONE_MAP}`);
    expect(frameFinishingWords({ ...DEFAULT_COSMOS_POST, aa: 'smaa' }).at(-1)).toBe('smoothed along its high-contrast edges (SMAA anti-aliasing) in the WebGPU renderer only');
    expect(frameFinishingWords({ ...DEFAULT_COSMOS_POST, bloom: false })[0]).toBe('bloomed in the WebGL renderer only');
    expect(frameFinishingWords(DEFAULT_COSMOS_POST, WEBGL_FRAME, 2.5, 1.5)).toContain(
      'tone-mapped (ACES at exposure 1.5 in the WebGL renderer, AgX at exposure 2.5 in the WebGPU renderer)'
    );
  });

  // Each number of the post options is a setting of a worded switch's step, written out here with the switch it sets;
  // typed from the options' number keys and the switches, so a number with no row, or a row naming no switch, also
  // fails the typecheck.
  type Setting = { [K in keyof CosmosPostOptions]: CosmosPostOptions[K] extends number ? K : never }[keyof CosmosPostOptions];
  const SETTINGS: { readonly [K in Setting]: FrameSwitch } = { bloomStrength: 'bloom', bloomRadius: 'bloom', bloomThreshold: 'bloom' };
  /** Each key of `options` that is neither a switch with words nor a number set to one, said with its key. */
  const unaccounted = (options: object, words: object, settings: Readonly<Record<string, string>>): string[] =>
    Object.entries(options).flatMap(([key, value]) => {
      if (typeof value !== 'number') return Object.hasOwn(words, key) ? [] : [`${key}: a switch with no words`];
      const sets = Object.hasOwn(settings, key) ? settings[key] : undefined;
      if (sets === undefined) return [`${key}: a number that sets no named step`];
      return Object.hasOwn(words, sets) ? [] : [`${key}: sets ${sets}, which is no worded step`];
    });

  it('accounts for every key of the post options, whatever its type: a worded step, or a setting of one', () => {
    const left = unaccounted(DEFAULT_COSMOS_POST, FRAME_STEP_WORDS, SETTINGS);
    expect(left, left.join('; ')).toEqual([]);
    // Every row is a number of the options, so the table holds no key the options have lost
    const options = DEFAULT_COSMOS_POST as unknown as Readonly<Record<string, unknown>>;
    for (const key of Object.keys(SETTINGS)) expect(typeof options[key], key).toBe('number');
  });

  // The shapes a new key can take, each under a made-up name no step would be called (brackets and spaces), so a real
  // step added under any name leaves every row meaning what it says; the test after the table holds that no made-up
  // name is a key the list, its words or its rows hold. Each row: the keys added, the rows added, the sentences given.
  const NEW_SWITCH = '[a made-up switch]';
  const NEW_NUMBER = '[a made-up number]';
  const NEW_MODE = '[a made-up mode]';
  const SHAPES: ReadonlyArray<readonly [string, object, Readonly<Record<string, string>>, readonly string[]]> = [
    ['a number with no row', { [NEW_NUMBER]: 0.2 }, {}, [`${NEW_NUMBER}: a number that sets no named step`]],
    ['a number set to a switch with words', { [NEW_NUMBER]: 0.2 }, { [NEW_NUMBER]: 'bloom' }, []],
    ['a number set to a switch that does not exist', { [NEW_NUMBER]: 0.2 }, { [NEW_NUMBER]: NEW_SWITCH }, [`${NEW_NUMBER}: sets ${NEW_SWITCH}, which is no worded step`]],
    ['a switch with no words', { [NEW_SWITCH]: true }, {}, [`${NEW_SWITCH}: a switch with no words`]],
    ['a mode with no words', { [NEW_MODE]: 'warm' }, {}, [`${NEW_MODE}: a switch with no words`]],
    // Words and rows are read as their own keys only, never inherited ones
    ['a switch named as an inherited key', { toString: true }, {}, ['toString: a switch with no words']],
    ['a number named as an inherited key', { constructor: 0.5 }, {}, ['constructor: a number that sets no named step']],
    ['two keys that are neither, each named', { [NEW_SWITCH]: true, [NEW_NUMBER]: 0.2 }, {}, [`${NEW_SWITCH}: a switch with no words`, `${NEW_NUMBER}: a number that sets no named step`]],
  ];
  /** Each shape's sentences over a list, its words and its rows. */
  const shapesOver = (options: object, words: object, settings: Readonly<Record<string, string>>) =>
    SHAPES.map(([name, added, rows]) => [name, unaccounted({ ...options, ...added }, words, { ...settings, ...rows })] as const);
  /** The table's made-up names: the three above, and the two inherited ones its own rows use. */
  const MADE_UP = [NEW_SWITCH, NEW_NUMBER, NEW_MODE, 'toString', 'constructor'];
  /** Each made-up name that a list, its words or its rows hold as a key of its own, said as such. */
  const clashes = (madeUp: readonly string[], ...lists: readonly object[]): string[] =>
    madeUp
      .filter((key) => lists.some((list) => Object.hasOwn(list, key)))
      .map((key) => `${key} is a made-up name of the shape table, and the list holds it: rename the table's key`);

  it('names each key that is neither, from a table of the shapes a new key can take', () => {
    const said = shapesOver(DEFAULT_COSMOS_POST, FRAME_STEP_WORDS, SETTINGS);
    SHAPES.forEach(([name, , , named], i) => expect(said[i]![1], name).toEqual(named));
  });

  it('makes up its keys under names the list, its words and its rows do not hold, and says so of one they do', () => {
    // Every key the table adds, as an option or as a row, is one of its made-up names
    const added = SHAPES.flatMap(([, keys, rows]) => [...Object.keys(keys), ...Object.keys(rows)]);
    expect(added.filter((key) => !MADE_UP.includes(key))).toEqual([]);
    const held = clashes(MADE_UP, DEFAULT_COSMOS_POST, FRAME_STEP_WORDS, SETTINGS);
    expect(held, held.join('; ')).toEqual([]);
    expect(clashes([...MADE_UP, 'filmGrain'], { ...DEFAULT_COSMOS_POST, filmGrain: true }, { ...FRAME_STEP_WORDS, [NEW_SWITCH]: () => null })).toEqual([
      `${NEW_SWITCH} is a made-up name of the shape table, and the list holds it: rename the table's key`,
      "filmGrain is a made-up name of the shape table, and the list holds it: rename the table's key",
    ]);
  });

  it('passes a step added as the list asks, under any name, and reads every shape the same over it', () => {
    for (const [step, amount] of [['filmGrain', 'filmGrainAmount'], ['halation', 'halationStrength']] as const) {
      const options = { ...DEFAULT_COSMOS_POST, [step]: true, [amount]: 0.2 };
      const words = { ...FRAME_STEP_WORDS, [step]: (on: boolean) => (on ? 'grained' : null) };
      const settings = { ...SETTINGS, [amount]: step };
      expect(unaccounted(options, words, settings), step).toEqual([]);
      expect(clashes(MADE_UP, options, words, settings), step).toEqual([]);
      expect(shapesOver(options, words, settings).map(([, said]) => said), step).toEqual(SHAPES.map(([, , , named]) => named));
    }
  });

  // One table of each switch's value when off and its values when on, with the words each on value gives, written
  // out here. A switch added to the post options with no row here fails the test's first line.
  const SWITCH_EDGES: Record<string, { readonly off: unknown; readonly on: readonly (readonly [unknown, string])[] }> = {
    aa: {
      off: 'none',
      on: [
        ['fxaa', 'smoothed along its high-contrast edges (FXAA anti-aliasing)'],
        ['smaa', 'smoothed along its high-contrast edges (SMAA anti-aliasing)'],
      ],
    },
    bloom: { off: false, on: [[true, 'bloomed']] },
    cinemaOptics: { off: false, on: [[true, OPTICS]] },
  };

  it('says each switch on in both renderers, in one, in neither, or in both with other values, from that table', () => {
    expect(Object.keys(SWITCH_EDGES).sort()).toEqual(Object.keys(FRAME_STEP_WORDS).sort());
    const allOff = Object.fromEntries(Object.entries(SWITCH_EDGES).map(([key, edge]) => [key, edge.off]));
    const gpuOff = { ...DEFAULT_COSMOS_POST, ...allOff } as CosmosPostOptions;
    const glOff = { ...WEBGL_FRAME, ...allOff } as typeof WEBGL_FRAME;
    // Every switch off in both: the tone map alone, which both renderers always apply with their curves and exposures
    expect(frameFinishingWords(gpuOff, glOff)).toEqual([TONE_MAP]);
    const steps = (gpu: object, gl: object) =>
      frameFinishingWords({ ...gpuOff, ...gpu }, { ...glOff, ...gl }).filter((word) => word !== TONE_MAP);
    for (const [key, edge] of Object.entries(SWITCH_EDGES)) {
      for (const [value, words] of edge.on) {
        expect(steps({ [key]: value }, {}), key).toEqual([`${words} in the WebGPU renderer only`]);
        expect(steps({}, { [key]: value }), key).toEqual([`${words} in the WebGL renderer only`]);
        expect(steps({ [key]: value }, { [key]: value }), key).toEqual([words]);
        // Two values that are both on and differ: each renderer's own words, the WebGL renderer's first
        for (const [other, otherWords] of edge.on.filter(([v]) => v !== value)) {
          expect(steps({ [key]: value }, { [key]: other }), key).toEqual([`${otherWords} in the WebGL renderer and ${words} in the WebGPU renderer`]);
        }
      }
    }
  });

  it('says a list as English does, from none to three', () => {
    expect(saidAsList([])).toBe('');
    expect(saidAsList(['tone-mapped'])).toBe('tone-mapped');
    expect(saidAsList(['bloomed', 'tone-mapped'])).toBe('bloomed and tone-mapped');
    expect(saidAsList(['bloomed', 'tone-mapped', 'smoothed'])).toBe('bloomed, tone-mapped and smoothed');
  });

  it("holds the WebGPU chain to the list, and the app to the list's defaults", () => {
    const chain = read('src/spatial/webgpu/postChain.ts');
    for (const part of [
      'if (options.bloom) {',
      'if (options.cinemaOptics) {',
      "if (options.aa === 'fxaa') {",
      'out = fxaa(display) as unknown as Vec4Node;',
      "} else if (options.aa === 'smaa') {",
      'out = smaa(display) as unknown as Vec4Node;',
      'pipeline.outputNode = out;',
    ]) expect(chain, part).toContain(part);
    // The renderer starts from the defaults, and nothing in the app changes them
    const renderer = read('src/spatial/webgpu/CislunarWebGpuRenderer.ts');
    expect(renderer).toContain('private postOptions: CosmosPostOptions = { ...DEFAULT_COSMOS_POST };');
    const callers = sourceFiles(path.join(web, 'src')).filter((file) => read(path.relative(web, file)).includes('.setPostOptions('));
    expect(callers.map((file) => path.relative(web, file))).toEqual([]);
  });

  it("holds the WebGL renderer's finishing to its composer: bloom, the camera optics and a tone map, and no anti-aliasing", () => {
    expect(WEBGL_FRAME).toEqual({ aa: 'none', bloom: true, cinemaOptics: true });
    const spark = read('src/spatial/SparkPortalRenderer.ts');
    expect(spark).toContain("antialias: false, powerPreference: 'high-performance'");
    expect([...spark.matchAll(/this\.composer\.addPass\(([^)(]*(?:\([^)]*\))?)/g)].map((m) => m[1])).toEqual([
      'new RenderPass(this.scene, this.camera)',
      'this.bloomPass',
      'createCinemaOpticsPass(this.cinemaOpticsUniforms)',
      'new OutputPass()',
    ]);
    expect(spark).not.toMatch(/FXAA|SMAA/);
  });

  it('holds each renderer to the tone curve and tone-mapping exposure the finishing words state', () => {
    expect(WEBGL_EXPOSURE).toBe(1.12);
    expect(LANDING_EXPOSURE).toBe(3.0);
    expect(read('src/spatial/frameFinishing.ts')).toContain('export const WEBGL_EXPOSURE = 1.12;');
    expect(read('src/spatial/frameFinishing.ts')).toContain('export const LANDING_EXPOSURE = 3.0;');
    expect(read('src/spatial/webgpu/CislunarWebGpuRenderer.ts')).toContain('host.renderer.toneMappingExposure = LANDING_EXPOSURE;');
    expect(read('src/spatial/webgpu/WebGpuHost.ts')).toContain('renderer.toneMapping = AgXToneMapping;');
    const spark = read('src/spatial/SparkPortalRenderer.ts');
    expect(spark).toContain('this.renderer.toneMapping = THREE.ACESFilmicToneMapping;');
    expect(spark).toContain('this.renderer.toneMappingExposure = WEBGL_EXPOSURE;');
    expect(spark).not.toContain('1.12');
    expect(webglFrameFinishingWords()).toEqual(['bloomed', OPTICS, WEBGL_TONE_MAP]);
    expect(webglFrameFinishingWords({ aa: 'none', bloom: false, cinemaOptics: false }, 1.5)).toEqual(['tone-mapped (ACES at exposure 1.5)']);
    expect(webglFrameFinishingWords({ aa: 'fxaa', bloom: false, cinemaOptics: true }, 1.12)).toEqual([
      OPTICS,
      WEBGL_TONE_MAP,
      'smoothed along its high-contrast edges (FXAA anti-aliasing)',
    ]);
  });
});

describe("a file a script made shows its record's own sentence on how it differs, whole, and nothing of the app's own", () => {
  const today = (): LoadedRecord[] => [{ url: '/textures/PROVENANCE.json', record: textures }];
  const withoutChanges = (): LoadedRecord[] => [
    {
      url: '/textures/PROVENANCE.json',
      record: {
        ...textures,
        files: textures.files.map((f) => {
          if (f.producedBy.method !== 'script') return f;
          const { changes: _omitted, ...rest } = f.producedBy;
          return { ...f, producedBy: rest };
        }),
      } as unknown as typeof textures,
    },
  ];
  const withField = (file: string, sentence: unknown): LoadedRecord[] => {
    const base = withoutChanges()[0]!.record;
    return [
      {
        url: '/textures/PROVENANCE.json',
        record: {
          ...base,
          files: base.files.map((f) => (f.path === file ? { ...f, producedBy: { ...f.producedBy, changes: sentence } } : f)),
        } as unknown as typeof textures,
      },
    ];
  };
  const milkyWay = (loaded: LoadedRecord[]) => creditRows(loaded, []).find((row) => row.name === 'Milky Way photograph')!.blocks;

  it('shows no further line when a record holds no such field, and shows the two eso0932a script sentences from disk today', () => {
    expect(fileOf('eso_milkyway_diffuse_6k.webp')?.producedBy.method).toBe('script');
    expect(recordChangeLines(withoutChanges(), 'eso0932a')).toEqual([]);
    expect(milkyWay(withoutChanges()).map((block) => block.changes)).toEqual([[HOME, SKY, COPY, REAL_SKY, FLIGHT, PREDSKY]]);
    const registration = fileOf('eso0932a_registration.json')!.producedBy;
    const starless = fileOf('eso_milkyway_diffuse_6k.webp')!.producedBy;
    expect(registration.method === 'script' && registration.changes).toBeTruthy();
    expect(starless.method === 'script' && starless.changes).toBeTruthy();
    const diskSentences = [
      registration.method === 'script' ? registration.changes : '',
      starless.method === 'script' ? starless.changes : '',
    ];
    expect(recordChangeLines(today(), 'eso0932a')).toEqual(diskSentences);
    expect(milkyWay(today()).map((block) => block.changes)).toEqual([[HOME, SKY, COPY, REAL_SKY, FLIGHT, PREDSKY, ...diskSentences]]);
  });

  it("shows the field's sentence character for character once the record holds it, after the app's own lines", () => {
    const sentence = 'Stars removed and filled from their surroundings;  then  smoothed (5\u00d75), with odd spacing kept.';
    const loaded = withField('eso_milkyway_diffuse_6k.webp', sentence);
    expect(recordChangeLines(loaded, 'eso0932a')).toEqual([sentence]);
    expect(milkyWay(loaded).map((block) => block.changes)).toEqual([[HOME, SKY, COPY, REAL_SKY, FLIGHT, PREDSKY, sentence]]);
    // Only a file a script made: a downloaded file's entry gives none, whatever it holds
    expect(recordChangeLines(withField('eso_milkyway_panorama.jpg', sentence), 'eso0932a')).toEqual([]);
    // Only for a source whose terms ask that changes be indicated: the copy's other sources give none
    expect(recordChangeLines(loaded, 'gaia-dr3')).toEqual([]);
  });

  it('shows a text of one character or more, and only from a file a script made from that very source', () => {
    expect(recordChangeLines(withField('eso_milkyway_diffuse_6k.webp', '.'), 'eso0932a')).toEqual(['.']);
    for (const field of ['', 0, 1, true, null, ['Stars removed.'], { text: 'Stars removed.' }]) {
      expect(recordChangeLines(withField('eso_milkyway_diffuse_6k.webp', field), 'eso0932a'), JSON.stringify(field)).toEqual([]);
    }
    // A file a script made from another source: its sentence is not this source's
    expect(fileOf('earth_night_blackmarble_2016_8k.jpg')?.producedBy.method).toBe('script');
    expect(recordChangeLines(withField('earth_night_blackmarble_2016_8k.jpg', 'Lights kept.'), 'eso0932a')).toEqual([]);
  });
});

describe('the grade writes its numbers into GLSL', () => {
  it('writes a number as a GLSL float, always with a decimal point or an exponent', () => {
    const rows: Array<[number, string]> = [
      [0, '0.0'],
      [2, '2.0'],
      [-1, '-1.0'],
      [0.08, '0.08'],
      [1.9, '1.9'],
      [-0.5, '-0.5'],
      [1e-7, '1e-7'],
      [1e21, '1e+21'],
    ];
    for (const [value, text] of rows) expect(glslFloat(value), String(value)).toBe(text);
    for (const value of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      expect(() => glslFloat(value), String(value)).toThrow(`${value} is not a finite number, so it has no GLSL float`);
    }
  });
});
