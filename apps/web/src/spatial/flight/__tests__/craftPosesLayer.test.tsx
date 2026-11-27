// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer, type CraftRenderer } from '../FlightCraftLayer.js';
import { ALIVE_AT_REST, type AliveSignals } from '../craftAlive.js';
import { SUIT_POSES } from '../craftPoses.js';
import { CRAFTS } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';
import { steerStartOf } from '../starView.js';

// craft-alive-two: while steered, the suit's poses blended by the same signals that lean and bank it, put on its bones
// before each render; at rest (not steered) the stance its file was modelled in; the layer says the weights it drew.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

/** The knee's turn about x, degrees, at each render: what the renderer would draw. */
function recordingRenderer(bone: THREE.Bone) {
  const drawn: number[] = [];
  const makeRenderer = (): CraftRenderer => ({
    setPixelRatio: () => {},
    setSize: () => {},
    setClearColor: () => {},
    clear: () => {},
    dispose: () => {},
    render: () => {
      drawn.push((2 * Math.atan2(bone.quaternion.x, bone.quaternion.w) * 180) / Math.PI);
    },
  });
  return { drawn, makeRenderer };
}

/** A suit of one box on one knee bone, its file carrying the three poses (hover 10, cruise 40, brake -30 degrees). */
function suit(withPoses = true) {
  const bone = new THREE.Bone();
  bone.name = 'knee';
  const load = async () => {
    const g = new THREE.Group();
    g.add(bone, new THREE.Mesh(new THREE.BoxGeometry(0.5, 1.8, 0.4), new THREE.MeshStandardMaterial()));
    const deg = { hover: 10, cruise: 40, brake: -30 } as Record<string, number>;
    g.animations = withPoses
      ? SUIT_POSES.map((name) => {
          const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), (deg[name]! * Math.PI) / 180);
          return new THREE.AnimationClip(name, 0, [new THREE.QuaternionKeyframeTrack('knee.quaternion', [0], [q.x, q.y, q.z, q.w])]);
        })
      : [];
    return g;
  };
  return { bone, load };
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('the suit posed by the alive signals', () => {
  const camera = flightCameraOf(there(), 1440, 900)!;
  const rows: WcsCameraRows = steerStartOf(camera).rows;
  const at: Vec3 = camera.observerPc;

  async function mount(withPoses = true) {
    const { bone, load } = suit(withPoses);
    const { drawn, makeRenderer } = recordingRenderer(bone);
    const drawRef: { current: ((s: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows; alive?: AliveSignals }) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer, drawRef })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    const layer = container.querySelector('[data-testid="flight-craft"]')!;
    const step = (alive?: AliveSignals) => {
      act(() => drawRef.current!({ positionPc: at, rows, craftRows: rows, ...(alive ? { alive } : {}) }));
      return { angle: drawn.at(-1)!, poses: layer.getAttribute('data-poses') };
    };
    return { drawn, layer, step };
  }

  it('stands in its modelled stance at rest, before any steering', async () => {
    const { drawn, layer } = await mount();
    expect(layer.getAttribute('data-state')).toBe('drawn');
    expect(drawn.at(-1)!).toBeCloseTo(0, 6);
    expect(layer.getAttribute('data-poses')).toBe('rest');
  });

  it('hovers when steered at rest, cruises with speed and brakes on the brake, each frame before it is drawn', async () => {
    const { step } = await mount();
    const hover = step(ALIVE_AT_REST);
    expect(hover.angle).toBeCloseTo(10, 4);
    expect(hover.poses).toBe('hover 1.00 cruise 0.00 brake 0.00');
    const cruise = step({ ...ALIVE_AT_REST, speed: 1 });
    expect(cruise.angle).toBeCloseTo(40, 4);
    expect(cruise.poses).toBe('hover 0.00 cruise 1.00 brake 0.00');
    const brake = step({ ...ALIVE_AT_REST, speed: 1, brake: 1 });
    expect(brake.angle).toBeCloseTo(-30, 4);
    expect(brake.poses).toBe('hover 0.00 cruise 0.00 brake 1.00');
  });

  it('goes back to its modelled stance when a frame is drawn without the signals', async () => {
    const { step } = await mount();
    step({ ...ALIVE_AT_REST, speed: 1 });
    const plain = step(undefined);
    expect(plain.angle).toBeCloseTo(0, 6);
    expect(plain.poses).toBe('rest');
  });

  it('draws a model whose file has no poses as before, saying so', async () => {
    const { step, layer } = await mount(false);
    expect(layer.getAttribute('data-poses')).toBe('none');
    expect(step({ ...ALIVE_AT_REST, speed: 1 }).poses).toBe('none');
  });
});
