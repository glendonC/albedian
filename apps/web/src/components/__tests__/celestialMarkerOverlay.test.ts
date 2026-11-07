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

const { CelestialMarkerOverlay, projectFindingToViewport } = await import(
  '../CelestialMarkerOverlay.js'
);
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

function buildFourClassFindings(baseSession: ObservationSession): CelestialFinding[] {
  const sha = baseSession.sourceAsset.sha256;
  const solId = baseSession.observationSolution.id;
  return [
    {
      id: 'f_obs',
      observationSolutionId: solId,
      evidenceClass: 'observed',
      title: 'Centroid #1',
      subtitle: 'SNR 18.4',
      sourceDetection: {
        assetId: baseSession.sourceAsset.id,
        frameTimeSec: 0,
        xPx: 480,
        yPx: 270,
        flux: 1200,
        snr: 18.4,
        fwhmPx: 2.8,
        centroidUncertaintyPx: 0.18,
      },
      skyPosition: { raDeg: 85.0, decDeg: 1.0, uncertaintyArcsec: 12 },
      evidence: {
        id: 'ev_obs',
        evidenceClass: 'observed',
        verificationMethod: 'DAOStarFinder centroid on original frame',
        sourceAssetSha256: sha,
        limitations: ['Measured from camera pixels only.'],
        verifiedByIndependentHoldout: true,
      },
    },
    {
      id: 'f_cat',
      observationSolutionId: solId,
      evidenceClass: 'catalog_matched',
      title: 'Betelgeuse',
      subtitle: 'Alpha Orionis · Orion',
      sourceDetection: {
        assetId: baseSession.sourceAsset.id,
        frameTimeSec: 0,
        xPx: 960,
        yPx: 540,
        flux: 9400,
        snr: 92.0,
        fwhmPx: 3.1,
        centroidUncertaintyPx: 0.08,
      },
      skyPosition: { raDeg: 88.7929, decDeg: 7.4071, uncertaintyArcsec: 1.4 },
      inferredDistancePc: {
        lower: 131.06,
        median: 152.67,
        upper: 182.82,
        method: 'geometric_parallax',
      },
      evidence: {
        id: 'ev_cat',
        evidenceClass: 'catalog_matched',
        verificationMethod: 'Held-out WCS match to Hipparcos',
        sourceAssetSha256: sha,
        pixelResidualPx: 0.41,
        limitations: ['Distance from Hipparcos 2007 parallax.'],
        verifiedByIndependentHoldout: true,
      },
    },
    {
      id: 'f_prd',
      observationSolutionId: solId,
      evidenceClass: 'predicted',
      title: 'Jupiter',
      subtitle: 'Solar System Planet',
      skyPosition: { raDeg: 88.5, decDeg: 7.2, uncertaintyArcsec: 0.5 },
      evidence: {
        id: 'ev_prd',
        evidenceClass: 'predicted',
        verificationMethod: 'Meeus (1998) ephemeris at observedAtUTC',
        limitations: ['Computed from ephemeris; not recovered from camera pixels.'],
        verifiedByIndependentHoldout: false,
      },
    },
    {
      id: 'f_sim',
      observationSolutionId: solId,
      evidenceClass: 'simulated',
      title: 'Orion Cone Sightline',
      subtitle: '3D Catalog Model',
      skyPosition: { raDeg: 88.9, decDeg: 7.5, uncertaintyArcsec: 5.0 },
      cartesianParsecICRS: [-3.1, 149.8, 19.6],
      evidence: {
        id: 'ev_sim',
        evidenceClass: 'simulated',
        verificationMethod: '3D catalog projection',
        limitations: ['Drawn or computed by the app; not measured in the source recording.'],
        verifiedByIndependentHoldout: false,
      },
    },
  ];
}

describe('CelestialMarkerOverlay (experience-4)', () => {
  it('projects source_2d pixel detections only for findings with sourceDetection and respects cropTransform', () => {
    const base = createSyntheticCalibratedSession();
    const findings = buildFourClassFindings(base);
    const session: ObservationSession = {
      ...base,
      sourceAsset: { ...base.sourceAsset, width: 1920, height: 1080 },
      spatialView: { ...base.spatialView, mode: 'source_2d' },
      observationSolution: { ...base.observationSolution, findings },
    };

    // f_obs at (480, 270) on 1920x1080 -> u=0.25, v=0.25 -> (360, 225) on 1440x900
    const pObs = projectFindingToViewport(findings[0]!, session, 1440, 900);
    expect(pObs.visible).toBe(true);
    expect(pObs.xPct).toBeCloseTo(25, 3);
    expect(pObs.yPct).toBeCloseTo(25, 3);
    expect(pObs.xPx).toBe(360);
    expect(pObs.yPx).toBe(225);

    // f_prd has no sourceDetection, so it is never drawn as a pixel marker in source_2d
    const pPrd = projectFindingToViewport(findings[2]!, session, 1440, 900);
    expect(pPrd.visible).toBe(false);

    // Zooming/panning cropTransform shifts the projected pixel position and hides off-screen markers
    const zoomedSession: ObservationSession = {
      ...session,
      cropTransform: { zoom: 2, panX: 0.1, panY: 0.05 },
    };
    const pZoomed = projectFindingToViewport(findings[0]!, zoomedSession, 1440, 900);
    expect(pZoomed.visible).toBe(true);
    expect(pZoomed.xPct).toBeCloseTo(10, 3);
    expect(pZoomed.yPct).toBeCloseTo(5, 3);

    const offscreenSession: ObservationSession = {
      ...session,
      cropTransform: { zoom: 2, panX: -0.6, panY: 0 },
    };
    expect(projectFindingToViewport(findings[0]!, offscreenSession, 1440, 900).visible).toBe(false);
  });

  it('projects registered_sky and catalog_3d findings via camera-rig and renders all four EvidenceClass silhouettes and shortCodes', () => {
    const base = createSyntheticCalibratedSession();
    const findings = buildFourClassFindings(base);
    // Look along RA 88.7929 deg, Dec 7.4071 deg so all 4 findings are in front of the camera
    const session: ObservationSession = {
      ...base,
      selectedFindingId: 'f_cat',
      focusedFindingId: 'f_obs',
      spatialView: {
        ...base.spatialView,
        mode: 'registered_sky',
        observerOriginParsec: [0, 0, 0],
        activeOverlays: {
          ...base.spatialView.activeOverlays,
          observedMarkers: true,
          predictedEphemerides: true,
        },
      },
      observationSolution: {
        ...base.observationSolution,
        findings: findings.map((f, idx) => ({
          ...f,
          skyPosition: { raDeg: 0, decDeg: -90 + idx * 2, uncertaintyArcsec: 1 },
          cartesianParsecICRS: [0, 0, -100 - idx * 10] as [number, number, number],
        })),
      },
    };

    const onSelectFinding = vi.fn();
    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: session.observationSolution.findings[1]!,
          onSelectFinding,
          viewportWidthPx: 1440,
          viewportHeightPx: 900,
        })
      );
    });

    const obsBtn = container.querySelector('[data-testid="celestial-marker-f_obs"]') as HTMLButtonElement;
    const catBtn = container.querySelector('[data-testid="celestial-marker-f_cat"]') as HTMLButtonElement;
    const prdBtn = container.querySelector('[data-testid="celestial-marker-f_prd"]') as HTMLButtonElement;
    const simBtn = container.querySelector('[data-testid="celestial-marker-f_sim"]') as HTMLButtonElement;

    expect(obsBtn).not.toBeNull();
    expect(catBtn).not.toBeNull();
    expect(prdBtn).not.toBeNull();
    expect(simBtn).not.toBeNull();

    // Non-color shape differentiation from getMarkerVisualSpec
    expect(obsBtn.getAttribute('data-marker-shape')).toBe('solid_dot_tick');
    expect(catBtn.getAttribute('data-marker-shape')).toBe('concentric_ring_elbow');
    expect(prdBtn.getAttribute('data-marker-shape')).toBe('dashed_circle');
    expect(simBtn.getAttribute('data-marker-shape')).toBe('bracket_reticle');

    // Short codes from getEpistemicBadgeDescriptor
    expect(obsBtn.textContent).toContain('OBS');
    expect(catBtn.textContent).toContain('CAT');
    expect(prdBtn.textContent).toContain('PRD');
    expect(simBtn.textContent).toContain('SIM');

    // Selection and aria attributes
    expect(catBtn.getAttribute('aria-pressed')).toBe('true');
    expect(obsBtn.getAttribute('aria-pressed')).toBe('false');
    expect(catBtn.getAttribute('aria-label')).toContain('Betelgeuse');
    expect(catBtn.getAttribute('aria-label')).toContain('Catalog Matched (Verified WCS)');
    expect(catBtn.getAttribute('aria-label')).toContain('Selected');
    expect(obsBtn.style.width).toBe('44px');
    expect(obsBtn.style.height).toBe('44px');

    // Clicking a marker invokes onSelectFinding with findingId and projected { xPx, yPx }
    act(() => obsBtn.click());
    expect(onSelectFinding).toHaveBeenCalledTimes(1);
    expect(onSelectFinding).toHaveBeenCalledWith('f_obs', expect.objectContaining({ xPx: expect.any(Number), yPx: expect.any(Number) }));
  });

  it('hides observed or predicted markers in registered_sky when their activeOverlays toggle is false', () => {
    const base = createSyntheticCalibratedSession();
    const findings = buildFourClassFindings(base).map((f, idx) => ({
      ...f,
      skyPosition: { raDeg: 0, decDeg: -90 + idx * 2, uncertaintyArcsec: 1 },
      cartesianParsecICRS: [0, 0, -100 - idx * 10] as [number, number, number],
    }));

    const session: ObservationSession = {
      ...base,
      spatialView: {
        ...base.spatialView,
        mode: 'registered_sky',
        activeOverlays: {
          ...base.spatialView.activeOverlays,
          observedMarkers: false,
          predictedEphemerides: false,
        },
      },
      observationSolution: { ...base.observationSolution, findings },
    };

    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: null,
          onSelectFinding: () => {},
        })
      );
    });

    expect(container.querySelector('[data-testid="celestial-marker-f_obs"]')).toBeNull();
    expect(container.querySelector('[data-testid="celestial-marker-f_prd"]')).toBeNull();
    expect(container.querySelector('[data-testid="celestial-marker-f_cat"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="celestial-marker-f_sim"]')).not.toBeNull();
  });

  it('wires CelestialMarkerOverlay into SpatialObservatoryViewport once past the landing overlay', () => {
    const base = createSyntheticCalibratedSession();
    const onSelectFinding = vi.fn();
    const noop = () => {};

    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session: base,
          setSession: noop,
          selectedFinding: base.observationSolution.findings[0] ?? null,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onSelectFinding,
          onPushToTalk: noop,
          onTriggerUpload: noop,
        })
      );
    });

    // Hidden on landing overlay
    expect(container.querySelector('[data-testid="celestial-marker-overlay"]')).toBeNull();

    // Click Launch Albedian
    const launchBtn = container.querySelector('[data-testid="enter-observatory-btn"]') as HTMLButtonElement;
    act(() => launchBtn.click());

    expect(container.querySelector('[data-testid="celestial-marker-overlay"]')).not.toBeNull();
    const markerBtn = container.querySelector('[data-testid="celestial-marker-finding_betelgeuse"]') as HTMLButtonElement;
    expect(markerBtn).not.toBeNull();
    act(() => markerBtn.click());
    expect(onSelectFinding).toHaveBeenCalledWith(
      'finding_betelgeuse',
      expect.objectContaining({ xPx: expect.any(Number), yPx: expect.any(Number) })
    );
  });

  it('defines marker overlay and 44x44px touch target rules in albedian.css', () => {
    expect(cssSource).toMatch(/\.alb-marker-overlay\s*\{[^}]*pointer-events:\s*none;/);
    expect(cssSource).toMatch(/\.alb-marker-hit\s*\{[^}]*position:\s*absolute;/);
    expect(cssSource).toMatch(/\.alb-marker-hit\s*\{[^}]*transform:\s*translate\(-50%,\s*-50%\);/);
    expect(cssSource).toMatch(/\.alb-marker-shortcode\s*\{[^}]*font-family:\s*var\(--alb-font-mono\);/);
  });
});
