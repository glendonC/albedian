// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../../../reveal/RevealPhoto.js';
import { PHONE_CRAFT_DIAMETER_PX, PHONE_SKY_TOP_PX, phoneCraftFraming, type CraftFraming } from '../craftScene.js';
import type { Craft } from '../crafts.js';

// D30 (UIUX-phone3d-READY.md): the flight behind a NASA craft at phone width. WebGL 2 is not in jsdom, so both drawn
// layers are stand-ins; the star layer's stand-in records the craft and the framing it is asked to draw.
const seen = vi.hoisted(() => ({ craft: null as Craft | null, framing: undefined as CraftFraming | undefined }));
vi.mock('../FlightStarsLayer.js', async () => {
  const { createElement: h, useEffect } = await import('react');
  return {
    FlightStarsLayer: (props: { craft?: Craft | null; craftFraming?: CraftFraming; onNoDistanceCount?: (n: number) => void }) => {
      seen.craft = props.craft ?? null;
      seen.framing = props.craftFraming;
      useEffect(() => {
        props.onNoDistanceCount?.(3);
      });
      return h('div', { 'data-testid': 'flight-stars' });
    },
  };
});
vi.mock('../../../reveal/RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../reveal/RealSkyLayer.js')>();
  const { createElement: h, useEffect } = await import('react');
  return {
    ...original,
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void }) => {
      useEffect(() => {
        props.onDrawn?.(true);
      }, [props.onDrawn]);
      return h('div', { 'data-testid': 'real-sky' });
    },
  };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  seen.craft = null;
  seen.framing = undefined;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

// The sheets are drawn into the body, over the ask bar
const q = (s: string) => document.querySelector(s);
const all = (s: string) => [...document.querySelectorAll(s)];
const click = (el: Element | null) => act(() => (el as HTMLElement).click());

function rigelThere(): ObservationSession {
  const session = poseSession({ ...sample('plate_calibrated') } as ObservationSolution);
  const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
  return { ...session, selectedFindingId: rigel.id } as ObservationSession;
}

function showPhone() {
  act(() => {
    root.render(
      createElement(RevealPhoto, {
        session: rigelThere(),
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 390,
        viewportHeightPx: 844,
        phone: true,
        flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 },
      })
    );
  });
}

describe('phoneCraftFraming: the craft in the sky band (D30 item 1)', () => {
  it('centres the craft across, its middle at 62 percent of the band from the top bar\'s foot to the readout, 184 px across', () => {
    expect(PHONE_SKY_TOP_PX).toBe(104);
    expect(PHONE_CRAFT_DIAMETER_PX).toBe(184);
    const f = phoneCraftFraming(390, 417);
    expect(f.xPx).toBe(195);
    expect(f.yPx).toBeCloseTo(104 + 0.62 * (417 - 104), 9);
    expect(Math.round(f.yPx)).toBe(298);
    expect(f.diameterPx).toBe(184);
  });
});

describe('There at phone width with a craft (D30 items 1 to 5)', () => {
  it('shows the pill row with the craft pill, and draws the chosen craft framed in the sky band', () => {
    showPhone();
    const pills = q('[data-testid="flight-pills"]')!;
    expect(pills.className).toContain('is-phone');
    const pill = q('[data-testid="craft-picker"] > button') as HTMLButtonElement;
    expect(pill.textContent).toBe('Your view');
    expect(pill.className).toContain('look-size-phone');
    expect(seen.craft).toBeNull();
    click(pill);
    click(q('[role="option"][data-craft="voyager-1"]'));
    expect(seen.craft?.id).toBe('voyager-1');
    // Unmeasured (no layout here), the band ends at the readout's foot
    expect(seen.framing).toEqual(phoneCraftFraming(390, 844 - 148));
  });

  it('ends the band at the readout\'s measured top, which moves as its lines grow', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 417, bottom: 696, left: 24, right: 366, width: 342, height: 279, x: 24, y: 417, toJSON: () => ({}) } as DOMRect);
    showPhone();
    click(q('[data-testid="craft-picker"] > button'));
    click(q('[role="option"][data-craft="voyager-1"]'));
    expect(seen.framing).toEqual(phoneCraftFraming(390, 417));
    vi.restoreAllMocks();
  });

  it('opens the craft list as the phone card sheet: four rows, the current one checked, the foot line', () => {
    showPhone();
    click(q('[data-testid="craft-picker"] > button'));
    const sheet = q('[data-testid="craft-picker-panel"]') as HTMLElement;
    expect(sheet.style.position).toBe('fixed');
    expect([sheet.style.left, sheet.style.right, sheet.style.bottom, sheet.style.borderRadius]).toEqual(['12px', '12px', '30px', '28px']);
    expect(Number(sheet.style.zIndex)).toBeGreaterThan(30);
    expect(sheet.parentElement).toBe(document.body);
    const rows = all('[role="option"]');
    expect(rows.map((r) => r.getAttribute('data-craft'))).toEqual(['none', 'voyager-1', 'jwst', 'astronaut']);
    expect(rows.map((r) => (r as HTMLElement).style.height)).toEqual(['52px', '52px', '52px', '52px']);
    expect(rows[0]!.getAttribute('aria-selected')).toBe('true');
    expect(rows[0]!.textContent).toContain('Your view');
    expect(rows[0]!.textContent).toContain('no craft');
    expect(rows[1]!.textContent).toContain('17.0 km/s');
    expect(rows[2]!.textContent).toContain('near Earth');
    expect(sheet.textContent).toContain("NASA's own 3D models. Drawn by the app, not measured.");
  });

  it('names Webb "Webb" on the phone\'s pill', () => {
    showPhone();
    click(q('[data-testid="craft-picker"] > button'));
    click(q('[role="option"][data-craft="jwst"]'));
    expect((q('[data-testid="craft-picker"] > button') as HTMLElement).textContent).toBe('Webb');
  });

  it('puts D27\'s first sentence under the tag with the details circle, and the trip under the numbers without its source', () => {
    showPhone();
    click(q('[data-testid="craft-picker"] > button'));
    click(q('[role="option"][data-craft="voyager-1"]'));
    const readout = q('[data-testid="flight-readout"]')!;
    expect(readout.querySelector('.flight-readout__note')!.textContent).toBe('Drawn or computed by the app; not measured in the source recording.');
    const circle = readout.querySelector('[data-testid="details-circle"]') as HTMLButtonElement;
    expect(circle.getAttribute('aria-label')).toBe('More details');
    expect(circle.getAttribute('aria-expanded')).toBe('false');
    const trip = readout.querySelector('[data-testid="pilot-trip"]')!;
    expect(trip.textContent).toContain('Voyager 1');
    expect(trip.textContent).toContain('to Rigel at its real speed, 17.0 km/s');
    expect(trip.textContent).not.toContain('Speed: NASA');
    expect((trip.querySelector('b') as HTMLElement).style.font).toContain('30px');
  });

  it('opens the whole disclosure from the circle, the sources one press away, the readout hidden meanwhile', () => {
    showPhone();
    click(q('[data-testid="craft-picker"] > button'));
    click(q('[role="option"][data-craft="voyager-1"]'));
    click(q('[data-testid="details-circle"]'));
    const sheet = q('[data-testid="details-sheet"]') as HTMLElement;
    expect(sheet.style.position).toBe('fixed');
    expect(sheet.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
    const pressed = sheet.querySelector('[data-testid="details-circle"]')!;
    expect(pressed.getAttribute('aria-label')).toBe('Fewer details');
    expect(pressed.getAttribute('aria-expanded')).toBe('true');
    expect(all('[data-testid="details-sheet"] li').map((li) => li.textContent)).toEqual([
      'Drawn or computed by the app; not measured in the source recording.',
      'Stars at their Bailer-Jones (2021) or Hipparcos 2007 distances, as bright as they would look from here.',
      'Rings: the stars your photo matched.',
      '3 stars have no measured distance, so they stay where they appear from Earth.',
      'Milky Way: ESO/S. Brunier, dimmed in flight, since its stars are as seen from Earth.',
      'Speed: NASA, 17.0 km/s relative to the Sun (August 2024).',
      "NASA's own 3D models. Drawn by the app, not measured.",
    ]);
    expect((q('[data-testid="flight-readout"]') as HTMLElement).style.visibility).toBe('hidden');
    click(pressed);
    expect(q('[data-testid="details-sheet"]')).toBeNull();
    expect((q('[data-testid="flight-readout"]') as HTMLElement).style.visibility).toBe('');
  });

  it('leaves the craft\'s lines out of the details when no craft flies', () => {
    showPhone();
    click(q('[data-testid="details-circle"]'));
    const lines = all('[data-testid="details-sheet"] li').map((li) => li.textContent);
    expect(lines).toHaveLength(5);
    expect(lines.some((l) => l?.startsWith('Speed:'))).toBe(false);
    expect(lines).not.toContain("NASA's own 3D models. Drawn by the app, not measured.");
  });
});
