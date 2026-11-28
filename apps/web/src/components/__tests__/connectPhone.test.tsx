// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { encodeQr } from '../../../../../scripts/qr.mjs';

vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../CoalescingBar.js', () => ({ CoalescingBar: () => null }));
const { HomeScreen } = await import('../HomeScreen.js');
const { connectPhoneAddress, phoneCodeGeometry } = await import('../phoneAddress.js');

// phone-code-home-one (UIUX-phone-code-READY.md Q3, DECIDED-BY-LEAD 19:25 on 27 Nov): only while the field start serves
// the network and only at the laptop's own host, one SecondAction "Connect phone" in the home's top row before Credits;
// its page in the Credits page's frame: Back, the title, the code on a white square (the standard's four module quiet
// zone inside it, radius 24, each module a whole CSS pixel count: floor(240 / (modules + 8))), 16 px under it the line
// "Scan the QR code to open Albedian on your phone." (Geist 14, ink 66 percent) and 8 px under that the address in Geist
// Mono 16, selectable. No other words. The code is the encoder's (scripts/qr.mjs, the terminal's too).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const homeCss = fs.readFileSync(path.resolve(here, '../../styles/home.css'), 'utf8');
const ADDRESS = 'http://172.20.10.3:5173'; // not real data: the record's own example
const LINE = 'Scan the QR code to open Albedian on your phone.';

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector(s);
const buttons = (scope: Element | null) => [...(scope?.querySelectorAll('button') ?? [])].map((b) => b.textContent || b.getAttribute('aria-label'));
const renderHome = (phoneAddress: string | null) =>
  act(() => root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: () => {}, onFileChosen: () => {}, phoneAddress })));
const press = (b: Element | null | undefined) => act(() => (b as HTMLButtonElement).click());
const byText = (text: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === text);
function rulesOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  postcss.parse(homeCss).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  return found;
}

describe("connectPhoneAddress: only the field start's network address, only at the laptop's own host", () => {
  it('gives the address at localhost, 127.0.0.1 and [::1]', () => {
    for (const host of ['localhost', '127.0.0.1', '[::1]']) expect(connectPhoneAddress(ADDRESS, host), host).toBe(ADDRESS);
  });
  it('gives none on the network address (so never on a phone), on any other host, or with no address served', () => {
    expect(connectPhoneAddress(ADDRESS, '172.20.10.3')).toBeNull();
    expect(connectPhoneAddress(ADDRESS, 'localhost.example')).toBeNull();
    expect(connectPhoneAddress(undefined, 'localhost')).toBeNull();
    expect(connectPhoneAddress('', 'localhost')).toBeNull();
  });
  it('gives none for anything but a plain http address the encoder takes', () => {
    expect(connectPhoneAddress('https://172.20.10.3:5173', 'localhost')).toBeNull();
    expect(connectPhoneAddress('http://172.20.10.3:5173/ é', 'localhost')).toBeNull();
    expect(connectPhoneAddress('javascript:alert(1)', 'localhost')).toBeNull();
  });
});

describe('phoneCodeGeometry: whole CSS pixels, the quiet zone inside the square', () => {
  it('draws version 2 at 7 px a module on a 231 px square (the record)', () => {
    const code = encodeQr(ADDRESS);
    expect(code.version).toBe(2);
    expect(phoneCodeGeometry(code.size)).toEqual({ modulePx: 7, sidePx: 231, quietPx: 28 });
  });
  it('keeps every size whole and within 240 px', () => {
    for (const size of [21, 25, 29, 33, 37]) {
      const g = phoneCodeGeometry(size);
      expect(g.modulePx).toBe(Math.floor(240 / (size + 8)));
      expect(g.sidePx).toBe(g.modulePx * (size + 8));
      expect(g.sidePx).toBeLessThanOrEqual(240);
    }
  });
});

describe('the home: Connect phone, before Credits, only with an address', () => {
  it('stands first in the top row with an address, and not at all without one', () => {
    renderHome(ADDRESS);
    expect(buttons(q('.home-top'))).toEqual(['Connect phone', 'Credits', 'Sound']);
    expect(byText('Connect phone')!.className).toBe(byText('Credits')!.className);
    renderHome(null);
    expect(buttons(q('.home-top'))).toEqual(['Credits', 'Sound']);
  });

  it("opens its page in the Credits page's frame: Back, the title, the code, the line and the address, no other word", () => {
    renderHome(ADDRESS);
    press(byText('Connect phone'));
    const page = q('[data-testid="connect-phone-page"]')!;
    expect(page.className).toBe('credits-page');
    expect(page.tagName).toBe('SECTION');
    expect(q('[data-testid="home-screen"]')).toBeNull();
    expect(page.querySelector('button')!.getAttribute('aria-label')).toBe('Back');
    const title = page.querySelector('h1')!;
    expect([title.className, title.textContent]).toEqual(['credits-title', 'Connect phone']);
    expect(page.getAttribute('aria-labelledby')).toBe(title.id);
    expect(page.querySelector('.phone-code__line')!.textContent).toBe(LINE);
    expect(page.querySelector('.phone-code__address')!.textContent).toBe(ADDRESS);
    const words = [...page.querySelectorAll('h1, p')].map((el) => el.textContent);
    expect(words).toEqual(['Connect phone', LINE, ADDRESS]);
  });

  it("draws the encoder's code crisp on a white square, radius 24, the quiet zone inside it, black modules", () => {
    renderHome(ADDRESS);
    press(byText('Connect phone'));
    const code = encodeQr(ADDRESS);
    const svg = q('.phone-code__square') as SVGSVGElement;
    expect([svg.getAttribute('width'), svg.getAttribute('height'), svg.getAttribute('viewBox')]).toEqual(['231', '231', '0 0 231 231']);
    expect(svg.getAttribute('shape-rendering')).toBe('crispEdges');
    expect(svg.getAttribute('aria-hidden')).toBe('true');
    const square = svg.querySelector('rect')!;
    expect([square.getAttribute('width'), square.getAttribute('height'), square.getAttribute('rx'), square.getAttribute('fill')]).toEqual(['231', '231', '24', '#ffffff']);
    const modules = svg.querySelector('path')!;
    expect(modules.getAttribute('fill')).toBe('#000000');
    // Each dark module one 7 px square at 28 + 7 x its column and row
    const drawn = [...modules.getAttribute('d')!.matchAll(/M(\d+) (\d+)h7v7h-7z/g)].map((m) => `${(Number(m[1]) - 28) / 7},${(Number(m[2]) - 28) / 7}`);
    const dark = code.modules.flatMap((row, y) => row.flatMap((on, x) => (on ? [`${x},${y}`] : [])));
    expect(drawn).toEqual(dark);
  });

  it('gives focus back to the pill that opened each page', () => {
    renderHome(ADDRESS);
    press(byText('Connect phone'));
    press(q('[data-testid="connect-phone-page"] button'));
    act(() => vi.advanceTimersByTime(2_000));
    expect(q('[data-testid="connect-phone-page"]')).toBeNull();
    expect(document.activeElement).toBe(byText('Connect phone'));
    press(byText('Credits'));
    press(q('[data-testid="credits-page"] button'));
    act(() => vi.advanceTimersByTime(2_000));
    expect(document.activeElement).toBe(byText('Credits'));
  });

  it("leaves as Credits leaves: the page's frame out, its words out", () => {
    renderHome(ADDRESS);
    press(byText('Connect phone'));
    press(q('[data-testid="connect-phone-page"] button'));
    expect(q('[data-testid="connect-phone-page"]')!.className).toBe('credits-page is-leaving');
    expect(q('[data-testid="home-screen"]')).not.toBeNull();
  });
});

describe('the page\'s type and places (Q3)', () => {
  it('sets the line 16 px under the code in Geist 14 at ink 66 percent, the address 8 px under it in Geist Mono 16, selectable', () => {
    expect(rulesOf('.phone-code__square')).toMatchObject({ display: 'block' });
    expect(rulesOf('.phone-code__line')).toEqual({ margin: '16px 0 0', font: '400 14px / 1.4 var(--look-font-sans)', color: 'var(--look-ink-2)' });
    expect(rulesOf('.phone-code__address')).toEqual({ margin: '8px 0 0', font: "400 16px / 1.4 'Geist Mono', ui-monospace, monospace", color: 'var(--look-ink)', 'user-select': 'text' });
  });
});
