import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { followRows, placeStar, resolveFindingParsecPosition, steerCamera, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { flightCameraOf, screenOfPlacement } from '../flightFrame.js';
import { steerStartOf } from '../starView.js';
import { CRAFTS, CRAFT_FRAMING, HANGAR_CRAFTS, NEAR_ELEVATION_DEG, STARDUST_ELEVATION_DEG, craftFromSearch, craftModelUrl, craftsOfHangar } from '../crafts.js';
import { craftFrameRadiusM, craftSceneOf, photoPinholeOf, restExtentM } from '../craftScene.js';

const W = 1440;
const H = 900;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' }).session;
}

/** Column-major 4 by 4 times a point. */
function apply(m: number[], p: Vec3): [number, number, number, number] {
  return [0, 1, 2, 3].map((r) => m[r]! * p[0] + m[4 + r]! * p[1] + m[8 + r]! * p[2] + m[12 + r]!) as [number, number, number, number];
}
const toScreen = (clip: [number, number, number, number]) => ({ xPx: ((clip[0] / clip[3] + 1) / 2) * W, yPx: ((1 - clip[1] / clip[3]) / 2) * H });

describe('craftSceneOf: a NASA model placed in the photo\'s own view', () => {
  it('lands the model\'s centre at the framing point through the same camera the stars use', () => {
    const session = there();
    const camera = flightCameraOf(session, W, H)!;
    const scene = craftSceneOf(camera, CRAFTS['voyager-1']!, [0.5, 3.4, 4.2], 10.8)!;
    const centre = apply(scene.modelMatrix, [0.5, 3.4, 4.2]);
    const s = toScreen(apply(scene.clipMatrix, [centre[0], centre[1], centre[2]]));
    expect(Math.abs(s.xPx - CRAFT_FRAMING.xFraction * W)).toBeLessThan(1e-6);
    expect(Math.abs(s.yPx - CRAFT_FRAMING.yFraction * H)).toBeLessThan(1e-6);
    expect(scene.near).toBeLessThan(Math.hypot(centre[0], centre[1], centre[2]) - 10.8);
    expect(scene.far).toBeGreaterThan(Math.hypot(centre[0], centre[1], centre[2]) + 10.8);
  });

  it('points the model\'s own forward axis at the star flown to', () => {
    const session = there();
    const camera = flightCameraOf(session, W, H)!;
    const craft = CRAFTS['voyager-1']!;
    const scene = craftSceneOf(camera, craft, [0, 0, 0], 10)!;
    const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
    const target = resolveFindingParsecPosition(picked)!;
    const o = camera.observerPc;
    const d = Math.hypot(target[0] - o[0], target[1] - o[1], target[2] - o[2]);
    const want = [(target[0] - o[0]) / d, (target[1] - o[1]) / d, (target[2] - o[2]) / d];
    const tip = apply(scene.modelMatrix, craft.modelForward);
    const base = apply(scene.modelMatrix, [0, 0, 0]);
    const got = [tip[0] - base[0], tip[1] - base[1], tip[2] - base[2]];
    const n = Math.hypot(got[0]!, got[1]!, got[2]!);
    for (let k = 0; k < 3; k++) expect(got[k]! / n).toBeCloseTo(want[k]!, 9);
  });

  it('lights it from the Sun\'s real direction, from where the camera stands', () => {
    const session = there();
    const camera = flightCameraOf(session, W, H)!;
    const scene = craftSceneOf(camera, CRAFTS['astronaut-emu']!, [0, 0, 0], 50)!;
    const o = camera.observerPc;
    const r = Math.hypot(o[0], o[1], o[2]);
    for (let k = 0; k < 3; k++) expect(scene.toSun[k]).toBeCloseTo(-o[k]! / r, 12);
  });

  it('has no scene once the camera stands at the star itself', () => {
    const camera = flightCameraOf(there(), W, H)!;
    expect(craftSceneOf({ ...camera, observerPc: camera.referencePc }, CRAFTS['voyager-1']!, [0, 0, 0], 10)).toBeNull();
  });

  it('while steered, frames the craft at the same point and points it where the person looks', () => {
    const camera = flightCameraOf(there(), W, H)!;
    const start = steerStartOf(camera);
    const steer = steerCamera({ ...start, speedPcPerS: 0.3 }, { yawRadPerS: 0.4, pitchRadPerS: 0.2 }, 0.5);
    const scene = craftSceneOf(camera, CRAFTS['voyager-1']!, [0, 0, 0], 10, steer)!;
    const centre = apply(scene.modelMatrix, [0, 0, 0]);
    const s = toScreen(apply(scene.clipMatrix, [centre[0], centre[1], centre[2]]));
    expect(Math.abs(s.xPx - CRAFT_FRAMING.xFraction * W)).toBeLessThan(1e-6);
    expect(Math.abs(s.yPx - CRAFT_FRAMING.yFraction * H)).toBeLessThan(1e-6);
    const tip = apply(scene.modelMatrix, CRAFTS['voyager-1']!.modelForward);
    const got = [tip[0] - centre[0], tip[1] - centre[1], tip[2] - centre[2]];
    const n = Math.hypot(got[0]!, got[1]!, got[2]!);
    for (let k = 0; k < 3; k++) expect(got[k]! / n).toBeCloseTo(steer.rows.z[k]!, 9);
  });

  it('leads off the centre in a turn while the chase camera follows, and comes back as it catches up (MOTION k13)', () => {
    const camera = flightCameraOf(there(), W, H)!;
    const start = steerStartOf(camera);
    let craft = { ...start, speedPcPerS: 0 };
    let chase = start.rows;
    // A steady turn to the right at 1 radian a second for a second, the camera following at 60 frames a second
    for (let i = 0; i < 60; i++) {
      craft = steerCamera(craft, { yawRadPerS: 1, pitchRadPerS: 0 }, 1 / 60);
      chase = followRows(chase, craft.rows, 1 / 60);
    }
    const at = (rows: typeof chase) => {
      const scene = craftSceneOf(camera, CRAFTS['voyager-1']!, [0, 0, 0], 10, { positionPc: craft.positionPc, rows, craftRows: craft.rows })!;
      const c = apply(scene.modelMatrix, [0, 0, 0]);
      return toScreen(apply(scene.clipMatrix, [c[0], c[1], c[2]]));
    };
    const turning = at(chase);
    expect(turning.xPx - CRAFT_FRAMING.xFraction * W).toBeGreaterThan(0.05 * W);
    // The turn ends; a second later the camera has caught up and the craft is back at its place
    for (let i = 0; i < 60; i++) chase = followRows(chase, craft.rows, 1 / 60);
    const settled = at(chase);
    expect(Math.abs(settled.xPx - CRAFT_FRAMING.xFraction * W)).toBeLessThan(0.01 * W);
    expect(Math.abs(settled.yPx - CRAFT_FRAMING.yFraction * H)).toBeLessThan(0.01 * H);
  });

  it('agrees with the star layer\'s camera on where a far point lands', () => {
    const session = there();
    const camera = flightCameraOf(session, W, H)!;
    const pin = photoPinholeOf(camera);
    const placement = { kind: 'sky_sphere' as const, direction: pin.rows.z as Vec3, reason: 'no_usable_distance' as const };
    const star = screenOfPlacement({ ...camera, wcs: { ...camera.wcs, radialDistortionK1: 0, radialDistortionK2: 0 } }, placement)!;
    const scene = craftSceneOf(camera, CRAFTS.jwst!, [0, 0, 0], 17)!;
    const s = toScreen(apply(scene.clipMatrix, [pin.rows.z[0] * 5, pin.rows.z[1] * 5, pin.rows.z[2] * 5]));
    expect(Math.abs(s.xPx - star.xPx)).toBeLessThan(1e-6);
    expect(Math.abs(s.yPx - star.yPx)).toBeLessThan(1e-6);
  });
});

describe('craftFromSearch: the interim choice by page address', () => {
  it('takes only the crafts the hangar lists', () => {
    expect(craftFromSearch('?craft=voyager-1')?.id).toBe('voyager-1');
    expect(craftFromSearch('?craft=astronaut-emu&x=1')?.id).toBe('astronaut-emu');
    expect(craftFromSearch('?craft=enterprise')).toBeNull();
    // Names every object has are not crafts
    expect(craftFromSearch('?craft=toString')).toBeNull();
    expect(craftFromSearch('?craft=constructor')).toBeNull();
    expect(craftFromSearch('')).toBeNull();
  });

  it('gives a real speed only for a craft that travels between stars, with its NASA page and date', () => {
    expect(CRAFTS['voyager-1']!.speed).toEqual({ kmS: 17.0, relativeTo: 'the Sun', sourceUrl: 'https://science.nasa.gov/mission/voyager/voyager-1/', asOf: '2024-08-21' });
    expect(CRAFTS.jwst!.speed).toBeNull();
    expect(CRAFTS['astronaut-emu']!.speed).toBeNull();
    // A page that gives no date for its figure: none is claimed
    expect(CRAFTS['new-horizons']!.speed).toEqual({ kmS: 15.3, relativeTo: 'the Sun', sourceUrl: 'https://science.nasa.gov/mission/new-horizons/', asOf: null });
  });

  it('reads each entry\'s model in the hangar\'s own frame, the chase level on the laptop and the far level on the phone', () => {
    const v = CRAFTS['voyager-1']!;
    expect(v.modelUrl).toBe('/models/hangar/voyager/voyager_lod1.glb');
    expect(v.phoneModelUrl).toBe('/models/hangar/voyager/voyager_lod2.glb');
    expect(v.pictureUrl).toBe('/models/hangar/voyager/voyager_picker.webp');
    expect(v.modelForward).toEqual([0, 0, 1]);
    // D26's rest pose as the design lane ruled (13:32): Voyager stands with its long boom (the model's -X) straight down
    expect(v.modelUp).toEqual([1, 0, 0]);
    expect(CRAFTS['voyager-2']!.modelUp).toEqual([1, 0, 0]);
    // The 17:10 ruling: Hubble and Gemini, which never travel, rest as their pictures stand them: their long axis (the
    // model's +Z) up the screen, a quarter turn about the screen's horizontal axis from their line of flight
    for (const id of ['hubble', 'gemini']) {
      expect(CRAFTS[id]!.modelUp).toEqual([0, 0, 1]);
      expect(CRAFTS[id]!.modelForward).toEqual([0, -1, 0]);
    }
    // Every other entry keeps its line of flight (+Z) and the side up that is up in its picker picture (+Y)
    // The 17:24 ruling, checked live against the pictures (hangar-2b): Spitzer's tube up with its sunshield behind it
    // (the turn the other way round, so the shield's side faces away), Dawn's wings straight across with their faces
    // to the camera; Juno, MESSENGER, OSIRIS-REx and MRO already stand as their pictures do in the hangar's frame
    expect([CRAFTS.spitzer!.modelForward, CRAFTS.spitzer!.modelUp]).toEqual([[0, 1, 0], [0, 0, 1]]);
    // hangar-2c, the design lane's 17:56 read: Dawn and OSIRIS-REx turned the other way round, so the faces with their
    // cells (Dawn) and the panels' faces under the dish on top (OSIRIS-REx) show, as their pictures
    expect([CRAFTS.dawn!.modelForward, CRAFTS.dawn!.modelUp]).toEqual([[0, 1, 0], [0, 0, -1]]);
    expect([CRAFTS['osiris-rex']!.modelForward, CRAFTS['osiris-rex']!.modelUp]).toEqual([[0, 1, 0], [0, 0, -1]]);
    // rest-1, the live check of 21 Nov against each picture (the 17:58, 18:05 and 18:35 rulings): Deep Space 1's wings
    // face the camera across, NEAR's panels above its body with the engine end at the foot, Cassini's dish on top over
    // its body (a half turn about its line of flight), the Van Allen Probes' octagon facing the camera, panels in a cross
    expect([CRAFTS['deep-space-1']!.modelForward, CRAFTS['deep-space-1']!.modelUp]).toEqual([[0, -1, 0], [0, 0, 1]]);
    // elev-1, the 13:35 ruling: NEAR seen from above by its picture's elevation, its heading kept (below)
    expect(CRAFTS['near-shoemaker']!.modelForward[0]).toBe(0);
    // Cassini (AUDIT-C's hold on rest-1): seen from its side, the dish (facing the model's -Z) faces up and the
    // magnetometer boom (the model's +Y) reaches to the upper left, the screen's up tilted 30 degrees toward the boom
    expect(CRAFTS.cassini!.modelForward).toEqual([-1, 0, 0]);
    expect(CRAFTS.cassini!.modelUp[0]).toBe(0);
    expect(CRAFTS.cassini!.modelUp[1]).toBeCloseTo(0.5, 12);
    expect(CRAFTS.cassini!.modelUp[2]).toBeCloseTo(-Math.sqrt(3) / 2, 12);
    expect([CRAFTS['van-allen-probes']!.modelForward, CRAFTS['van-allen-probes']!.modelUp]).toEqual([[0, -1, 0], [0, 0, 1]]);
    // elev-1 (the 13:35 ruling): Stardust and NEAR Shoemaker rest seen from above by their NASA pictures' elevation,
    // found on the live sheet: the camera's look and the screen's up turned together about the screen's horizontal
    // axis, the heading (the look's part across the craft's built up) kept, so the craft's top tilts toward the camera
    const fromAbove: Record<string, [Vec3, Vec3, number]> = {
      stardust: [[0, 0, 1], [0, 1, 0], STARDUST_ELEVATION_DEG],
      'near-shoemaker': [[0, -1, 0], [0, 0, 1], NEAR_ELEVATION_DEG],
    };
    for (const [id, [f, u, deg]] of Object.entries(fromAbove)) {
      const e = (deg * Math.PI) / 180;
      const want = [f.map((x, i) => Math.cos(e) * x - Math.sin(e) * u[i]!), f.map((x, i) => Math.sin(e) * x + Math.cos(e) * u[i]!)];
      const got = [CRAFTS[id]!.modelForward, CRAFTS[id]!.modelUp];
      got.forEach((v, k) => v.forEach((x, i) => expect(x).toBeCloseTo(want[k]![i]!, 12)));
      // Seen from above: the look runs down the craft's built up, the screen's up away from the camera
      expect(CRAFTS[id]!.modelForward.reduce((a, x, i) => a + x * u[i]!, 0)).toBeCloseTo(-Math.sin(e), 12);
      expect(CRAFTS[id]!.modelUp.reduce((a, x, i) => a + x * f[i]!, 0)).toBeCloseTo(Math.sin(e), 12);
    }
    // The angles kept from the sheet (.cache/uiux/built/elev-1/): Stardust 45, NEAR 15
    expect([STARDUST_ELEVATION_DEG, NEAR_ELEVATION_DEG]).toEqual([45, 15]);
    // hangar-5, the live check of 21 Nov against each picture (the 12:40 ruling): each seen from its front quarter on
    // the left, level, as the hangar's picture camera looks (about 28.8 degrees off its front), the hangar's up kept, so
    // the Viking Lander stands on its legs
    for (const id of ['deep-impact', 'viking-lander']) {
      const [fx, fy, fz] = CRAFTS[id]!.modelForward;
      expect(CRAFTS[id]!.modelUp).toEqual([0, 1, 0]);
      expect([fy, Math.hypot(fx, fy, fz)]).toEqual([0, expect.closeTo(1, 12)]);
      expect([fz < 0, (Math.atan2(fx, -fz) * 180) / Math.PI]).toEqual([true, expect.closeTo(28.81, 2)]);
    }
    const turned = new Set(['hubble', 'gemini', 'spitzer', 'dawn', 'osiris-rex', 'deep-space-1', 'near-shoemaker', 'cassini', 'van-allen-probes', 'stardust', 'deep-impact', 'viking-lander']);
    const others = Object.values(CRAFTS).filter((c) => !c.id.startsWith('voyager') && !turned.has(c.id));
    expect(['juno', 'messenger', 'mro', 'mars-global-surveyor', 'maven', 'ibex'].every((id) => others.some((c) => c.id === id))).toBe(true);
    expect(others.every((c) => c.modelUp.join() === '0,1,0' && c.modelForward.join() === '0,0,1')).toBe(true);
    expect(CRAFTS['voyager-1']!.modelForward).toEqual([0, 0, 1]);
    expect(v.kind).toBe('craft');
    expect(CRAFTS['astronaut-emu']!.kind).toBe('suit');
    // Every hangar entry, however many the hangar holds
    expect(Object.keys(CRAFTS)).toHaveLength(HANGAR_CRAFTS.length);
    expect(HANGAR_CRAFTS.length).toBeGreaterThanOrEqual(17);
    expect(craftModelUrl(v, false)).toBe(v.modelUrl);
    expect(craftModelUrl(v, true)).toBe(v.phoneModelUrl);
  });

  it('frames the craft at the point and size a screen gives it, its bounding sphere that many pixels across', () => {
    const session = there();
    const camera = flightCameraOf(session, 390, 844)!;
    const framing = { xPx: 195, yPx: 298, diameterPx: 184 };
    const scene = craftSceneOf(camera, CRAFTS['voyager-1']!, [0, 0, 0], 10, undefined, framing)!;
    const centre = apply(scene.modelMatrix, [0, 0, 0]);
    const at = (p: Vec3) => {
      const c = apply(scene.clipMatrix, p);
      return { xPx: ((c[0] / c[3] + 1) / 2) * 390, yPx: ((1 - c[1] / c[3]) / 2) * 844 };
    };
    const s = at([centre[0], centre[1], centre[2]]);
    expect(Math.abs(s.xPx - 195)).toBeLessThan(1e-6);
    expect(Math.abs(s.yPx - 298)).toBeLessThan(1e-6);
    // The sphere's edge seen square to the line of sight is half the diameter from the centre on screen (within the
    // pinhole's small off-axis stretch)
    const d = Math.hypot(centre[0], centre[1], centre[2]);
    const pin = photoPinholeOf(camera);
    const r = pin.rows.x;
    const edge = at([centre[0] + r[0] * 10 * (d / Math.sqrt(d * d - 100)), centre[1] + r[1] * 10 * (d / Math.sqrt(d * d - 100)), centre[2] + r[2] * 10 * (d / Math.sqrt(d * d - 100))]);
    expect(Math.hypot(edge.xPx - s.xPx, edge.yPx - s.yPx)).toBeGreaterThan(92 * 0.9);
    expect(Math.hypot(edge.xPx - s.xPx, edge.yPx - s.yPx)).toBeLessThan(92 * 1.1);
  });

  it('measures a craft at rest by its extent across its own right and up, not its bounding sphere (D30 item 1)', () => {
    // A model 1 m wide, 10 m tall and 2 m deep along its own forward (-y here is forward, z up): seen from behind,
    // the widest it looks is its height along z
    const craft = { ...CRAFTS['voyager-1']!, modelForward: [0, -1, 0] as Vec3, modelUp: [0, 0, 1] as Vec3 };
    expect(restExtentM(craft, [1, 2, 10])).toBeCloseTo(10, 12);
    expect(restExtentM(craft, [12, 2, 3])).toBeCloseTo(12, 12);
    // Its depth along forward never counts
    expect(restExtentM(craft, [1, 50, 3])).toBeCloseTo(3, 12);
  });

  it('frames that extent, not the sphere, at the screen\'s size: a 10 m mast inside a 20 m sphere spans the 184 px', () => {
    const session = there();
    const camera = flightCameraOf(session, 390, 844)!;
    const framing = { xPx: 195, yPx: 298, diameterPx: 184 };
    const scene = craftSceneOf(camera, CRAFTS['voyager-1']!, [0, 0, 0], 10, undefined, framing, 5)!;
    const centre = apply(scene.modelMatrix, [0, 0, 0]);
    const at = (p: Vec3) => {
      const c = apply(scene.clipMatrix, p);
      return { xPx: ((c[0] / c[3] + 1) / 2) * 390, yPx: ((1 - c[1] / c[3]) / 2) * 844 };
    };
    const pin = photoPinholeOf(camera);
    const u = pin.rows.y;
    const top = at([centre[0] + u[0] * 5, centre[1] + u[1] * 5, centre[2] + u[2] * 5]);
    const foot = at([centre[0] - u[0] * 5, centre[1] - u[1] * 5, centre[2] - u[2] * 5]);
    // "About 184 px": the mast stands along the photo's up, a little foreshortened off the photo's axis (172 px here)
    expect(Math.hypot(top.xPx - foot.xPx, top.yPx - foot.yPx)).toBeGreaterThan(184 * 0.9);
    expect(Math.hypot(top.xPx - foot.xPx, top.yPx - foot.yPx)).toBeLessThan(184 * 1.1);
    // The near plane still clears the whole sphere
    expect(scene.near).toBeLessThan(Math.hypot(centre[0], centre[1], centre[2]) - 10);
  });

  it('places a flat extent on the photo\'s axis exactly the screen\'s size across, where the sphere rule falls short (frame-2)', () => {
    const session = there();
    const camera = flightCameraOf(session, 390, 844)!;
    const pin = photoPinholeOf(camera);
    // The photo's own axis on the window, so nothing is foreshortened: the mast stands square to the line of sight
    const axis = screenOfPlacement(camera, placeStar(pin.rows.z, null))!;
    const framing = { xPx: axis.xPx, yPx: axis.yPx, diameterPx: 184 };
    const scene = craftSceneOf(camera, CRAFTS['voyager-1']!, [0, 0, 0], 10, undefined, framing, 5)!;
    const centre = apply(scene.modelMatrix, [0, 0, 0]);
    const at = (p: Vec3) => {
      const c = apply(scene.clipMatrix, p);
      return { xPx: ((c[0] / c[3] + 1) / 2) * 390, yPx: ((1 - c[1] / c[3]) / 2) * 844 };
    };
    const u = pin.rows.y;
    const top = at([centre[0] + u[0] * 5, centre[1] + u[1] * 5, centre[2] + u[2] * 5]);
    const foot = at([centre[0] - u[0] * 5, centre[1] - u[1] * 5, centre[2] - u[2] * 5]);
    expect(Math.hypot(top.xPx - foot.xPx, top.yPx - foot.yPx)).toBeCloseTo(184, 3);
  });

  it('stands Voyager at rest with its boom straight down the screen and its dish above it (the 13:32 ruling)', () => {
    const session = there();
    const camera = flightCameraOf(session, 390, 844)!;
    const scene = craftSceneOf(camera, CRAFTS['voyager-1']!, [0, 0, 0], 10, undefined, { xPx: 195, yPx: 298, diameterPx: 184 }, 8)!;
    const toScreen844 = (p: Vec3) => {
      const c = apply(scene.clipMatrix, p);
      return { xPx: ((c[0] / c[3] + 1) / 2) * 390, yPx: ((1 - c[1] / c[3]) / 2) * 844 };
    };
    const at = (m: Vec3) => {
      const w = apply(scene.modelMatrix, m);
      return toScreen844([w[0], w[1], w[2]]);
    };
    const dish = at([0, 0, 0]);
    const boomEnd = at([-12, 0, 0]);
    expect(boomEnd.yPx - dish.yPx).toBeGreaterThan(100);
    expect(Math.abs(boomEnd.xPx - dish.xPx)).toBeLessThan(0.15 * (boomEnd.yPx - dish.yPx));
  });

  it('takes a travel speed only relative to the Sun and above zero, else claims none (AUDIT-C on hangar-1)', () => {
    // A hangar file written by hand (typed test data): one model, entries whose travel varies
    const travel = (speedKmS: number, relativeTo: string) => ({ speedKmS, relativeTo, asOf: '2024-01-01', page: 'https://science.nasa.gov/' });
    const file = {
      frame: { forward: '+Z', up: '+Y' },
      models: { m: { kind: 'craft', files: { lod1: { path: 'm/a.glb' }, lod2: { path: 'm/b.glb' }, picker: { path: 'm/p.webp' } } } },
      entries: [
        { id: 'sun', name: 'Sun-relative', model: 'm', travel: travel(15.3, 'Sun') },
        { id: 'earth', name: 'Earth-relative', model: 'm', travel: travel(7.7, 'Earth') },
        { id: 'zero', name: 'Zero', model: 'm', travel: travel(0, 'Sun') },
        { id: 'negative', name: 'Negative', model: 'm', travel: travel(-3, 'Sun') },
        { id: 'nan', name: 'Not a number', model: 'm', travel: travel(Number.NaN, 'Sun') },
        { id: 'infinite', name: 'Infinite', model: 'm', travel: travel(Number.POSITIVE_INFINITY, 'Sun') },
        { id: 'none', name: 'None', model: 'm', travel: null },
      ],
    };
    const crafts = craftsOfHangar(file);
    expect(crafts.map((c) => [c.id, c.speed?.kmS ?? null])).toEqual([['sun', 15.3], ['earth', null], ['zero', null], ['negative', null], ['nan', null], ['infinite', null], ['none', null]]);
    expect(crafts[0]!.speed).toEqual({ kmS: 15.3, relativeTo: 'the Sun', sourceUrl: 'https://science.nasa.gov/', asOf: '2024-01-01' });
  });

  it('stands Hubble at rest with its tube up the screen and its panels lying back from it, not seen end-on (the 17:10 ruling)', () => {
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    const scene = craftSceneOf(camera, CRAFTS.hubble!, [0, 0, 0], 10, undefined, undefined, 6)!;
    const at = (m: Vec3) => {
      const w = apply(scene.modelMatrix, m);
      const c = apply(scene.clipMatrix, [w[0], w[1], w[2]]);
      return { xPx: ((c[0] / c[3] + 1) / 2) * 1440, yPx: ((1 - c[1] / c[3]) / 2) * 900 };
    };
    const centre = at([0, 0, 0]);
    const tubeEnd = at([0, 0, 6]);
    const panelEnd = at([0, 6, 0]);
    // The tube (+Z) runs up the screen, nearly straight
    expect(centre.yPx - tubeEnd.yPx).toBeGreaterThan(100);
    expect(Math.abs(tubeEnd.xPx - centre.xPx)).toBeLessThan(0.15 * (centre.yPx - tubeEnd.yPx));
    // The panels' axis (+Y) runs along the line of sight, so it moves far less on screen than the tube
    expect(Math.hypot(panelEnd.xPx - centre.xPx, panelEnd.yPx - centre.yPx)).toBeLessThan(0.35 * (centre.yPx - tubeEnd.yPx));
  });

  it('stands Spitzer at rest with its tube up the screen, as its picture does (hangar-2b)', () => {
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    const scene = craftSceneOf(camera, CRAFTS.spitzer!, [0, 0, 0], 4, undefined, undefined, 2.5)!;
    const at = (m: Vec3) => {
      const w = apply(scene.modelMatrix, m);
      const c = apply(scene.clipMatrix, [w[0], w[1], w[2]]);
      return { xPx: ((c[0] / c[3] + 1) / 2) * 1440, yPx: ((1 - c[1] / c[3]) / 2) * 900 };
    };
    const centre = at([0, 0, 0]);
    const top = at([0, 0, 2.5]);
    expect(centre.yPx - top.yPx).toBeGreaterThan(100);
    expect(Math.abs(top.xPx - centre.xPx)).toBeLessThan(0.15 * (centre.yPx - top.yPx));
  });

  it('frames the Van Allen Probes by their body, not their wire antennas, as the 18:35 ruling says; every other craft by its whole model', () => {
    const vap = CRAFTS['van-allen-probes']!;
    expect(vap.frameByBodyRadiusM).toBe(2.5);
    // Their bounds are 42 m across with the wires: the body's 2.5 m radius frames them instead
    expect(craftFrameRadiusM(vap, [42.4, 13.3, 42.4], 30)).toBe(2.5);
    const others = Object.values(CRAFTS).filter((c) => c.id !== 'van-allen-probes');
    expect(others.every((c) => c.frameByBodyRadiusM === null)).toBe(true);
    expect(craftFrameRadiusM(CRAFTS['voyager-1']!, [16.4, 7.5, 10.7], 11)).toBeCloseTo(restExtentM(CRAFTS['voyager-1']!, [16.4, 7.5, 10.7]) / 2, 12);
    expect(craftFrameRadiusM(CRAFTS['voyager-1']!, [0, 0, 0], 11)).toBe(11);
  });

  it('rests Cassini with its dish facing up and its boom reaching to the upper left, as ruled (AUDIT-C\'s hold on rest-1)', () => {
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    const scene = craftSceneOf(camera, CRAFTS.cassini!, [0, 0, 0], 12, undefined, undefined, 6)!;
    const at = (m: Vec3) => {
      const w = apply(scene.modelMatrix, m);
      const c = apply(scene.clipMatrix, [w[0], w[1], w[2]]);
      return { xPx: ((c[0] / c[3] + 1) / 2) * 1440, yPx: ((1 - c[1] / c[3]) / 2) * 900 };
    };
    const centre = at([0, 0, 0]);
    const boomEnd = at([0, 11, 0]);
    const dishFace = at([0, 0, -3]);
    // The boom: to the left and up
    expect(boomEnd.xPx).toBeLessThan(centre.xPx - 50);
    expect(boomEnd.yPx).toBeLessThan(centre.yPx - 20);
    // The dish's facing: up the screen more than sideways (the 30 degree tilt that raises the boom leans it a little right)
    expect(centre.yPx - dishFace.yPx).toBeGreaterThan(Math.abs(dishFace.xPx - centre.xPx));
  });
});

