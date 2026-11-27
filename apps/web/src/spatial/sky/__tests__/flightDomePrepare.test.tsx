// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Vec3 } from '@albedian/camera-rig';
import { FlightDomeLayer, prepareFlightDome, type DomeView } from '../FlightDomeLayer.js';
import { FLIGHT_DOME_FRAGMENT, FLIGHT_MILKY_WAY_URL } from '../flightMilkyWay.js';
import { forgetKeptSkyPictures, prepareSkyPicture } from '../skyPicture.js';
import { installSkyFakes, named } from './skyPictureFakes.js';

// steer-hitch-one (LEAD ANSWER 27 Nov 03:02, AUDIT-D's catch): the steering dome's program was compiled, linked and
// waited for on its first draw, the first steer, a frame of 233 to 267 ms on a cold shader cache right as a person takes
// the controls. It is made while the flight rests (prepareFlightDome, beside prepareCraftSky), so the first steer makes
// no program.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8');
const view = (z: Vec3): DomeView => ({ rows: { x: [1, 0, 0], y: [0, 1, 0], z }, crpixX: 195, crpixY: 422, scaleRadPerPx: 0.0005, k1: 0, k2: 0, storedToScreen: [1, 0, 0, 0, 1, 0], viewportWidthPx: 390, viewportHeightPx: 844 });

let container: HTMLDivElement;
let root: Root;
let transfers: unknown[];
beforeEach(() => {
  forgetKeptSkyPictures();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  transfers = [];
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, type: string) {
    if (type === 'bitmaprenderer') return { transferFromImageBitmap: (b: unknown) => transfers.push(b) } as unknown as RenderingContext;
    return null;
  } as typeof HTMLCanvasElement.prototype.getContext);
  vi.stubGlobal('Image', class { set src(_u: string) {} });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
});

describe('the steering dome made while the flight rests (steer-hitch-one)', () => {
  it('does the dome\'s first paint at rest at the window\'s size, so the first steer makes no program', async () => {
    const fakes = installSkyFakes({ pictureUrl: FLIGHT_MILKY_WAY_URL, otherBody: registration, pictureRows: 300 });
    prepareSkyPicture(FLIGHT_MILKY_WAY_URL);
    await act(async () => await fakes.frame(5));
    const domePrograms = () => named(fakes.calls, 'shaderSource').filter((c) => c.args[1] === FLIGHT_DOME_FRAGMENT).length;
    expect(domePrograms()).toBe(0);
    prepareFlightDome(390, 844, 2);
    await act(async () => await fakes.frame(2));
    // At rest: the program made, one frame drawn and handed over, at the window's device size, as the first steer would
    expect(domePrograms()).toBe(1);
    const linksAtRest = named(fakes.calls, 'linkProgram').length;
    expect(linksAtRest).toBeGreaterThan(0);
    expect(named(fakes.calls, 'drawArrays')).toHaveLength(1);
    expect(transfers).toHaveLength(1);
    expect(named(fakes.calls, 'viewport').at(-1)!.args).toEqual([0, 0, 780, 1688]);
    // A second preparation draws again but makes no second program
    prepareFlightDome(390, 844, 2);
    await act(async () => await fakes.frame(2));
    expect(domePrograms()).toBe(1);
    // The first steer: drawn and handed over, with no shader compiled and no program linked
    await act(async () => root.render(createElement(FlightDomeLayer, { view: view([0, 0, 1]), viewportWidthPx: 390, viewportHeightPx: 844 })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(container.querySelector('[data-testid="flight-dome"]')?.getAttribute('data-state')).toBe('drawn');
    expect(transfers).toHaveLength(3);
    expect(domePrograms()).toBe(1);
    expect(named(fakes.calls, 'linkProgram')).toHaveLength(linksAtRest);
  });

  it('does nothing where the page cannot keep sky pictures, and nothing goes wrong', async () => {
    const fakes = installSkyFakes({ pictureUrl: FLIGHT_MILKY_WAY_URL, otherBody: registration });
    vi.stubGlobal('OffscreenCanvas', undefined);
    expect(() => prepareFlightDome(390, 844, 2)).not.toThrow();
    await act(async () => await new Promise((r) => setTimeout(r, 10)));
    expect(named(fakes.calls, 'drawArrays')).toHaveLength(0);
    expect(transfers).toHaveLength(0);
  });
});
