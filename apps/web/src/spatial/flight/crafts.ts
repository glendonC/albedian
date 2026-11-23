import type { Vec3 } from '@albedian/camera-rig';
import hangar from '../../../public/models/hangar/hangar.json';

/**
 * pilot-1's crafts, from the ASSETS lane's hangar (public/models/hangar/hangar.json, its files in
 * public/models/PROVENANCE.json): each entry's model in the hangar's own frame (+Z forward, +Y up, metres), its chase
 * level on the laptop and its far level on the phone (the hangar's lod1 and lod2), its picture, and its real speed
 * where it really travels between stars, read at NASA's own page and labelled with that page and date. A craft or suit
 * that does not travel between stars has no speed and so no real travel time (D26 item 5, D35 item 5).
 */

export type CraftId = string;

export interface CraftSpeed {
  readonly kmS: number;
  readonly relativeTo: 'the Sun';
  /** The page the figure was read on, and the date the page gives for it (none where the page gives none). */
  readonly sourceUrl: string;
  readonly asOf: string | null;
}

export interface Craft {
  readonly id: CraftId;
  /** The hangar entry's own name. */
  readonly name: string;
  /** The hangar's kind for its model: a craft or a spacesuit. */
  readonly kind: 'craft' | 'suit';
  /** The chase camera's level (the hangar's lod1). */
  readonly modelUrl: string;
  /** The far or phone view's level (the hangar's lod2). */
  readonly phoneModelUrl: string;
  /** The picker's picture. */
  readonly pictureUrl: string;
  /** In the model's own axes (the hangar's frame): the way the craft flies, and its up. */
  readonly modelForward: Vec3;
  readonly modelUp: Vec3;
  readonly speed: CraftSpeed | null;
  /** The keys of the NASA facts the hangar's record holds for this entry. */
  readonly factKeys: readonly string[];
  /** Where the design lane rules a craft framed by its body (the hangar's bodyRadiusM), that radius; else none. */
  readonly frameByBodyRadiusM: number | null;
}

interface HangarFile {
  readonly frame: { readonly forward: string; readonly up: string };
  readonly models: Readonly<Record<string, { readonly kind: string; readonly bodyRadiusM?: number; readonly files: Readonly<Record<string, { readonly path: string }>> }>>;
  readonly entries: readonly {
    readonly id: string;
    readonly name: string;
    readonly model: string;
    readonly travel: { readonly speedKmS: number; readonly relativeTo: string; readonly asOf: string | null; readonly page: string } | null;
    readonly facts?: readonly { readonly key: string }[];
  }[];
}

/**
 * The elevation each NASA picture looks down on its craft from (the design lane's 13:35 ruling on 21 Nov): NASA states
 * no angle, so each was found on the live sheet from 15, 30 and 45 degrees, kept where a panel's apparent width over its
 * length comes nearest the picture's (elev-1, .cache/uiux/built/elev-1/): NEAR's panels 0.35 and 0.37 in its picture,
 * 0.33 at 15 and 0.47 at 30; Stardust's arrays about 0.26 in its picture, about 0.10, 0.15 and 0.18 at 15, 30 and 45.
 */
export const STARDUST_ELEVATION_DEG = 45;
export const NEAR_ELEVATION_DEG = 15;
export const KEPLER_ELEVATION_DEG = 20;
export const KEPLER_HEADING_DEG = 40;
export const INGENUITY_ELEVATION_DEG = 15;
export const CLEMENTINE_HEADING_DEG = 12;

/**
 * A rest attitude seen from `deg` above: the camera's look and the screen's up turned together about the screen's
 * horizontal axis, so the craft's top tilts toward the camera and its heading stays as built.
 */
function seenFromAbove(forward: Vec3, up: Vec3, deg: number): { readonly forward: Vec3; readonly up: Vec3 } {
  const c = Math.cos((deg * Math.PI) / 180);
  const s = Math.sin((deg * Math.PI) / 180);
  return {
    forward: [c * forward[0] - s * up[0], c * forward[1] - s * up[1], c * forward[2] - s * up[2]],
    up: [s * forward[0] + c * up[0], s * forward[1] + c * up[1], s * forward[2] + c * up[2]],
  };
}

/**
 * A craft's attitude at rest, by hangar model, where it is not the hangar's own frame (the design lane's rulings in
 * UIUX-pilot-READY.md): the model axis drawn along the way the camera looks (`forward`) and the one drawn up the screen.
 * - 13:32: Voyager stands as D26's render shows it, its long boom (the model's -X) straight down under the dish, its line
 *   of flight kept.
 * - 17:10: a craft that never travels rests as its picture stands it: Hubble's tube and Gemini's nose (the model's +Z,
 *   their long axis) up the screen, a quarter turn about the screen's horizontal axis from the line of flight, so the
 *   model's +Y, its picture's up, faces the camera.
 * - 17:24, checked live against the pictures: Spitzer's tube up with its sunshield behind it (the turn the other way
 *   round, so the shield's side faces away); Dawn's wings straight across with their faces to the camera.
 * Every other model keeps the hangar's frame: +Z its line of flight, +Y the up of its picker picture (build_craft.py
 * renders the pictures with the camera's up along it).
 */
/** The look from the front quarter on the left, level (the hangar's picture camera's view across, build_craft.py). */
const FRONT_QUARTER: Vec3 = [0.55 / Math.hypot(0.55, 1), 0, -1 / Math.hypot(0.55, 1)];

const REST_ATTITUDE: Readonly<Record<string, { readonly forward?: Vec3; readonly up: Vec3 }>> = {
  voyager: { up: [1, 0, 0] },
  hubble: { forward: [0, -1, 0], up: [0, 0, 1] },
  gemini: { forward: [0, -1, 0], up: [0, 0, 1] },
  spitzer: { forward: [0, 1, 0], up: [0, 0, 1] },
  // rest-1, checked live on 21 Nov against the pictures (17:58, 18:05, 18:35 rulings); Stardust, MGS, MAVEN, IBEX keep the frame
  dawn: { forward: [0, 1, 0], up: [0, 0, -1] },
  osirisrex: { forward: [0, 1, 0], up: [0, 0, -1] },
  ds1: { forward: [0, -1, 0], up: [0, 0, 1] },
  near: seenFromAbove([0, -1, 0], [0, 0, 1], NEAR_ELEVATION_DEG),
  // elev-1: Stardust keeps the hangar's frame, seen from above as its picture
  stardust: seenFromAbove([0, 0, 1], [0, 1, 0], STARDUST_ELEVATION_DEG),
  // Cassini, from its side: the dish (facing -Z) up, the magnetometer boom (+Y) to the upper left (AUDIT-C's hold on rest-1)
  cassini: { forward: [-1, 0, 0], up: [0, Math.sin(Math.PI / 6), -Math.cos(Math.PI / 6)] },
  vanallen: { forward: [0, -1, 0], up: [0, 0, 1] },
  // hangar-5, checked live on 21 Nov against the pictures (the 12:40 ruling): each seen from its front quarter on the
  // left, as the hangar's own picture camera looks (build_craft.py's view -0.55 across to 1 along the front, about
  // 28.8 degrees; its "a little above" not taken), so Deep Impact's camera opening sits at the front right with the dish
  // behind on the left, and the Viking Lander's dish at the upper left with its sampler arm to the lower right. Both keep
  // the hangar's up: the lander stands on its legs, its only pose
  deepimpact: { forward: FRONT_QUARTER, up: [0, 1, 0] },
  viking: { forward: FRONT_QUARTER, up: [0, 1, 0] },
  // as-2 (the 21 Nov 14:33 ruling), checked live on 22 Nov against NASA's render: the docked pair from its side, level,
  // Apollo's engine at the left and Soyuz at the right (+Z, its line of flight, along the screen's right), panels up
  apollosoyuz: { forward: [1, 0, 0], up: [0, 1, 0] },
  // ky-2 (the 22 Nov 05:53 ruling), checked live against its picture: upright, the aperture (+Z) up the screen, its
  // solar array's side (+Y) away from the camera as Spitzer's shield, turned about the aperture's axis so the white
  // panel faces the front right as in NASA's render, and seen a little from above
  kepler: seenFromAbove(
    [Math.sin((KEPLER_HEADING_DEG * Math.PI) / 180), Math.cos((KEPLER_HEADING_DEG * Math.PI) / 180), 0],
    [0, 0, 1],
    KEPLER_ELEVATION_DEG,
  ),
  // in-1 (the 22 Nov 22:18 ruling), checked live against NASA's render: on its four legs, the mast and solar panel up
  // (the hangar's up), the rotors spread, seen a little from above. From the front quarter on the right: the hangar's
  // FRONT_QUARTER (the left) showed the render's mirror image (the panel sloping up to the right), the right one
  // matches it (the panel sloping down to the right, the long blade reaching left, one blade hanging below)
  ingenuity: seenFromAbove([-FRONT_QUARTER[0], 0, FRONT_QUARTER[2]], [0, 1, 0], INGENUITY_ELEVATION_DEG),
  // pv-1 (the 23 Nov 03:38 ruling), checked live against perseverance_picker.webp (.cache/uiux/built/pv-1/zoom.png):
  // upright on its six wheels (+Y up), from the front quarter on the left at 0 deg (as Deep Impact and the Viking Lander:
  // CRAFT_FRAMING yFraction 0.62 already looks ~9 deg down onto the top deck; 15 deg pitched the MMRTG above the UHF antenna)
  perseverance: { forward: FRONT_QUARTER, up: [0, 1, 0] },
  // cl-1 (the 23 Nov 11:07 ruling), checked live against clementine_picker.webp (.cache/uiux/built/cl-1/zoom.png):
  // upright gold-foil prism with thruster (-Z) up and high-gain dish (+Z) below, solar cell faces (+Y) toward the camera
  // turned 12 deg (0 deg extra elevation) so the two round ports sit front-left and the sensor housing sits right of the cone
  clementine: {
    forward: [-Math.sin((CLEMENTINE_HEADING_DEG * Math.PI) / 180), -Math.cos((CLEMENTINE_HEADING_DEG * Math.PI) / 180), 0],
    up: [0, 0, -1],
  },
};

/**
 * The models framed by their body, not their whole model (the design lane's 18:35 ruling: framing by the body where the
 * record names one and the whole model shows a speck): the Van Allen Probes, whose wire antennas make the bounds 42 m.
 */
const FRAME_BY_BODY: ReadonlySet<string> = new Set(['vanallen']);

const AXES: Readonly<Record<string, Vec3>> = { '+X': [1, 0, 0], '-X': [-1, 0, 0], '+Y': [0, 1, 0], '-Y': [0, -1, 0], '+Z': [0, 0, 1], '-Z': [0, 0, -1] };

/** The hangar's entries as crafts, in the hangar's own order; an entry whose model or frame cannot be read is left out. */
export function craftsOfHangar(file: HangarFile, base = '/models/hangar/'): Craft[] {
  const forward = AXES[file.frame.forward];
  const up = AXES[file.frame.up];
  if (!forward || !up) return [];
  return file.entries.flatMap((entry) => {
    const model = file.models[entry.model];
    const lod1 = model?.files.lod1?.path;
    const lod2 = model?.files.lod2?.path ?? lod1;
    const picture = model?.files.picker?.path;
    if (!model || !lod1 || !lod2 || !picture || (model.kind !== 'craft' && model.kind !== 'suit')) return [];
    const t = entry.travel;
    const speed: CraftSpeed | null =
      t && t.relativeTo === 'Sun' && Number.isFinite(t.speedKmS) && t.speedKmS > 0 ? { kmS: t.speedKmS, relativeTo: 'the Sun', sourceUrl: t.page, asOf: t.asOf } : null;
    return [{ id: entry.id, name: entry.name, kind: model.kind, modelUrl: base + lod1, phoneModelUrl: base + lod2, pictureUrl: base + picture, modelForward: REST_ATTITUDE[entry.model]?.forward ?? forward, modelUp: REST_ATTITUDE[entry.model]?.up ?? up, speed, factKeys: (entry.facts ?? []).map((fact) => fact.key), frameByBodyRadiusM: FRAME_BY_BODY.has(entry.model) && model.bodyRadiusM && model.bodyRadiusM > 0 ? model.bodyRadiusM : null }];
  });
}

/** The hangar's crafts, in its own order. */
export const HANGAR_CRAFTS: readonly Craft[] = craftsOfHangar(hangar as HangarFile);

export const CRAFTS: Readonly<Record<CraftId, Craft>> = Object.fromEntries(HANGAR_CRAFTS.map((craft) => [craft.id, craft]));

/** The level a screen draws: the chase camera's on the laptop, the far or phone view's on the phone (the hangar's levels). */
export function craftModelUrl(craft: Craft, phone: boolean): string {
  return phone ? craft.phoneModelUrl : craft.modelUrl;
}

/** The craft a page address asks for (`?craft=voyager-1`), the interim way to choose one until the picker's record. */
export function craftFromSearch(search: string): Craft | null {
  const id = new URLSearchParams(search).get('craft');
  return id && Object.hasOwn(CRAFTS, id) ? CRAFTS[id]! : null;
}

/**
 * Where the craft sits and how large (INTERIM, from the design lane's UIUX-pilot-QUESTION recommendation 1: centred
 * across, its middle at 62 percent of the window's height, about 320 px across on the laptop).
 */
export const CRAFT_FRAMING = { xFraction: 0.5, yFraction: 0.62, diameterPx: 320 } as const;

/**
 * The rotation (row-major 3 by 3) that takes the model's own axes to the craft's axes in ICRS: its forward to
 * `forward`, its up to `up`, and its right (forward x up) to `right`.
 */
export function modelToIcrs(craft: Craft, right: Vec3, up: Vec3, forward: Vec3): number[] {
  const f = craft.modelForward;
  const u = craft.modelUp;
  const r: Vec3 = [f[1] * u[2] - f[2] * u[1], f[2] * u[0] - f[0] * u[2], f[0] * u[1] - f[1] * u[0]];
  // World = [right up forward] [r u f]^T
  const out: number[] = [];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) out.push(right[i]! * r[j]! + up[i]! * u[j]! + forward[i]! * f[j]!);
  }
  return out;
}
