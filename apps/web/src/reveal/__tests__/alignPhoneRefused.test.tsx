// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample, POSE } from '../../components/__tests__/poseSessions.js';
import type { AlignPose } from '../alignPose.js';
import { RevealPhoto } from '../RevealPhoto.js';
import type { Restoration } from '../restoration.js';

// D24's refused state at phone width (UIUX-align-READY.md, drawn 19 Nov 18:01 as alp-refused): the kept rings over the
// photo, the Predicted tag at 606, the two sentences at 646 and 704, Line it up again centred at 772 where Solve from
// here was, then Show predicted sky and Restore under it, reached by scrolling, all 44 px.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const starRef = POSE.findings.find((f) => f.catalogRef?.objectType === 'star')!.catalogRef!;
const target = (id: string, title: string, alt: number, az: number, mag: number) =>
  ({ ...template, id, title, skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az }, catalogRef: { ...starRef, apparentMagnitude: mag } }) as CelestialFinding;
function unmatched(): ObservationSession {
  const session = poseSession(base);
  return {
    ...session,
    sourceAsset: { ...session.sourceAsset!, width: 3024, height: 4032, displayOrientation: undefined, cameraMetadata: { focalLength35mmMm: 26 } },
    observationSolution: {
      ...session.observationSolution!,
      findings: [...session.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted'), target('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16), target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38)],
    } as ObservationSolution,
  } as ObservationSession;
}
const KEPT: AlignPose = { azDeg: 150, altDeg: 30, rollDeg: 0, fovXDeg: 60, aspect: 4 / 3 };

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
const q = (selector: string) => container.querySelector(selector);
const button = (label: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined;
function showPhone(extra: Record<string, unknown> = {}) {
  const session = unmatched();
  act(() =>
    root.render(
      createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, onShowPredictedSky: () => {}, alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest', name: 'Fomalhaut' } }, ...extra })
    )
  );
}
function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let rules = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    rules += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(rules, selector).toBe(1);
  return found;
}

describe('D24 refused at phone width', () => {
  it('leads with the Predicted tag, the two sentences, Line it up again, then Show predicted sky, all 44 px', () => {
    showPhone({ onRestore: () => {}, restoration: { phase: 'idle' } as Restoration });
    const status = q('.reveal-status.is-refused')!;
    expect(status.className).toBe('reveal-status is-unmatched is-phone is-refused has-predicted');
    expect([...status.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'reveal-status__lead', 'align-refused__kept', 'reveal-phone-go', 'reveal-phone-more']);
    expect(status.children[0]!.getAttribute('data-evidence-class')).toBe('predicted');
    expect(status.children[1]!.textContent).toBe('No stars matched near your alignment.');
    expect(status.children[2]!.textContent).toBe('Your alignment is kept, drawn as predicted.');
    // Restore under Show predicted sky, as alp-refused draws it, now that the phone restores (D38)
    expect([...status.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Line it up again', 'Show predicted sky', 'Restore']);
    for (const b of status.querySelectorAll('button')) expect(b.className).toContain('look-size-phone');
    // The kept rings stay drawn over the photo at the phone's 11 px size, amber and dashed, and the no-match screen's own actions are not offered
    expect(q('[data-testid="early-marks"]')!.className).toBe('align-layer is-still is-phone');
    expect(button('Line it up yourself')).toBeUndefined();
  });

  it('Line it up again reopens the phone aligning screen at the kept pose', () => {
    showPhone();
    act(() => button('Line it up again')!.click());
    expect(q('.align-phone')).not.toBeNull();
    expect(q('.reveal-status.is-refused')).toBeNull();
  });

  it('places each piece where alp-refused draws it, the whole screen scrolling with the photo and the rings', () => {
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-refused')).toEqual({ height: '970px', padding: '0' });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-refused > .look-tag')).toEqual({ position: 'absolute', left: '24px', top: '606px', margin: '0' });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-refused > .reveal-status__lead')).toEqual({ position: 'absolute', left: '24px', right: '24px', top: '646px' });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-refused > .align-refused__kept')).toEqual({ position: 'absolute', left: '24px', right: '24px', top: '704px', margin: '0' });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-refused > .reveal-phone-go')).toEqual({ position: 'absolute', left: '0', right: '0', top: '772px' });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-refused > .reveal-phone-more')).toEqual({ position: 'absolute', left: '0', right: '0', top: '832px', display: 'flex', 'flex-direction': 'column', 'align-items': 'center', gap: '10px' });
    expect(declarationsOf(':root[data-phone-scroll] .align-layer.is-still')).toEqual({ translate: '0 calc(-1 * var(--phone-scroll, 0px))' });
  });
});
