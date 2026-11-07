// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return null;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));

vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: {
    toggle: vi.fn(() => false),
    setMasterVolume: vi.fn(),
    setScaleRegime: vi.fn(),
    playDetentClick: vi.fn(),
  },
}));

vi.mock('../../audio/SkyAudioFeed.js', () => ({
  skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() },
}));

vi.mock('../CoalescingBar.js', () => ({
  CoalescingBar: () => null,
}));

const { CelestialTargetCard } = await import('../CelestialTargetCard.js');
const { SpatialObservatoryViewport } = await import(
  '../SpatialObservatoryViewport.js'
);

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const cssPath = path.resolve(__dirname, '../../styles/albedian.css');
const cssSource = fs.readFileSync(cssPath, 'utf8');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('CelestialTargetCard (experience-5, Moment 05 Apple Maps Place Card)', () => {
  it('renders nothing when selectedFinding is null', () => {
    const session = createSyntheticCalibratedSession();
    const noop = () => {};
    act(() => {
      root.render(
        createElement(CelestialTargetCard, {
          session,
          selectedFinding: null,
          onRunUtterance: noop,
          onPushToTalk: noop,
        })
      );
    });
    expect(container.querySelector('[data-testid="celestial-target-card"]')).toBeNull();
  });

  it('renders catalog_matched star with EpistemicBadgeDescriptor, Bailer-Jones distance interval, pixel centroid, catalog fields, and enables Go there', () => {
    const base = createSyntheticCalibratedSession();
    const session: ObservationSession = {
      ...base,
      machines: { ...base.machines, navigation: 'sky_registered' },
    };
    const baseBetelgeuse = session.observationSolution!.findings.find(
      (f) => f.id === 'finding_betelgeuse'
    )!;
    const betelgeuse: CelestialFinding = {
      ...baseBetelgeuse,
      catalogRef: baseBetelgeuse.catalogRef
        ? {
            ...baseBetelgeuse.catalogRef,
            colorIndexBpRp: 1.5,
            properMotionRaMasYr: 27.54,
            properMotionDecMasYr: 11.3,
          }
        : undefined,
    };
    const onRunUtterance = vi.fn();
    const onPushToTalk = vi.fn();

    act(() => {
      root.render(
        createElement(CelestialTargetCard, {
          session,
          selectedFinding: betelgeuse,
          onRunUtterance,
          onPushToTalk,
        })
      );
    });

    const card = container.querySelector('[data-testid="celestial-target-card"]') as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.getAttribute('data-hud-interactive')).toBe('true');

    const badge = container.querySelector('[data-testid="target-card-evidence-badge"]') as HTMLElement;
    expect(badge.getAttribute('data-evidence-class')).toBe('catalog_matched');
    expect(badge.textContent).toContain('CAT');
    expect(badge.textContent).toContain('Catalog Matched (Verified WCS)');

    const disclosure = container.querySelector('[data-testid="target-card-disclosure"]');
    expect(disclosure?.textContent).toBe(
      'Source pixel detection matched to catalog via independently verified WCS.'
    );

    expect(container.querySelector('[data-testid="target-card-title"]')?.textContent).toBe('Betelgeuse');
    expect(container.querySelector('[data-testid="target-card-subtitle"]')?.textContent).toContain('Orion');

    // Pixel row
    const pxRow = container.querySelector('[data-testid="target-card-pixel-row"]');
    expect(pxRow).not.toBeNull();
    expect(pxRow?.textContent).toContain('SNR');

    // Distance row with bounds
    const distRow = container.querySelector('[data-testid="target-card-distance-row"]');
    expect(distRow?.textContent).toContain('pc');
    expect(distRow?.textContent).toContain('ly');

    // Catalog row
    const catRow = container.querySelector('[data-testid="target-card-catalog-row"]');
    expect(catRow).not.toBeNull();
    expect(container.querySelector('[data-testid="target-card-spectral-type"]')?.textContent).toContain(
      betelgeuse.catalogRef?.spectralType ?? ''
    );
    expect(container.querySelector('[data-testid="target-card-bprp"]')?.textContent).toBe(
      'BP-RP: 1.50'
    );
    expect(container.querySelector('[data-testid="target-card-proper-motion"]')?.textContent).toContain(
      '(27.54, 11.30) mas/yr'
    );
    expect(container.querySelector('[data-testid="target-card-citation"]')?.textContent).toBe(
      betelgeuse.catalogRef?.citation
    );

    // Actions: Go there, Ask out loud, Back to moment (since navigation is sky_registered)
    const goBtn = container.querySelector('[data-testid="target-card-go-there"]') as HTMLButtonElement;
    expect(goBtn.disabled).toBe(false);
    act(() => goBtn.click());
    expect(onRunUtterance).toHaveBeenCalledWith('Take me to Betelgeuse', 'pointer');

    const askBtn = container.querySelector('[data-testid="target-card-ask"]') as HTMLButtonElement;
    act(() => askBtn.click());
    expect(onPushToTalk).toHaveBeenCalledTimes(1);

    const backBtn = container.querySelector('[data-testid="target-card-back"]') as HTMLButtonElement;
    expect(backBtn).not.toBeNull();
    act(() => backBtn.click());
    expect(onRunUtterance).toHaveBeenCalledWith('Take me back', 'pointer');
  });

  it('omits absent catalog quantities rather than inventing them, disables Go there when distance is unconstrained, and hides Back to moment in source mode', () => {
    const base = createSyntheticCalibratedSession();
    const session: ObservationSession = {
      ...base,
      machines: { ...base.machines, navigation: 'source' },
    };

    // A predicted planet without sourceDetection, without inferredDistancePc, and without spectralType/BP-RP/properMotion
    const predictedPlanet: CelestialFinding = {
      id: 'finding_jupiter_prd',
      observationSolutionId: session.observationSolution!.id,
      evidenceClass: 'predicted',
      title: 'Jupiter',
      subtitle: 'Solar System Planet',
      skyPosition: { raDeg: 88.5, decDeg: 7.2, uncertaintyArcsec: 0.5 },
      catalogRef: {
        id: 'cat_jup',
        provider: 'JPL Horizons',
        releaseVersion: 'DE440',
        catalogSourceId: '599',
        commonName: 'Jupiter',
        constellation: 'Taurus',
        objectType: 'planet',
        coordinateEpoch: 'J2000',
        raDeg: 88.5,
        decDeg: 7.2,
        apparentMagnitude: -2.4,
        citation: 'Meeus (1998) Astronomical Algorithms',
      },
      evidence: {
        id: 'ev_jup',
        evidenceClass: 'predicted',
        verificationMethod: 'Ephemeris at observedAtUTC',
        limitations: ['Computed from recording time and location; not recovered from camera pixels.'],
        verifiedByIndependentHoldout: false,
      },
    };

    act(() => {
      root.render(
        createElement(CelestialTargetCard, {
          session,
          selectedFinding: predictedPlanet,
          onRunUtterance: () => {},
          onPushToTalk: () => {},
        })
      );
    });

    const badge = container.querySelector('[data-testid="target-card-evidence-badge"]') as HTMLElement;
    expect(badge.getAttribute('data-evidence-class')).toBe('predicted');
    expect(badge.textContent).toContain('PRD');
    expect(badge.textContent).toContain('Predicted (Ephemeris / Model)');

    // No pixel row since sourceDetection is absent
    expect(container.querySelector('[data-testid="target-card-pixel-row"]')).toBeNull();

    // Distance explicitly disclosed as unconstrained
    const distRow = container.querySelector('[data-testid="target-card-distance-row"]');
    expect(distRow?.textContent).toContain('Distance Unconstrained');
    expect(distRow?.textContent).toContain('No reliable parallax or Bailer-Jones distance prior available.');

    // Absent optional catalog fields are omitted, never invented
    expect(container.querySelector('[data-testid="target-card-spectral-type"]')).toBeNull();
    expect(container.querySelector('[data-testid="target-card-bprp"]')).toBeNull();
    expect(container.querySelector('[data-testid="target-card-proper-motion"]')).toBeNull();

    // Go there is disabled because predictedPlanet is not catalog_matched with a distance prior
    const goBtn = container.querySelector('[data-testid="target-card-go-there"]') as HTMLButtonElement;
    expect(goBtn.disabled).toBe(true);

    // Back to moment button is hidden when already in source navigation state
    expect(container.querySelector('[data-testid="target-card-back"]')).toBeNull();

    // Limitations rendered verbatim
    const lims = container.querySelector('[data-testid="target-card-limitations"]');
    expect(lims?.textContent).toContain(
      'Computed from recording time and location; not recovered from camera pixels.'
    );
  });

  it('wires CelestialTargetCard into SpatialObservatoryViewport once past the landing overlay', () => {
    const base = createSyntheticCalibratedSession();
    const betelgeuse = base.observationSolution!.findings.find(
      (f) => f.id === 'finding_betelgeuse'
    )!;
    const onRunUtterance = vi.fn();
    const onPushToTalk = vi.fn();
    const noop = () => {};

    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session: base,
          setSession: noop,
          selectedFinding: betelgeuse,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance,
          onSelectFinding: noop,
          onPushToTalk,
          onTriggerUpload: noop,
        })
      );
    });

    // Hidden on landing overlay
    expect(container.querySelector('[data-testid="celestial-target-card"]')).toBeNull();

    // Click Launch Albedian
    const launchBtn = container.querySelector('[data-testid="enter-observatory-btn"]') as HTMLButtonElement;
    act(() => launchBtn.click());

    expect(container.querySelector('[data-testid="celestial-target-card"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="target-card-title"]')?.textContent).toBe('Betelgeuse');

    const goBtn = container.querySelector('[data-testid="target-card-go-there"]') as HTMLButtonElement;
    act(() => goBtn.click());
    expect(onRunUtterance).toHaveBeenCalledWith('Take me to Betelgeuse', 'pointer');
  });

  it('defines desktop right-docked card and narrow-width bottom-sheet rules in albedian.css', () => {
    expect(cssSource).toMatch(/\.alb-target-card\s*\{[^}]*position:\s*fixed;/);
    expect(cssSource).toMatch(/\.alb-target-card\s*\{[^}]*right:\s*32px;/);
    expect(cssSource).toMatch(/@media\s*\(max-width:\s*720px\)[\s\S]*?\.alb-target-card\s*\{[^}]*left:\s*12px;[^}]*right:\s*12px;/);
  });
});
