// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CRAFTS } from '../crafts.js';
import { CraftPicker, PilotTrip } from '../PilotPieces.js';
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
    // D35 items 1 and 3: the hangar's two kinds as headings, not pressable; the list scrolls within 470 px
    expect([...container.querySelectorAll('[data-testid="craft-picker-heading"]')].map((h) => h.textContent)).toEqual(['Spacecraft', 'Spacesuits']);
    expect([...container.querySelectorAll('[role="group"]')].map((g) => g.getAttribute('aria-label'))).toEqual(['Spacecraft', 'Spacesuits']);
    const list = container.querySelector('[data-testid="craft-picker-list"]') as HTMLElement;
    expect([list.style.maxHeight, list.style.overflowY]).toEqual(['470px', 'auto']);
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
