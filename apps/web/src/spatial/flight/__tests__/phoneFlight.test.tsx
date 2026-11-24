// @vitest-environment jsdom
import { act, createElement, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../../../reveal/RevealPhoto.js';
import { phoneThereScrim } from '../../../reveal/RealSkyLayer.js';
import { AskBar } from '../../../reveal/AskBar.js';
import { PHONE_CRAFT_DIAMETER_PX, PHONE_SKY_TOP_PX, phoneCraftFraming, type CraftFraming } from '../craftScene.js';
import type { Craft } from '../crafts.js';
import { CRAFT_CHOICES } from '../pilotReadout.js';

// D30 (UIUX-phone3d-READY.md): the flight behind a NASA craft at phone width. WebGL 2 is not in jsdom, so both drawn
// layers are stand-ins; the star layer's stand-in records the craft and the framing it is asked to draw.
const seen = vi.hoisted(() => ({
  craft: null as Craft | null,
  framing: undefined as CraftFraming | undefined,
  steering: false,
  onDragSky: undefined as (() => void) | undefined,
  onPressSky: undefined as (() => void) | undefined,
  scrimTopPx: undefined as number | null | undefined,
  // The starless glow drawn in flight (milkyway-flight-one); false stands for one loading or failed
  glow: true,
}));
vi.mock('../FlightStarsLayer.js', async () => {
  const { createElement: h, useEffect } = await import('react');
  return {
    FlightStarsLayer: (props: { craft?: Craft | null; craftFraming?: CraftFraming; onNoDistanceCount?: (n: number) => void; steering?: boolean; onDragSky?: () => void; onPressSky?: () => void }) => {
      seen.craft = props.craft ?? null;
      seen.framing = props.craftFraming;
      seen.steering = props.steering === true;
      seen.onDragSky = props.onDragSky;
      seen.onPressSky = props.onPressSky;
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
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void; onGlowDrawn?: (drawn: boolean) => void; there?: boolean; readoutTopPx?: number | null }) => {
      seen.scrimTopPx = props.readoutTopPx;
      useEffect(() => {
        props.onDrawn?.(true);
        props.onGlowDrawn?.(seen.glow && props.there === true);
      }, [props.onDrawn, props.onGlowDrawn, props.there]);
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

function showPhone(more: Partial<ComponentProps<typeof RevealPhoto>> = {}) {
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
        ...more,
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
    // .ask-bar.is-phone spans y = 772..816 (top 772, height 44) on 844 px height, so bottom: 28 covers it with 0 px peeking
    expect([sheet.style.left, sheet.style.right, sheet.style.bottom, sheet.style.borderRadius]).toEqual(['12px', '12px', '28px', '28px']);
    expect(Number(sheet.style.zIndex)).toBeGreaterThan(30);
    expect(sheet.parentElement).toBe(document.body);
    const rows = all('[role="option"]');
    expect(rows).toHaveLength(CRAFT_CHOICES.length);
    expect(rows.every((r) => (r as HTMLElement).style.height === '52px')).toBe(true);
    expect(rows[0]!.getAttribute('aria-selected')).toBe('true');
    expect(rows[0]!.textContent).toContain('Your view');
    expect(rows[0]!.textContent).toContain('no craft');
    expect(rows[1]!.textContent).toContain('17.0 km/s');
    // D35 item 3: on the phone the sheet runs from top 150, its list scrolling inside it
    expect(sheet.style.top).toBe('150px');
    expect((q('[data-testid="craft-picker-list"]') as HTMLElement).style.overflowY).toBe('auto');
    expect(sheet.textContent).toContain("NASA's own 3D models. Drawn by the app, not measured.");
  });

  it("carries the readout's Simulated tag at the sheet's top, no caret circle, 10 px above the list (E, UIUX-tagphone-READY.md)", () => {
    showPhone();
    click(q('[data-testid="craft-picker"] > button'));
    const sheet = q('[data-testid="craft-picker-panel"]') as HTMLElement;
    const row = sheet.firstElementChild as HTMLElement;
    expect(row.getAttribute('data-testid')).toBe('craft-picker-tagrow');
    expect([...row.children].map((el) => el.getAttribute('data-evidence-class'))).toEqual(['simulated']);
    expect(row.querySelector('[data-testid="details-circle"]')).toBeNull();
    expect([row.style.display, row.style.marginBottom, row.style.flex]).toEqual(['flex', '10px', '0 0 auto']);
    // Inside the sheet's own 14 px padding, at full from its first frame: no animation or fade on the row or the sheet
    expect(sheet.style.padding.split(' ')[0]).toBe('14px');
    expect([row.style.animation, row.style.opacity, sheet.style.animation, sheet.style.opacity]).toEqual(['', '', '', '']);
    expect(row.nextElementSibling!.getAttribute('data-testid')).toBe('craft-picker-list');
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
      "Milky Way: ESO/S. Brunier's photograph as seen from Earth, with its own stars removed.",
      'Speed: NASA, 17.0 km/s relative to the Sun (August 2024).',
      "NASA's own 3D models. Drawn by the app, not measured.",
    ]);
    expect((q('[data-testid="flight-readout"]') as HTMLElement).style.visibility).toBe('hidden');
    click(pressed);
    expect(q('[data-testid="details-sheet"]')).toBeNull();
    expect((q('[data-testid="flight-readout"]') as HTMLElement).style.visibility).toBe('');
  });

  it('leaves the Milky Way line out of the details while the starless glow is not drawn (AUDIT-D 18:18)', () => {
    seen.glow = false;
    try {
      showPhone();
      click(q('[data-testid="details-circle"]'));
      const lines = all('[data-testid="details-sheet"] li').map((li) => li.textContent);
      expect(lines.some((l) => l?.startsWith('Milky Way'))).toBe(false);
      expect(lines).toContain('Rings: the stars your photo matched.');
    } finally {
      seen.glow = true;
    }
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

describe('Steering on a touch screen (D30 item 6)', () => {
  it('hands the camera over on a drag on the sky, never on a tap', () => {
    const onPhoneSteer = vi.fn();
    showPhone({ onPhoneSteer });
    expect(seen.onPressSky).toBeUndefined();
    expect(seen.steering).toBe(false);
    act(() => seen.onDragSky!());
    expect(onPhoneSteer).toHaveBeenCalledTimes(1);
  });

  it('while steering: "Drag: turn" on its own line under the first sentence, no speed line, nothing only true from Earth', () => {
    showPhone({ phoneSteering: true, onPhoneSteer: () => {} });
    expect(seen.steering).toBe(true);
    expect(seen.onDragSky).toBeUndefined();
    const note = q('[data-testid="flight-readout"] .flight-readout__note')!;
    expect(note.textContent).toBe('Drawn or computed by the app; not measured in the source recording.Drag: turn');
    const line = note.querySelector('[data-testid="steer-line"]') as HTMLElement;
    expect(line.textContent).toBe('Drag: turn');
    expect(line.previousElementSibling!.tagName).toBe('BR');
    expect(line.style.color).toBe('var(--look-ink-2)');
    expect(q('[data-testid="steer-speed"]')).toBeNull();
    expect(q('[data-testid="real-sky"]')).toBeNull();
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
  });

  it('offers "Autopilot" beside Back to Moment on the phone while steering, which gives the camera back', () => {
    const onAutopilot = vi.fn();
    const bar = (phone: boolean, autopilot?: () => void) =>
      act(() => root.render(createElement(AskBar, { canListen: true, listening: false, words: null, onListen: () => {}, onSend: () => {}, onBack: () => {}, phone, onAutopilot: autopilot })));
    bar(true, onAutopilot);
    const buttons = all('[data-testid="ask-bar"] button').map((b) => b.textContent);
    expect(buttons).toEqual(['Back to Moment', 'Autopilot']);
    click(all('[data-testid="ask-bar"] button')[1]!);
    expect(onAutopilot).toHaveBeenCalledTimes(1);
    expect((all('[data-testid="ask-bar"] button')[1] as HTMLElement).className).toContain('look-second');
    bar(true, undefined);
    expect(all('[data-testid="ask-bar"] button').map((b) => b.textContent)).toEqual(['Back to Moment']);
    bar(false, onAutopilot);
    expect(all('[data-testid="ask-bar"] button').map((b) => b.textContent)).not.toContain('Autopilot');
  });
});

describe('Your photo\'s view at phone width (D30 item 7)', () => {
  it('shows the pill beside the craft\'s, and pressed, the readout as D28\'s in the flight readout\'s place', () => {
    showPhone();
    const pill = q('[data-testid="cone-pill"]') as HTMLButtonElement;
    expect(pill.className).toContain('look-size-phone');
    click(pill);
    expect(pill.getAttribute('aria-pressed')).toBe('true');
    expect(q('[data-testid="craft-picker"]')).not.toBeNull();
    expect(q('[data-testid="flight-readout"]')).toBeNull();
    const readout = q('[data-testid="cone-readout"]')!;
    expect(readout.className).toBe('flight-readout is-phone');
    expect(readout.querySelector('[data-testid="details-circle"]')).not.toBeNull();
    expect(readout.querySelector('.flight-readout__note')!.textContent).toBe('Drawn or computed by the app; not measured in the source recording.');
    const title = readout.querySelector('[data-testid="cone-title"]') as HTMLElement;
    expect(title.textContent).toBe("Your photo's view");
    expect(title.style.fontSize).toBe('15px');
    expect(all('[data-testid="cone-readout"] .flight-readout__stat span').map((x) => x.textContent)).toEqual(['of your stars inside', 'to the farthest']);
  });

  it('ends the phone\'s steering when it opens, so Autopilot does not stay offered where nothing steers', () => {
    const onEndPhoneSteer = vi.fn();
    showPhone({ phoneSteering: true, onPhoneSteer: () => {}, onEndPhoneSteer });
    expect(onEndPhoneSteer).not.toHaveBeenCalled();
    click(q('[data-testid="cone-pill"]'));
    expect(onEndPhoneSteer).toHaveBeenCalledTimes(1);
  });

  it('holds D28\'s disclosure lines in the sheet behind the circle', () => {
    showPhone();
    click(q('[data-testid="cone-pill"]'));
    click(q('[data-testid="cone-readout"] [data-testid="details-circle"]'));
    expect(all('[data-testid="details-sheet"] li').map((li) => li.textContent)).toEqual([
      'Drawn or computed by the app; not measured in the source recording.',
      "Your photo's view: the part of the sky your photo holds, from its solve, out to its farthest matched star with a measured distance.",
      'Stars at their Bailer-Jones (2021) or Hipparcos 2007 distances.',
      'Rings: the stars your photo matched.',
      '3 stars have no measured distance, so they stay where they appear from Earth.',
    ]);
    expect((q('[data-testid="cone-readout"]') as HTMLElement).style.visibility).toBe('hidden');
  });

  it("keeps the Simulated tag row, details sheet and first sentence in Your photo's view while an answer is open, hiding the title, stats and nearest line (item B)", () => {
    const answer = {
      heard: 'How far away is it?',
      sentence: 'Rigel is about 860 light years away.',
      answer: null,
    } as const;
    showPhone({ answer });
    click(q('[data-testid="cone-pill"]'));
    const readout = q('[data-testid="cone-readout"]')!;
    expect(readout.querySelector('.flight-readout__tagrow .look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
    expect(readout.querySelector('.flight-readout__note')!.textContent).toBe('Drawn or computed by the app; not measured in the source recording.');
    expect(readout.querySelector('[data-testid="cone-title"]')).toBeNull();
    expect(readout.querySelector('.flight-readout__stats')).toBeNull();
    expect(readout.querySelectorAll('p')).toHaveLength(1);
    click(q('[data-testid="cone-readout"] [data-testid="details-circle"]'));
    expect(q('[data-testid="details-sheet"]')).not.toBeNull();
  });
});

describe('The scrim raised to the readout (D30 item 2)', () => {
  it('darkens from 70 px above the readout\'s top to 62 percent 10 px under it and 86 at the foot, the top bar\'s shade as before', () => {
    expect(phoneThereScrim(844, 417)).toBe(
      'linear-gradient(to bottom, rgba(0, 0, 0, 0.7) 0px, rgba(0, 0, 0, 0.3) 112px, rgba(0, 0, 0, 0) 150px, rgba(0, 0, 0, 0) 347px, rgba(0, 0, 0, 0.62) 427px, rgba(0, 0, 0, 0.86) 844px)'
    );
  });

  it('hands the panorama the readout\'s measured top on the phone there', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 417, bottom: 696, left: 24, right: 366, width: 342, height: 279, x: 24, y: 417, toJSON: () => ({}) } as DOMRect);
    showPhone();
    expect(seen.scrimTopPx).toBe(417);
    vi.restoreAllMocks();
  });
});
