// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CRAFTS } from '../crafts.js';
import { CraftPicker, PilotTrip, laptopPickerListMaxHeight } from '../PilotPieces.js';
import { FlightClock, CRAFT_CHOICES } from '../pilotReadout.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

describe('CraftPicker (D26 item 3, D35)', () => {
  it('names the current craft on its pill and opens the rows above it, your view first', async () => {
    const onPick = vi.fn();
    await act(async () => root.render(createElement(CraftPicker, { craft: CRAFTS['voyager-1']!, onPick })));
    const pill = container.querySelector('[data-testid="craft-picker"] > button')!;
    expect(pill.textContent).toBe('Voyager 1');
    expect(container.querySelector('[data-testid="craft-picker-panel"]')).toBeNull();
    await act(async () => (pill as HTMLButtonElement).click());
    const rows = [...container.querySelectorAll('[role="option"]')];
    expect(rows.slice(0, 3).map((r) => r.textContent)).toEqual(['Your viewno craft', 'Voyager 117.0 km/s', 'Voyager 215.4 km/s']);
    expect(rows).toHaveLength(CRAFT_CHOICES.length);
    expect(rows.filter((r) => r.getAttribute('aria-selected') === 'true').map((r) => r.getAttribute('data-craft'))).toEqual(['voyager-1']);
    // D35 items 1 and 3: the hangar's two kinds as headings, not pressable; the list scrolls within D35's 470 px cap
    // clamped by the viewport height (900 - 480 = 420 px at 1440x900 when no status block is on the page)
    expect([...container.querySelectorAll('[data-testid="craft-picker-heading"]')].map((h) => h.textContent)).toEqual(['Spacecraft', 'Spacesuits']);
    expect([...container.querySelectorAll('[role="group"]')].map((g) => g.getAttribute('aria-label'))).toEqual(['Spacecraft', 'Spacesuits']);
    const list = container.querySelector('[data-testid="craft-picker-list"]') as HTMLElement;
    expect([list.style.maxHeight, list.style.overflowY]).toEqual([`${laptopPickerListMaxHeight(window.innerHeight)}px`, 'auto']);
    // D35 item 4: every picture on the same black tile
    const img = rows[1]!.querySelector('img') as HTMLImageElement;
    expect([img.getAttribute('src'), img.style.background, img.style.borderRadius, img.style.objectFit]).toEqual(['/models/hangar/voyager/voyager_picker.webp', 'rgb(0, 0, 0)', '8px', 'contain']);
    expect([...container.querySelectorAll('[data-testid="craft-picker-panel"] p')].at(-1)!.textContent).toBe("NASA's own 3D models. Drawn by the app, not measured.");
    await act(async () => (rows.find((r) => r.getAttribute('data-craft') === 'astronaut-emu') as HTMLButtonElement).click());
    expect(onPick).toHaveBeenLastCalledWith(CRAFTS['astronaut-emu']!);
    expect(container.querySelector('[data-testid="craft-picker-panel"]')).toBeNull();
    await act(async () => (pill as HTMLButtonElement).click());
    await act(async () => (container.querySelectorAll('[role="option"]')[0] as HTMLButtonElement).click());
    expect(onPick).toHaveBeenLastCalledWith(null);
  });

  it('says "Your view" when no craft flies', async () => {
    await act(async () => root.render(createElement(CraftPicker, { craft: null, onPick: () => {} })));
    expect(container.querySelector('[data-testid="craft-picker"] > button')!.textContent).toBe('Your view');
  });

  it('clamps the laptop list height below reveal-status with 16 px clearance at 900 px and 768 px heights (D35 item 3, tag rule)', async () => {
    // Pure math across 1080, 900 and 768 heights, with and without measured status/panel rects
    expect(laptopPickerListMaxHeight(1080)).toBe(470);
    expect(laptopPickerListMaxHeight(900)).toBe(420);
    expect(laptopPickerListMaxHeight(768)).toBe(288);
    // At 1440x900 with D27's status bottom at 364, panel bottom at 814 and 43 px panel chrome: 814 - (364 + 16) - 43 = 391 px
    expect(laptopPickerListMaxHeight(900, { panelBottomPx: 814, panelChromePx: 43, statusBottomPx: 364 })).toBe(391);
    // At 1024x768 with status bottom at 364 and panel bottom at 682: 682 - (364 + 16) - 43 = 259 px (never reaches Simulated)
    expect(laptopPickerListMaxHeight(768, { panelBottomPx: 682, panelChromePx: 43, statusBottomPx: 364 })).toBe(259);
    // When status is short in a tall window, D35's 470 px cap still holds; in a tiny window, the 96 px floor holds
    expect(laptopPickerListMaxHeight(1080, { panelBottomPx: 994, panelChromePx: 43, statusBottomPx: 220 })).toBe(470);
    expect(laptopPickerListMaxHeight(500, { panelBottomPx: 414, panelChromePx: 43, statusBottomPx: 364 })).toBe(96);

    // Live DOM measurement against [data-testid="reveal-status"] on open and on window resize
    const status = document.createElement('div');
    status.setAttribute('data-testid', 'reveal-status');
    document.body.appendChild(status);
    let viewH = 900;
    const box = (left: number, top: number, right: number, bottom: number) =>
      ({ left, top, right, bottom, x: left, y: top, width: right - left, height: bottom - top, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(window, 'innerHeight', 'get').mockImplementation(() => viewH);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('data-testid') === 'reveal-status') return box(36, 70, 356, 364);
      if (this.getAttribute('data-testid') === 'craft-picker-panel') return box(36, viewH - 86 - 513, 428, viewH - 86);
      if (this.getAttribute('data-testid') === 'craft-picker-list') return box(36, viewH - 86 - 505, 428, viewH - 86 - 35);
      return box(0, 0, 0, 0);
    });
    try {
      await act(async () => root.render(createElement(CraftPicker, { craft: CRAFTS['voyager-1']!, onPick: () => {} })));
      await act(async () => (container.querySelector('[data-testid="craft-picker"] > button') as HTMLButtonElement).click());
      const list = container.querySelector('[data-testid="craft-picker-list"]') as HTMLElement;
      // chromePx = 513 - 470 = 43; 814 - 380 - 43 = 391 px at 900 height
      expect(list.style.maxHeight).toBe('391px');
      viewH = 768;
      await act(async () => {
        window.dispatchEvent(new Event('resize'));
      });
      // At 768 height: panelBottom = 682; 682 - 380 - 43 = 259 px
      expect(list.style.maxHeight).toBe('259px');
    } finally {
      status.remove();
      vi.restoreAllMocks();
    }
  });
});

describe('PilotTrip (D26 items 4 and 5)', () => {
  it('shows the real trip and its lines for Voyager 1', async () => {
    await act(async () => root.render(createElement(PilotTrip, { craft: CRAFTS['voyager-1']!, starName: 'Acrux', starDistancePc: 322 / 3.261563777, flightSeconds: 1.5 })));
    const trip = container.querySelector('[data-testid="pilot-trip"]')!;
    expect(trip.querySelector('b')!.textContent).toBe('5.68 millionyears');
    expect(trip.textContent).toContain('to Acrux at its real speed, 17.0 km/s');
    expect(trip.textContent).toContain('Shown here in 1.5 s: about 3.79 million years each second.');
    expect(trip.textContent).toContain('Speed: NASA, 17.0 km/s relative to the Sun (August 2024).');
  });

  it('shows the no-travel sentence for Webb and no number', async () => {
    await act(async () => root.render(createElement(PilotTrip, { craft: CRAFTS.jwst!, starName: 'Acrux', starDistancePc: 98.7, flightSeconds: 1.5 })));
    const trip = container.querySelector('[data-testid="pilot-trip"]')!;
    expect(trip.querySelector('b')).toBeNull();
    expect(trip.textContent).toBe('James Webb Space TelescopeNo real travel time: Webb stays near Earth, at the Sun-Earth L2 point.');
  });
});

describe('FlightClock: the flight\'s own travel time, measured', () => {
  it('measures from the flight\'s first frame, in either half, to arrival, and nothing for an arrival without a flight', () => {
    const clock = new FlightClock();
    clock.observe('at_source', -50);
    clock.observe('phase_a_photo_to_sky', 0);
    clock.observe('phase_a_photo_to_sky', 400);
    clock.observe('phase_b_sky_to_space', 750);
    clock.observe('at_space', 1500);
    expect(clock.seconds).toBeCloseTo(1.5, 12);
    const reduced = new FlightClock();
    reduced.observe('at_space', 10);
    expect(reduced.seconds).toBeNull();
    // A flight broken off before arrival starts again from its next travelling frame
    const broken = new FlightClock();
    broken.observe('phase_b_sky_to_space', 0);
    broken.observe('at_source', 300);
    broken.observe('phase_b_sky_to_space', 1000);
    broken.observe('at_space', 1750);
    expect(broken.seconds).toBeCloseTo(0.75, 12);
  });
});
