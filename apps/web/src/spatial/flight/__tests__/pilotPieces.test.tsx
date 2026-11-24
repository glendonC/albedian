// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CRAFTS } from '../crafts.js';
import { FlightPills, laptopFlightPillsBottomPx } from '../ConePieces.js';
import { CraftPicker, DetailsCircle, DetailsSheet, PilotTrip, laptopPickerListMaxHeight } from '../PilotPieces.js';
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
    // D35 item 4 and hp-top.html: "Your view" shows Phosphor's eye icon on the 44x44 black tile, every craft picture on
    // the same black tile, and every row places its detail in the right column (44px 1fr auto 18px) with the selected wash
    const yourTile = rows[0]!.firstElementChild as HTMLElement;
    expect([yourTile.style.background, yourTile.style.borderRadius]).toEqual(['rgb(0, 0, 0)', '8px']);
    expect(yourTile.querySelector('svg path')).not.toBeNull();
    const selectedRow = rows[1] as HTMLElement;
    expect([selectedRow.style.display, selectedRow.style.gridTemplateColumns, selectedRow.style.borderRadius, selectedRow.style.background]).toEqual([
      'grid',
      '44px 1fr auto 18px',
      '12px',
      'rgba(244, 243, 239, 0.08)',
    ]);
    const detailSpan = selectedRow.children[2] as HTMLElement;
    expect([detailSpan.textContent, detailSpan.style.fontVariantNumeric]).toEqual(['17.0 km/s', 'tabular-nums']);
    expect((rows[0] as HTMLElement).style.background).toBe('');
    const panel = container.querySelector('[data-testid="craft-picker-panel"]') as HTMLElement;
    expect(panel.style.padding).toBe('8px');
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

  it('steps laptop flight-pills 16 px above .ask-bar when horizontal room is short and re-clamps CraftPicker (UIUX-pilot-READY 04:34)', async () => {
    // Pure boundary math: 36 + pillsWidthPx + 16 <= askLeftPx stays at 32 px; > askLeftPx steps to (viewH - askTopPx) + 16
    expect(laptopFlightPillsBottomPx(900, null)).toBe(32);
    expect(laptopFlightPillsBottomPx(900, { pillsWidthPx: 338, askLeftPx: 390, askTopPx: 820 })).toBe(32);
    expect(laptopFlightPillsBottomPx(900, { pillsWidthPx: 338.1, askLeftPx: 390, askTopPx: 820 })).toBe(96);
    expect(laptopFlightPillsBottomPx(768, { pillsWidthPx: 338.4, askLeftPx: 206.4, askTopPx: 688 })).toBe(96);

    const status = document.createElement('div');
    status.setAttribute('data-testid', 'reveal-status');
    const askBar = document.createElement('div');
    askBar.setAttribute('data-testid', 'ask-bar');
    document.body.append(status, askBar);

    let viewW = 1440;
    let viewH = 900;
    let askW = 612;
    let pillsW = 338;
    const observed = new Map<Element, Set<() => void>>();
    const fireResize = (target: Element) => {
      for (const cb of observed.get(target) ?? []) cb();
    };
    const prevRO = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      private cb: () => void;
      private targets = new Set<Element>();
      constructor(cb: () => void) {
        this.cb = cb;
      }
      observe(target: Element) {
        this.targets.add(target);
        if (!observed.has(target)) observed.set(target, new Set());
        observed.get(target)!.add(this.cb);
      }
      unobserve(target: Element) {
        this.targets.delete(target);
        observed.get(target)?.delete(this.cb);
      }
      disconnect() {
        for (const t of this.targets) observed.get(t)?.delete(this.cb);
        this.targets.clear();
      }
    } as unknown as typeof ResizeObserver;

    const box = (left: number, top: number, right: number, bottom: number) =>
      ({ left, top, right, bottom, x: left, y: top, width: right - left, height: bottom - top, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(window, 'innerHeight', 'get').mockImplementation(() => viewH);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('data-testid') === 'reveal-status') return box(36, 70, 356, 364);
      if (this.getAttribute('data-testid') === 'ask-bar') return box(viewW / 2 - askW / 2, viewH - 80, viewW / 2 + askW / 2, viewH - 32);
      if (this.getAttribute('data-testid') === 'flight-pills') {
        const b = Number.parseFloat(this.style.bottom || '32');
        return box(36, viewH - b - 36, 36 + pillsW, viewH - b);
      }
      if (this.getAttribute('data-testid') === 'craft-picker-panel') {
        const pills = this.closest('[data-testid="flight-pills"]') as HTMLElement | null;
        const b = Number.parseFloat(pills?.style.bottom || '32');
        const panelBottom = viewH - (b + 36 + 10);
        return box(36, panelBottom - 513, 428, panelBottom);
      }
      if (this.getAttribute('data-testid') === 'craft-picker-list') {
        const pills = this.closest('[data-testid="flight-pills"]') as HTMLElement | null;
        const b = Number.parseFloat(pills?.style.bottom || '32');
        const panelBottom = viewH - (b + 36 + 10);
        return box(36, panelBottom - 505, 428, panelBottom - 35);
      }
      return box(0, 0, 0, 0);
    });

    try {
      await act(async () =>
        root.render(
          createElement(FlightPills, {
            craftPill: createElement(CraftPicker, { craft: CRAFTS['voyager-1']!, onPick: () => {} }),
            cone: false,
            onCone: () => {},
          }),
        ),
      );
      const pills = container.querySelector('[data-testid="flight-pills"]') as HTMLElement;
      // 1440x900 with Voyager 1 (36 + 338 + 16 = 390 <= 414): stays at bottom 32 px
      expect(pills.style.bottom).toBe('32px');
      expect(pills.style.transition).toBe('');

      // Open CraftPicker at 1440x900 (bottom 32 -> panelBottom = 900 - 78 = 822; 822 - 380 - 43 = 399 px)
      await act(async () => (container.querySelector('[data-testid="craft-picker"] > button') as HTMLButtonElement).click());
      const list = container.querySelector('[data-testid="craft-picker-list"]') as HTMLElement;
      expect(list.style.maxHeight).toBe('399px');

      // Long name at 1440x900 (James Webb Space Telescope: pillsW = 442.2 -> 36 + 442.2 + 16 = 494.2 > 414) steps to 96 px
      // and CraftPicker immediately re-clamps its open list (panelBottom = 900 - 142 = 758; 758 - 380 - 43 = 335 px)
      pillsW = 442.2;
      await act(async () => {
        fireResize(pills);
      });
      expect(pills.style.bottom).toBe('96px');
      expect(list.style.maxHeight).toBe('335px');

      // Back to short name at 1440x900 returns to 32 px, then typing field opens (askW = 822.7 -> askLeft = 308.65) -> 96 px
      pillsW = 338;
      await act(async () => {
        fireResize(pills);
      });
      expect(pills.style.bottom).toBe('32px');
      askW = 822.7;
      await act(async () => {
        fireResize(askBar);
      });
      expect(pills.style.bottom).toBe('96px');

      // Resize to 1024x768 with normal askW = 612 (askLeft = 206 < 390): stays stepped at 96 px and clamps list to 203 px
      askW = 612;
      viewW = 1024;
      viewH = 768;
      await act(async () => {
        window.dispatchEvent(new Event('resize'));
      });
      expect(pills.style.bottom).toBe('96px');
      // panelBottom = 768 - 142 = 626; 626 - 380 - 43 = 203 px
      expect(list.style.maxHeight).toBe('203px');
    } finally {
      globalThis.ResizeObserver = prevRO;
      status.remove();
      askBar.remove();
      vi.restoreAllMocks();
    }
  });

  it('closes CraftPicker and DetailsSheet on Escape returning focus to the trigger, and steps option focus on ArrowUp and ArrowDown', async () => {
    const key = (target: EventTarget, init: KeyboardEventInit) => {
      const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
      act(() => {
        target.dispatchEvent(ev);
      });
      return ev;
    };

    // 1. Laptop CraftPicker: ArrowDown/ArrowUp move focus across options with preventDefault; Escape closes and focuses trigger
    await act(async () => root.render(createElement(CraftPicker, { craft: CRAFTS['voyager-1']!, onPick: () => {} })));
    const trigger = container.querySelector('[data-testid="craft-picker"] > button') as HTMLButtonElement;
    trigger.focus();
    await act(async () => trigger.click());
    expect(container.querySelector('[data-testid="craft-picker-panel"]')).not.toBeNull();

    // First ArrowDown from trigger focuses the selected option (voyager-1, index 1), next ArrowDown advances to voyager-2 (index 2), ArrowUp returns to voyager-1
    const down1 = key(trigger, { key: 'ArrowDown' });
    expect(down1.defaultPrevented).toBe(true);
    expect((document.activeElement as HTMLElement).getAttribute('data-craft')).toBe('voyager-1');
    const down2 = key(document.activeElement!, { key: 'ArrowDown' });
    expect(down2.defaultPrevented).toBe(true);
    expect((document.activeElement as HTMLElement).getAttribute('data-craft')).toBe('voyager-2');
    const up1 = key(document.activeElement!, { key: 'ArrowUp' });
    expect(up1.defaultPrevented).toBe(true);
    expect((document.activeElement as HTMLElement).getAttribute('data-craft')).toBe('voyager-1');

    // Escape inside an input or with ctrlKey or while KeyboardHelp dialog is open leaves CraftPicker open
    const input = document.createElement('input');
    document.body.appendChild(input);
    const helpWrap = document.createElement('div');
    helpWrap.className = 'keys-help';
    const helpDialog = document.createElement('section');
    helpDialog.setAttribute('role', 'dialog');
    helpWrap.appendChild(helpDialog);
    document.body.appendChild(helpWrap);
    try {
      expect(key(input, { key: 'Escape' }).defaultPrevented).toBe(false);
      expect(container.querySelector('[data-testid="craft-picker-panel"]')).not.toBeNull();
      expect(key(window, { key: 'Escape' }).defaultPrevented).toBe(false);
      expect(container.querySelector('[data-testid="craft-picker-panel"]')).not.toBeNull();
    } finally {
      input.remove();
      helpWrap.remove();
    }
    expect(key(window, { key: 'Escape', ctrlKey: true }).defaultPrevented).toBe(false);
    expect(container.querySelector('[data-testid="craft-picker-panel"]')).not.toBeNull();

    const esc = key(window, { key: 'Escape' });
    expect(esc.defaultPrevented).toBe(true);
    expect(container.querySelector('[data-testid="craft-picker-panel"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);

    // 2. Phone DetailsSheet: Escape calls onClose with preventDefault and returns focus to details-circle
    let detailsOpen = true;
    const renderDetails = () =>
      createElement(
        'div',
        null,
        createElement(
          'div',
          { style: { visibility: detailsOpen ? 'hidden' : 'visible' } },
          createElement(DetailsCircle, { open: detailsOpen, onToggle: () => (detailsOpen = !detailsOpen) }),
        ),
        detailsOpen
          ? createElement(DetailsSheet, {
              lines: ['Drawn or computed by the app; not measured in the source recording.'],
              onClose: () => {
                detailsOpen = false;
                act(() => {
                  root.render(renderDetails());
                });
              },
            })
          : null,
      );
    await act(async () => root.render(renderDetails()));
    expect(document.querySelector('[data-testid="details-sheet"]')).not.toBeNull();
    const sheetEsc = key(window, { key: 'Escape' });
    expect(sheetEsc.defaultPrevented).toBe(true);
    expect(document.querySelector('[data-testid="details-sheet"]')).toBeNull();
    expect(document.activeElement).toBe(container.querySelector('[data-testid="details-circle"]'));
  });

  it('contains Tab and Shift+Tab focus inside open phone CraftPicker and DetailsSheet modal sheets without trapping laptop CraftPicker', async () => {
    const key = (target: EventTarget, init: KeyboardEventInit) => {
      const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
      act(() => {
        target.dispatchEvent(ev);
      });
      return ev;
    };

    // Laptop CraftPicker stays a non-modal listbox popup (Tab is not trapped)
    await act(async () => root.render(createElement(CraftPicker, { craft: CRAFTS['voyager-1']!, onPick: () => {}, phone: false })));
    const laptopTrigger = container.querySelector('[data-testid="craft-picker"] > button') as HTMLButtonElement;
    expect(laptopTrigger.getAttribute('aria-haspopup')).toBe('listbox');
    await act(async () => laptopTrigger.click());
    const laptopPanel = container.querySelector('[data-testid="craft-picker-panel"]') as HTMLElement;
    expect(laptopPanel.getAttribute('role')).toBe('listbox');
    expect(laptopPanel.getAttribute('aria-modal')).toBeNull();
    const laptopOpts = [...container.querySelectorAll<HTMLButtonElement>('[role="option"]')];
    laptopOpts.at(-1)!.focus();
    expect(key(laptopOpts.at(-1)!, { key: 'Tab' }).defaultPrevented).toBe(false);

    // 1. Phone CraftPicker modal sheet: role="dialog" aria-modal="true" wrapping inner role="listbox", initial focus on selected option, Tab/Shift+Tab contained
    const bgBtn = document.createElement('button');
    bgBtn.textContent = 'Back to Moment';
    document.body.appendChild(bgBtn);
    try {
      await act(async () => root.render(createElement(CraftPicker, { key: 'phone', craft: CRAFTS['voyager-1']!, onPick: () => {}, phone: true })));
      const phoneTrigger = container.querySelector('[data-testid="craft-picker"] > button') as HTMLButtonElement;
      expect(phoneTrigger.getAttribute('aria-haspopup')).toBe('dialog');
      phoneTrigger.focus();
      await act(async () => phoneTrigger.click());

      const sheet = document.querySelector('[data-testid="craft-picker-panel"]') as HTMLElement;
      expect([sheet.getAttribute('role'), sheet.getAttribute('aria-modal'), sheet.getAttribute('aria-label')]).toEqual(['dialog', 'true', 'Craft']);
      const innerList = sheet.querySelector('[data-testid="craft-picker-list"]') as HTMLElement;
      expect([innerList.getAttribute('role'), innerList.getAttribute('aria-label')]).toEqual(['listbox', 'Craft']);

      const opts = [...sheet.querySelectorAll<HTMLButtonElement>('[role="option"]')];
      const first = opts[0]!;
      const middle = opts[1]!;
      const last = opts.at(-1)!;
      // Initial focus moves onto the selected option inside the sheet (voyager-1)
      expect(document.activeElement).toBe(middle);

      // Middle option lets normal intra-sheet Tab proceed without preventDefault
      expect(key(middle, { key: 'Tab' }).defaultPrevented).toBe(false);

      // Last option wraps forward on Tab to first option
      last.focus();
      const wrapForward = key(last, { key: 'Tab' });
      expect(wrapForward.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(first);

      // First option wraps backward on Shift+Tab to last option
      const wrapBack = key(first, { key: 'Tab', shiftKey: true });
      expect(wrapBack.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(last);

      // Focus accidentally on occluded background button is pulled back into the sheet on both Tab and Shift+Tab
      bgBtn.focus();
      expect(key(bgBtn, { key: 'Tab' }).defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(first);
      bgBtn.focus();
      expect(key(bgBtn, { key: 'Tab', shiftKey: true }).defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(last);

      // 2. Phone DetailsSheet modal sheet: role="dialog" aria-modal="true", initial focus on Fewer details, Tab and Shift+Tab stay on Fewer details
      let detailsOpen = true;
      await act(async () =>
        root.render(
          createElement(
            'div',
            null,
            createElement(DetailsCircle, { open: false, onToggle: () => {} }),
            createElement(DetailsSheet, {
              lines: ['Drawn or computed by the app; not measured in the source recording.'],
              onClose: () => {
                detailsOpen = false;
              },
            }),
          ),
        ),
      );
      expect(container.querySelector('[data-testid="details-circle"]')!.getAttribute('aria-haspopup')).toBe('dialog');
      const dSheet = document.querySelector('[data-testid="details-sheet"]') as HTMLElement;
      expect([dSheet.getAttribute('role'), dSheet.getAttribute('aria-modal'), dSheet.getAttribute('aria-label')]).toEqual(['dialog', 'true', 'Details']);
      const closeBtn = dSheet.querySelector('[data-testid="details-circle"]') as HTMLButtonElement;
      expect(closeBtn.getAttribute('aria-haspopup')).toBeNull();
      expect(document.activeElement).toBe(closeBtn);

      expect(key(closeBtn, { key: 'Tab' }).defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(closeBtn);
      expect(key(closeBtn, { key: 'Tab', shiftKey: true }).defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(closeBtn);

      bgBtn.focus();
      expect(key(bgBtn, { key: 'Tab' }).defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(closeBtn);
      expect(detailsOpen).toBe(true);
    } finally {
      bgBtn.remove();
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
