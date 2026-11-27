// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';
import { FlightPills } from '../../spatial/flight/ConePieces.js';

// flight-pills-foot-one (DECIDED-BY-LEAD at the line stamped 07:21 on WEB-UX's note: on a phone, in a flight, the craft
// pill and Your photo's view stood at a fixed top of 712, below the window at Safari's upright 714, the app's Open sheet
// at 650 and every sideways size, with nothing to scroll to them): the row stands by the window's foot as the ask bar
// does since phone-foot-one, 132 px above it (its place in 844, unchanged there), so it is inside every window and keeps
// its 16 px under the readout, which stands 148 px above the foot.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

function phoneRow(): HTMLElement {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => root.render(createElement(FlightPills, { craftPill: null, cone: false, onCone: () => {}, phone: true })));
  return host.querySelector('[data-testid="flight-pills"]') as HTMLElement;
}
/** The phone row's top as reveal.css places it (phone-sideways-four moved it there from ConePieces.tsx's inline style). */
function phoneTop(): string {
  let top = '';
  postcss.parse(css).walkRules((rule) => {
    if (rule.parent?.type === 'root' && rule.selector === '.flight-pills.is-phone') rule.walkDecls('top', (d) => void (top = d.value));
  });
  return top;
}
const at = (value: string, h: number) => {
  const m = /^calc\(100% - (\d+)px\)$/.exec(value);
  if (!m) throw new Error(value);
  return h - Number(m[1]);
};

describe("the phone flight's pill row stands by the window's foot", () => {
  it('stands 132 px above the foot: 712 in 844 as approved, inside every phone window', () => {
    const row = phoneRow();
    expect(row.className).toBe('flight-pills is-phone');
    expect(row.getAttribute('style')).toBeNull();
    expect(phoneTop()).toBe('calc(100% - 132px)');
    expect(at(phoneTop(), 844)).toBe(712);
    for (const h of [292, 320, 402, 650, 714, 844, 932]) {
      const top = at(phoneTop(), h);
      expect(top + 44, String(h)).toBeLessThanOrEqual(h);
      expect(top, String(h)).toBeGreaterThanOrEqual(0);
    }
  });

  it('keeps its 16 px under the readout, which stands 148 px above the foot', () => {
    let bottom: string | undefined;
    postcss.parse(css).walkRules((rule) => {
      if (rule.parent?.type === 'root' && rule.selector === '.flight-readout.is-phone') rule.walkDecls('bottom', (d) => void (bottom = d.value));
    });
    expect(bottom).toBe('148px');
    expect(at(phoneTop(), 844) - (844 - 148)).toBe(16);
  });
});
