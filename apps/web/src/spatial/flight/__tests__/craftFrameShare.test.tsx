// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer, type CraftRenderer } from '../FlightCraftLayer.js';
import { CRAFTS, CRAFT_FRAMING } from '../crafts.js';
import { laptopCraftFraming, laptopFrameShare, phoneCraftFraming } from '../craftScene.js';
import { flightCameraOf } from '../flightFrame.js';

// craft-frame-share-one (DECIDED-BY-LEAD 27 Nov 12:04, on LOOK's film-size check): the craft keeps the laptop's approved
// frame on larger windows: its framed size grows with the window from the laptop's size up, by the smaller of the
// width's and the height's ratio to 1440 by 900, never smaller than now and unchanged on the phone; the level of detail
// follows the drawn size.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
}

describe('the laptop\'s framed size by the window', () => {
  it('is the approved 320 px at 1440 by 900 and grows by the smaller of the two ratios above it', () => {
    expect(laptopFrameShare(1440, 900)).toBe(1);
    expect(laptopFrameShare(1920, 1080)).toBeCloseTo(1.2, 12);
    expect(laptopFrameShare(2560, 1080)).toBeCloseTo(1.2, 12);
    expect(laptopFrameShare(3840, 2160)).toBeCloseTo(2.4, 12);
    expect(laptopFrameShare(1512, 982)).toBeCloseTo(1.05, 12);
    expect(laptopFrameShare(2160, 1000)).toBeCloseTo(1000 / 900, 12);
  });

  it('is never smaller than now: a narrower, shorter or taller window keeps 320 px', () => {
    expect(laptopFrameShare(1280, 800)).toBe(1);
    expect(laptopFrameShare(1440, 1200)).toBe(1);
    expect(laptopFrameShare(1200, 1600)).toBe(1);
    expect(laptopFrameShare(2560, 880)).toBe(1);
    expect(laptopFrameShare(1024, 768)).toBe(1);
  });

  it('frames the craft at that size, its place and its frame\'s height as before', () => {
    const session = there();
    for (const [w, h, d] of [[1440, 900, 320], [1920, 1080, 384], [1280, 800, 320], [3840, 2160, 768]] as const) {
      const framing = laptopCraftFraming(flightCameraOf(session, w, h)!);
      expect(framing.diameterPx).toBeCloseTo(d, 9);
      expect(framing.xPx).toBeCloseTo(CRAFT_FRAMING.xFraction * w, 9);
      expect(framing.yPx).toBeCloseTo(CRAFT_FRAMING.yFraction * h, 9);
      expect(framing.frameHeightPx).toBe(h);
    }
  });

  it('leaves the phone as it is', () => {
    expect(phoneCraftFraming(390, 640).diameterPx).toBe(184);
    expect(phoneCraftFraming(844, 300).diameterPx).toBe(184);
  });
});

describe('FlightCraftLayer on a larger window', () => {
  let container: HTMLDivElement;
  let root: Root;
  const ratio = Object.getOwnPropertyDescriptor(window, 'devicePixelRatio');
  const setRatio = (r: number) => Object.defineProperty(window, 'devicePixelRatio', { value: r, configurable: true });
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    if (ratio) Object.defineProperty(window, 'devicePixelRatio', ratio);
    vi.restoreAllMocks();
  });
  const makeRenderer = (): CraftRenderer => ({ setPixelRatio: () => {}, setSize: () => {}, setClearColor: () => {}, clear: () => {}, dispose: () => {}, render: () => {} });
  const model = async () => {
    const g = new THREE.Group();
    g.add(new THREE.Mesh(new THREE.BoxGeometry(2, 1, 0.5), new THREE.MeshStandardMaterial()));
    return g;
  };
  const drawnBox = async (w: number, h: number) => {
    setRatio(1);
    const camera = flightCameraOf(there(), w, h)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load: model, makeRenderer })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    const [x0, y0, x1, y1] = container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-box')!.split(',').map(Number);
    act(() => root.unmount());
    root = createRoot(container);
    return { across: x1! - x0!, tall: y1! - y0! };
  };

  it('draws the craft larger by the window\'s share: 1.2 times at 1920 by 1080, as large at 1280 by 800', async () => {
    const laptop = await drawnBox(1440, 900);
    const film = await drawnBox(1920, 1080);
    const small = await drawnBox(1280, 800);
    // The framing sizes the craft by its sphere at its framing point; seen through each window's own field the drawn box
    // differs by a hair (0.985 of the laptop's at 1920 by 1080 on main, measured), so the share is read within 4 percent
    const within = (v: number, want: number) => expect(Math.abs(v / want - 1)).toBeLessThan(0.04);
    expect(laptop.across).toBeGreaterThan(50);
    within(film.across / laptop.across, 1.2);
    within(film.tall / laptop.tall, 1.2);
    within(small.across / laptop.across, 1);
  });

  it('picks the level by the drawn size: the finest at scale one once the framed craft spans 600 px', async () => {
    const emu = CRAFTS['astronaut-emu']!;
    for (const [w, h, want] of [[1440, 900, emu.modelUrl], [1920, 1080, emu.modelUrl], [3840, 2160, emu.fineModelUrl]] as const) {
      setRatio(1);
      const load = vi.fn(async (_url: string) => new THREE.Group());
      await act(async () => root.render(createElement(FlightCraftLayer, { camera: flightCameraOf(there(), w, h)!, craft: emu, load, makeRenderer })));
      expect(load.mock.calls.map(([u]) => u)).toEqual([want]);
      act(() => root.unmount());
      root = createRoot(container);
    }
  });
});
