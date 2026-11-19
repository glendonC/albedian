// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createEmptyObservationSession } from '@albedian/observation';
import { PREFERS_REDUCED_MOTION_QUERY, usePrefersReducedMotion, withReducedMotion } from '../usePrefersReducedMotion.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let reduced = false;
const listeners: Array<() => void> = [];
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  reduced = false;
  listeners.length = 0;
  (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
    get matches() {
      return query === PREFERS_REDUCED_MOTION_QUERY && reduced;
    },
    addEventListener: (_: string, listener: () => void) => listeners.push(listener),
    removeEventListener: () => {},
  });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as Record<string, unknown>).matchMedia;
});

const Probe = () => createElement('p', null, String(usePrefersReducedMotion()));

describe('usePrefersReducedMotion', () => {
  it('reads the setting and follows its changes', async () => {
    reduced = true;
    await act(async () => root.render(createElement(Probe)));
    expect(container.textContent).toBe('true');
    reduced = false;
    await act(async () => listeners.forEach((l) => l()));
    expect(container.textContent).toBe('false');
  });

  it('says no where the browser cannot tell', async () => {
    delete (window as unknown as Record<string, unknown>).matchMedia;
    await act(async () => root.render(createElement(Probe)));
    expect(container.textContent).toBe('false');
  });
});

describe('withReducedMotion', () => {
  it('sets the session\'s reducedMotion, and keeps the same session when it already matches', () => {
    const session = createEmptyObservationSession();
    expect(session.reducedMotion).toBe(false);
    expect(withReducedMotion(session, false)).toBe(session);
    const less = withReducedMotion(session, true);
    expect(less.reducedMotion).toBe(true);
    expect({ ...less, reducedMotion: false }).toEqual(session);
  });
});
