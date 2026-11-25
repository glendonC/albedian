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

// milkyway-flight-one (ruled 16:18 on 24 Nov): in flight the layer shows the starless glow in a canvas of its own, so Go
// there dissolves the photograph into it; Real sky itself stays ESO's photograph as published.
describe('RealSkyLayer in flight: the starless glow', () => {
  const asked: string[] = [];
  class RecordingImage extends FailingImage {
    override set src(url: string) {
      asked.push(url);
    }
  }
  beforeEach(() => {
    asked.length = 0;
    vi.stubGlobal('Image', RecordingImage);
  });

  it('asks for the starless glow, in a canvas of its own, when there', async () => {
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session: poseSession(sample('plate_calibrated') as ObservationSolution), rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, there: true }));
    });
    expect(asked).toContain('/textures/eso_milkyway_panorama.jpg');
    expect(asked).toContain('/textures/eso_milkyway_diffuse_6k.webp');
    const glow = container.querySelector('canvas.real-sky__glow');
    expect(glow).not.toBeNull();
    expect(glow!.getAttribute('data-state')).toBe('loading');
    expect(glow!.classList.contains('is-drawn')).toBe(false);
  });

  it('moves the glow with the stars on the arrival, over the same area as the photograph (17:18 ruling)', async () => {
    const shift = { fullPx: [64, -32] as const, nowPx: [16, -8] as const };
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session: poseSession(sample('plate_calibrated') as ObservationSolution), rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, there: true, shift }));
    });
    const photo = container.querySelector<HTMLCanvasElement>('canvas.real-sky__canvas')!;
    const glow = container.querySelector<HTMLCanvasElement>('canvas.real-sky__glow')!;
    expect(glow.style.transform).toBe('translate(16px, -8px)');
    for (const key of ['left', 'top', 'width', 'height', 'transform'] as const) expect(glow.style[key]).toBe(photo.style[key]);
    expect([glow.width, glow.height]).toEqual([photo.width, photo.height]);
    expect(glow.width).toBeGreaterThan(1440);
  });

  it('never asks for the glow in Real sky, before Go there', async () => {
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session: poseSession(sample('plate_calibrated') as ObservationSolution), rect: null, viewportWidthPx: 1440, viewportHeightPx: 900 }));
    });
    expect(asked).toEqual(['/textures/eso_milkyway_panorama.jpg']);
    expect(container.querySelector('canvas.real-sky__glow')).toBeNull();
  });
});

describe('RealSkyLayer in flight: the glow fills the planets', () => {
  it('draws the glow canvas with the planet-filling shader, and the photograph with its own', async () => {
    const { FLIGHT_GLOW_FRAGMENT } = await import('../../spatial/sky/flightMilkyWay.js');
    const compiled = new Map<HTMLCanvasElement, string[]>();
    // A WebGL 2 stand-in that records each canvas's shader sources and says yes to every step
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      const canvas = this;
      return new Proxy({}, {
        get: (_t, p) => {
          if (p === 'shaderSource') return (_s: unknown, src: string) => compiled.set(canvas, [...(compiled.get(canvas) ?? []), src]);
          if (p === 'getShaderParameter' || p === 'getProgramParameter') return () => true;
          if (p === 'getError' || p === 'NO_ERROR') return p === 'NO_ERROR' ? 0 : () => 0;
          if (p === 'isContextLost') return () => false;
          if (typeof p === 'string' && /^[A-Z_0-9]+$/.test(p)) return 1;
          return () => ({});
        },
      }) as unknown as RenderingContext;
    } as never);
    class LoadingImage extends FailingImage {
      override set src(_url: string) {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    vi.stubGlobal('Image', LoadingImage);
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session: poseSession(sample('plate_calibrated') as ObservationSolution), rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, there: true }));
    });
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    const glow = container.querySelector<HTMLCanvasElement>('canvas.real-sky__glow')!;
    const photo = container.querySelector<HTMLCanvasElement>('canvas.real-sky__canvas')!;
    expect(glow.classList.contains('is-drawn')).toBe(true);
    expect(compiled.get(glow)).toContain(FLIGHT_GLOW_FRAGMENT);
    expect(compiled.get(photo)).not.toContain(FLIGHT_GLOW_FRAGMENT);
    vi.restoreAllMocks();
  });

  it('draws the glow over the same moved area as the photograph on the arrival (its view and origin)', async () => {
    const views = new Map<HTMLCanvasElement, number[][]>();
    // A WebGL 2 stand-in that records each canvas's two-number uniforms (the view's size, then its origin)
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      const canvas = this;
      return new Proxy({}, {
        get: (_t, p) => {
          if (p === 'uniform2f') return (_l: unknown, x: number, y: number) => views.set(canvas, [...(views.get(canvas) ?? []), [x, y]]);
          if (p === 'getShaderParameter' || p === 'getProgramParameter') return () => true;
          if (p === 'getError' || p === 'NO_ERROR') return p === 'NO_ERROR' ? 0 : () => 0;
          if (p === 'isContextLost') return () => false;
          if (typeof p === 'string' && /^[A-Z_0-9]+$/.test(p)) return 1;
          return () => ({});
        },
      }) as unknown as RenderingContext;
    } as never);
    class LoadingImage extends FailingImage {
      override set src(_url: string) {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    vi.stubGlobal('Image', LoadingImage);
    const session = poseSession(sample('plate_calibrated') as ObservationSolution);
    const shift = { fullPx: [64, -32] as const, nowPx: [16, -8] as const };
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session, rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, there: true, shift }));
    });
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    const glow = container.querySelector<HTMLCanvasElement>('canvas.real-sky__glow')!;
    const photo = container.querySelector<HTMLCanvasElement>('canvas.real-sky__canvas')!;
    expect(glow.classList.contains('is-drawn')).toBe(true);
    expect(views.get(photo)).toEqual([[1440 + 64, 900 + 32], [-64, 0]]);
    expect(views.get(glow)).toEqual(views.get(photo));
    // A new star flown to: a new move, and the glow is drawn again over the new area with the photograph
    const next = { fullPx: [-40, 20] as const, nowPx: [0, 0] as const };
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session, rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, there: true, shift: next }));
    });
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    expect(views.get(photo)!.slice(-2)).toEqual([[1440 + 40, 900 + 20], [0, -20]]);
    expect(views.get(glow)!.slice(-2)).toEqual(views.get(photo)!.slice(-2));
    vi.restoreAllMocks();
  });
});

// AUDIT-D on milkyway-flight-one (18:18): in flight the photograph is hidden, so the layer says whether the glow drew;
// the flight's Milky Way line follows that, never the photograph's state
describe('RealSkyLayer in flight: says whether the glow drew', () => {
  /** A WebGL 2 stand-in that says yes to every step. */
  function yesGl() {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() =>
      new Proxy({}, {
        get: (_t, p) => {
          if (p === 'getShaderParameter' || p === 'getProgramParameter') return () => true;
          if (p === 'getError' || p === 'NO_ERROR') return p === 'NO_ERROR' ? 0 : () => 0;
          if (p === 'isContextLost') return () => false;
          if (typeof p === 'string' && /^[A-Z_0-9]+$/.test(p)) return 1;
          return () => ({});
        },
      })) as never);
  }
  /** The photograph loads; the glow loads, fails or never answers, as asked. */
  function images(glow: 'loads' | 'fails' | 'waits') {
    class SomeImage extends FailingImage {
      override set src(url: string) {
        if (!url.includes('diffuse')) setTimeout(() => this.onload?.(), 0);
        else if (glow === 'loads') setTimeout(() => this.onload?.(), 0);
        else if (glow === 'fails') setTimeout(() => this.onerror?.(), 0);
      }
    }
    vi.stubGlobal('Image', SomeImage);
  }
  async function render(onDrawn: (d: boolean) => void, onGlowDrawn: (d: boolean) => void) {
    await act(async () => {
      root.render(createElement(RealSkyLayer, { session: poseSession(sample('plate_calibrated') as ObservationSolution), rect: null, viewportWidthPx: 1440, viewportHeightPx: 900, there: true, onDrawn, onGlowDrawn }));
    });
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
  }

  it('says the glow is not drawn when it fails, though the photograph drew', async () => {
    yesGl();
    images('fails');
    const onDrawn = vi.fn();
    const onGlowDrawn = vi.fn();
    await render(onDrawn, onGlowDrawn);
    expect(onDrawn).toHaveBeenLastCalledWith(true);
    expect(container.querySelector('canvas.real-sky__glow')!.getAttribute('data-state')).toBe('failed');
    expect(onGlowDrawn).not.toHaveBeenCalledWith(true);
    expect(onGlowDrawn).toHaveBeenLastCalledWith(false);
    vi.restoreAllMocks();
  });

  it('says the glow is not drawn while it is still loading, though the photograph drew', async () => {
    yesGl();
    images('waits');
    const onDrawn = vi.fn();
    const onGlowDrawn = vi.fn();
    await render(onDrawn, onGlowDrawn);
    expect(onDrawn).toHaveBeenLastCalledWith(true);
    expect(container.querySelector('canvas.real-sky__glow')!.getAttribute('data-state')).toBe('loading');
    expect(onGlowDrawn).not.toHaveBeenCalledWith(true);
    vi.restoreAllMocks();
  });

  it('says the glow is drawn once it is, and not as the layer leaves', async () => {
    yesGl();
    images('loads');
    const onGlowDrawn = vi.fn();
    await render(() => {}, onGlowDrawn);
    expect(onGlowDrawn).toHaveBeenLastCalledWith(true);
    await act(async () => root.render(createElement('div')));
    expect(onGlowDrawn).toHaveBeenLastCalledWith(false);
    vi.restoreAllMocks();
  });
});

// The ruled level (16:18): there, the photograph gives way and the drawn glow stands at full, never dimmed
describe('the flight\'s Milky Way at its own level', () => {
  it('shows the drawn glow at opacity 1 there, the photograph at 0', () => {
    const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const rule = (selector: string) => {
      const at = css.indexOf(`${selector} {`);
      expect(at).toBeGreaterThanOrEqual(0);
      return css.slice(at, css.indexOf('}', at));
    };
    expect(rule('.real-sky.is-there .real-sky__glow.is-drawn')).toMatch(/opacity:\s*1;/);
    expect(css).toMatch(/\n\.real-sky__glow\s*\{\s*opacity:\s*0;\s*\}/);
    // realsky-upload-one (19:45): the photograph gives way once the glow has drawn and come in over it, or at once if it fails
    expect(rule('.real-sky.is-there.is-glow-drawn .real-sky__canvas')).toMatch(/opacity:\s*0;\s*transition-delay:\s*var\(--look-mo-dur-view\);/);
    expect(rule('.real-sky.is-there.is-glow-failed .real-sky__canvas')).toMatch(/opacity:\s*0;/);
    expect(css).not.toMatch(/\.real-sky\.is-there\.is-shown\s*\{[^}]*opacity:\s*0\.3/);
  });
});
