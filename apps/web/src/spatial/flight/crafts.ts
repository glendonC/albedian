import type { Vec3 } from '@albedian/camera-rig';

/**
 * pilot-1's crafts: NASA's own public-domain 3D models (NASA 3D Resources, listed in public/models/PROVENANCE.json),
 * each with the model's own axes for its heading and its up, and its real speed where it really travels, read at
 * NASA's own page and labelled with that page and date. A craft that does not travel between stars has no speed and
 * so no real travel time. The words people read for these come from the design lane (UIUX-pilot); none are here.
 */

export type CraftId = 'voyager-1' | 'jwst' | 'astronaut';

export interface CraftSpeed {
  readonly kmS: number;
  readonly relativeTo: 'the Sun';
  /** The page the figure was read on, and the date the page gives for it. */
  readonly sourceUrl: string;
  readonly asOf: string;
}

export interface Craft {
  readonly id: CraftId;
  readonly modelUrl: string;
  /** In the model's own axes (glTF, +y up as exported): the way the craft flies, and its up. */
  readonly modelForward: Vec3;
  readonly modelUp: Vec3;
  readonly speed: CraftSpeed | null;
}

export const CRAFTS: Readonly<Record<CraftId, Craft>> = {
  // The dish (+y) faces Earth, so flying away from Earth the craft moves along -y [verified: the thin disc of BODY.000
  // spans x and z at y from -1.19 to 2.12, the bus below it]
  'voyager-1': {
    id: 'voyager-1',
    modelUrl: '/models/nasa/voyager-probe-b.glb',
    modelForward: [0, -1, 0],
    modelUp: [0, 0, 1],
    // [verified] "moving at a velocity of 38,026.79 mph (17.0 km/second) relative to the Sun", as of 21 August 2024
    speed: { kmS: 17.0, relativeTo: 'the Sun', sourceUrl: 'https://science.nasa.gov/mission/voyager/voyager-1/', asOf: '2024-08-21' },
  },
  // Webb stays near Earth at the Sun-Earth L2 point: no real travel time between stars
  jwst: {
    id: 'jwst',
    modelUrl: '/models/nasa/james-webb-space-telescope-a.glb',
    modelForward: [0, 1, 0],
    modelUp: [-1, 0, 0],
    speed: null,
  },
  // A spacesuit is worn near a spacecraft, never flown between stars: no real travel time [verified: the visor faces +z]
  astronaut: {
    id: 'astronaut',
    modelUrl: '/models/nasa/astronaut.glb',
    modelForward: [0, 0, 1],
    modelUp: [0, 1, 0],
    speed: null,
  },
};

/** The craft a page address asks for (`?craft=voyager-1`), the interim way to choose one until the picker's record. */
export function craftFromSearch(search: string): Craft | null {
  const id = new URLSearchParams(search).get('craft');
  return id && Object.hasOwn(CRAFTS, id) ? CRAFTS[id as CraftId] : null;
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
