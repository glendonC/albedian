// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { RealSkyLayer, isRealSkyShown } from '../RealSkyLayer.js';

// The Real sky layer tells the reveal whether it drew, so the reveal's status says only what is on screen (the verdict on
// unit-realsky-1, scenario B: the panorama does not load).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = fs.readFileSync(path.resolve(here, '../../../public/textures/eso0932a_registration.json'), 'utf8');

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.stubGlobal('fetch', vi.fn(async () => new Response(registration, { status: 200 })));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

class FailingImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decoding = '';
  set src(_url: string) {
    setTimeout(() => this.onerror?.(), 0);
  }
}

describe('RealSkyLayer: whether it drew', () => {
  it('says it drew nothing, and stays hidden, when the panorama does not load', async () => {
    vi.stubGlobal('Image', FailingImage);
    const onDrawn = vi.fn();
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session: poseSession(sample('plate_calibrated') as ObservationSolution), rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, onDrawn }));
    });
    await act(async () => await new Promise((r) => setTimeout(r, 20)));
    expect(container.querySelector('[data-testid="real-sky"]')!.getAttribute('data-state')).toBe('failed');
    expect(container.querySelector('.real-sky')!.className).toBe('real-sky');
    expect(onDrawn).toHaveBeenLastCalledWith(false);
    expect(onDrawn).not.toHaveBeenCalledWith(true);
  });

  it('says it drew nothing while it loads', async () => {
    class SlowImage extends FailingImage {
      override set src(_url: string) {}
    }
    vi.stubGlobal('Image', SlowImage);
    const onDrawn = vi.fn();
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session: poseSession(sample('plate_calibrated') as ObservationSolution), rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, onDrawn }));
    });
    expect(container.querySelector('[data-testid="real-sky"]')!.getAttribute('data-state')).toBe('loading');
    expect(onDrawn).toHaveBeenLastCalledWith(false);
  });
});

describe('isRealSkyShown', () => {
  it('shows Real sky only for a session with a photo, in the measured sky view of a verified solve, where WebGL 2 draws', () => {
    const base = poseSession(sample('plate_calibrated') as ObservationSolution);
    const session = { ...base, spatialView: { ...base.spatialView, mode: 'registered_sky' as const } };
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    try {
      expect(isRealSkyShown(session)).toBe(true);
      expect(isRealSkyShown({ ...session, sourceAsset: undefined })).toBe(false);
      expect(isRealSkyShown(base)).toBe(false);
    } finally {
      delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
    }
    expect(isRealSkyShown(session)).toBe(false);
  });
});
