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
}

interface HangarFile {
  readonly frame: { readonly forward: string; readonly up: string };
  readonly models: Readonly<Record<string, { readonly kind: string; readonly files: Readonly<Record<string, { readonly path: string }>> }>>;
  readonly entries: readonly {
    readonly id: string;
    readonly name: string;
    readonly model: string;
    readonly travel: { readonly speedKmS: number; readonly relativeTo: string; readonly asOf: string | null; readonly page: string } | null;
  }[];
}

/**
 * A craft's attitude at rest, by hangar model, where it is not the hangar's own frame (the design lane's rulings in
 * UIUX-pilot-READY.md): the model axis drawn along the way the camera looks (`forward`) and the one drawn up the screen.
 * - 13:32: Voyager stands as D26's render shows it, its long boom (the model's -X) straight down under the dish, its line
 *   of flight kept.
 * - 17:10: a craft that never travels rests as its picture stands it: Hubble's tube and Gemini's nose (the model's +Z,
 *   their long axis) up the screen, a quarter turn about the screen's horizontal axis from the line of flight, so the
 *   model's +Y, its picture's up, faces the camera.
 * Every other model keeps the hangar's frame: +Z its line of flight, +Y the up of its picker picture (build_craft.py
 * renders the pictures with the camera's up along it).
 */
const REST_ATTITUDE: Readonly<Record<string, { readonly forward?: Vec3; readonly up: Vec3 }>> = {
  voyager: { up: [1, 0, 0] },
  hubble: { forward: [0, -1, 0], up: [0, 0, 1] },
  gemini: { forward: [0, -1, 0], up: [0, 0, 1] },
};

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
    return [{ id: entry.id, name: entry.name, kind: model.kind, modelUrl: base + lod1, phoneModelUrl: base + lod2, pictureUrl: base + picture, modelForward: REST_ATTITUDE[entry.model]?.forward ?? forward, modelUp: REST_ATTITUDE[entry.model]?.up ?? up, speed }];
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
