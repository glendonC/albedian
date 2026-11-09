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

const { CelestialMarkerOverlay, chooseLabelledFindingIds, projectFindingToViewport } = await import(
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
  const sha = baseSession.sourceAsset!.sha256;
  const solId = baseSession.observationSolution!.id;
  return [
    {
      id: 'f_obs',
      observationSolutionId: solId,
      evidenceClass: 'observed',
      title: 'Centroid #1',
      subtitle: 'SNR 18.4',
      sourceDetection: {
        assetId: baseSession.sourceAsset!.id,
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
        assetId: baseSession.sourceAsset!.id,
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
      sourceAsset: { ...base.sourceAsset!, width: 1920, height: 1080 },
      spatialView: { ...base.spatialView, mode: 'source_2d' },
      observationSolution: { ...base.observationSolution!, findings },
    };

    // 1920x1080 fits 1440x900 as 1440x810 from y=45 (shape kept, letterboxed). f_obs at (480, 270) is the centre of
    // its pixel, (480.5, 270.5) on the photo -> u=480.5/1920, v=270.5/1080 -> (360.375, 45 + 270.5 * 0.75 = 247.875)
    const pObs = projectFindingToViewport(findings[0]!, session, 1440, 900);
    expect(pObs.visible).toBe(true);
    expect(pObs.xPct).toBeCloseTo(25.026, 3);
    expect(pObs.yPct).toBeCloseTo(27.542, 3);
    expect(pObs.xPx).toBe(360);
    expect(pObs.yPx).toBe(248);

    // f_prd has no sourceDetection, so it is never drawn as a pixel marker in source_2d
    const pPrd = projectFindingToViewport(findings[2]!, session, 1440, 900);
    expect(pPrd.visible).toBe(false);

    // Zooming/panning cropTransform shifts the projected pixel position and hides off-screen markers
    const zoomedSession: ObservationSession = {
      ...session,
      cropTransform: { zoom: 2, panX: 0.1, panY: 0.05 },
    };
    // u' = (480.5/1920 - 0.5) * 2 + 0.5 + 0.1 = 0.100521 -> 10.052 %; v' = (270.5/1080 - 0.5) * 2 + 0.55 = 0.050926
    // -> y = 45 + 0.050926 * 810 = 86.25 -> 9.583 %
    const pZoomed = projectFindingToViewport(findings[0]!, zoomedSession, 1440, 900);
    expect(pZoomed.visible).toBe(true);
    expect(pZoomed.xPct).toBeCloseTo(10.052, 3);
    expect(pZoomed.yPct).toBeCloseTo(9.583, 3);

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
        ...base.observationSolution!,
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
          selectedFinding: session.observationSolution!.findings[1]!,
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
      observationSolution: { ...base.observationSolution!, findings },
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
          selectedFinding: base.observationSolution!.findings[0] ?? null,
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

/** A photo solve the size of the rehearsal's: many named and unnamed catalog stars and many plain detections. */
function buildCrowdedSession(namedCount: number, unnamedCount: number, observedCount: number): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const sha = base.sourceAsset!.sha256;
  const solId = base.observationSolution!.id;
  const assetId = base.sourceAsset!.id;
  const findings: CelestialFinding[] = [];
  let n = 0;
  const detection = () => {
    n += 1;
    return {
      assetId,
      frameTimeSec: 0,
      xPx: 40 + ((n * 37) % 1840),
      yPx: 40 + ((n * 53) % 1000),
      flux: 1000 - n,
      snr: 20,
      fwhmPx: 3,
      centroidUncertaintyPx: 0.1,
    };
  };
  const catalogFinding = (id: string, name: string, sourceId: string, mag: number): CelestialFinding => ({
    id,
    observationSolutionId: solId,
    evidenceClass: 'catalog_matched',
    title: name,
    subtitle: 'test row',
    sourceDetection: detection(),
    skyPosition: { raDeg: 80, decDeg: 0, uncertaintyArcsec: 10 },
    catalogRef: {
      id: `ref_${id}`,
      provider: 'Hipparcos',
      releaseVersion: 'Hipparcos 2007',
      catalogSourceId: sourceId,
      commonName: name,
      constellation: 'Orion',
      objectType: 'star',
      coordinateEpoch: 'J2000.0',
      raDeg: 80,
      decDeg: 0,
      apparentMagnitude: mag,
      citation: 'test',
    },
    evidence: {
      id: `ev_${id}`,
      evidenceClass: 'catalog_matched',
      verificationMethod: 'test',
      sourceAssetSha256: sha,
      limitations: ['test'],
      verifiedByIndependentHoldout: true,
    },
  }) as CelestialFinding;
  // Named stars get magnitudes out of order so the test sees a sort, not the input order
  for (let i = 0; i < namedCount; i++) {
    const mag = ((i * 7) % namedCount) / 4;
    findings.push(catalogFinding(`named_${i}`, `Star ${String.fromCharCode(65 + (i % 26))}${i}`, `HIP ${1000 + i}`, mag));
  }
  for (let i = 0; i < unnamedCount; i++) {
    const id = `Gaia DR3 ${900000 + i}`;
    findings.push(catalogFinding(`unnamed_${i}`, id, id, -5 + i * 0.01));
  }
  for (let i = 0; i < observedCount; i++) {
    findings.push({
      id: `obs_${i}`,
      observationSolutionId: solId,
      evidenceClass: 'observed',
      title: `Observed Point Source #${i}`,
      subtitle: 'test',
      sourceDetection: detection(),
      evidence: {
        id: `ev_obs_${i}`,
        evidenceClass: 'observed',
        verificationMethod: 'test',
        sourceAssetSha256: sha,
        limitations: ['test'],
        verifiedByIndependentHoldout: false,
      },
    } as CelestialFinding);
  }
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, width: 1920, height: 1080 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
    observationSolution: { ...base.observationSolution!, findings },
  } as ObservationSession;
}

function labelledIds(): string[] {
  return [...container.querySelectorAll('.alb-marker-hit')]
    .filter((el) => el.querySelector('.alb-marker-label'))
    .map((el) => (el as HTMLElement).dataset.testid!.replace('celestial-marker-', ''));
}

describe('CelestialMarkerOverlay label budget (experience-9b)', () => {
  it('labels only the brightest named stars: 8 on a phone, 20 on a laptop', () => {
    const session = buildCrowdedSession(33, 30, 705);
    const brightestNamed = session
      .observationSolution!.findings.filter((f) => f.id.startsWith('named_'))
      .sort((a, b) => a.catalogRef!.apparentMagnitude! - b.catalogRef!.apparentMagnitude!)
      .map((f) => f.id);

    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: null,
          onSelectFinding: () => {},
          viewportWidthPx: 390,
          viewportHeightPx: 844,
        })
      );
    });
    expect(labelledIds().sort()).toEqual(brightestNamed.slice(0, 8).sort());

    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: null,
          onSelectFinding: () => {},
          viewportWidthPx: 1440,
          viewportHeightPx: 900,
        })
      );
    });
    expect(labelledIds().sort()).toEqual(brightestNamed.slice(0, 20).sort());
  });

  it('keeps every finding on the photo pickable, drawing the unlabelled ones as small dots', () => {
    const session = buildCrowdedSession(33, 30, 705);
    const onSelectFinding = vi.fn();
    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: null,
          onSelectFinding,
          viewportWidthPx: 390,
          viewportHeightPx: 844,
        })
      );
    });
    const all = container.querySelectorAll('.alb-marker-hit');
    expect(all.length).toBe(768);
    const quiet = container.querySelectorAll('.alb-marker-hit.is-quiet');
    expect(quiet.length).toBe(768 - 8);
    // An unnamed catalog star and a plain detection are dots without a label
    const gaia = container.querySelector('[data-testid="celestial-marker-unnamed_0"]') as HTMLButtonElement;
    expect(gaia.classList.contains('is-quiet')).toBe(true);
    expect(gaia.querySelector('.alb-marker-label')).toBeNull();
    const obs = container.querySelector('[data-testid="celestial-marker-obs_0"]') as HTMLButtonElement;
    expect(obs.querySelector('.alb-marker-label')).toBeNull();
    // Still announced by name and still selectable
    expect(obs.getAttribute('aria-label')).toBeTruthy();
    act(() => obs.click());
    expect(onSelectFinding).toHaveBeenCalledWith('obs_0', expect.objectContaining({ xPx: expect.any(Number) }));
    // Labelled markers come last so they sit above the dots
    const order = [...all].map((el) => el.classList.contains('is-quiet'));
    expect(order.indexOf(false)).toBe(768 - 8);
  });

  it('always labels the selected finding, even a plain detection', () => {
    const session = buildCrowdedSession(33, 30, 705);
    const selected = session.observationSolution!.findings.find((f) => f.id === 'obs_100')!;
    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: selected,
          onSelectFinding: () => {},
          viewportWidthPx: 390,
          viewportHeightPx: 844,
        })
      );
    });
    const marker = container.querySelector('[data-testid="celestial-marker-obs_100"]') as HTMLButtonElement;
    expect(marker.classList.contains('is-quiet')).toBe(false);
    expect(marker.querySelector('.alb-marker-label')).not.toBeNull();
    expect(labelledIds().length).toBe(9);
  });
});

describe('CelestialMarkerOverlay label budget without a magnitude (integrity-25)', () => {
  it('ranks a named finding whose catalog gives no magnitude after every one that gives one', () => {
    // The brightest named star loses its magnitude, as a catalog that gives none would leave it
    const findings = buildCrowdedSession(33, 0, 0).observationSolution!.findings.map((f) => {
      if (f.id !== 'named_0') return f;
      const catalogRef = { ...f.catalogRef! };
      delete catalogRef.apparentMagnitude;
      return { ...f, catalogRef } as CelestialFinding;
    });
    const brightestWithMagnitude = findings
      .filter((f) => f.id.startsWith('named_') && f.catalogRef!.apparentMagnitude !== undefined)
      .sort((a, b) => a.catalogRef!.apparentMagnitude! - b.catalogRef!.apparentMagnitude!)
      .map((f) => f.id);
    expect([...chooseLabelledFindingIds(findings, 390, [])].sort()).toEqual(brightestWithMagnitude.slice(0, 8).sort());
  });
});

describe('CelestialMarkerOverlay label budget, auditor gaps (experience-9e)', () => {
  it('gives the phone budget to predicted objects before any star: 10 predicted and 33 named stars give 8 predicted labels', () => {
    const base = createSyntheticCalibratedSession();
    const prdTemplate = buildFourClassFindings(base).find((f) => f.id === 'f_prd')!;
    const named = buildCrowdedSession(33, 0, 0).observationSolution!.findings;
    const predicted = Array.from({ length: 10 }, (_, i) => ({ ...prdTemplate, id: `prd_${i}`, title: `Predicted ${i}` }));
    // Everything sits in front of the camera, spread over the phone screen
    const placed = [...predicted, ...named].map((f, i) => ({
      ...f,
      skyPosition: { raDeg: 0, decDeg: -90 + i * 0.1, uncertaintyArcsec: 1 },
      cartesianParsecICRS: [(i % 10) * 2 - 9, Math.floor(i / 10) * 4 - 10, -100] as [number, number, number],
    })) as CelestialFinding[];
    const session: ObservationSession = {
      ...base,
      selectedFindingId: null,
      focusedFindingId: null,
      spatialView: {
        ...base.spatialView,
        mode: 'registered_sky',
        observerOriginParsec: [0, 0, 0],
        activeOverlays: { ...base.spatialView.activeOverlays, observedMarkers: true, predictedEphemerides: true },
      },
      observationSolution: { ...base.observationSolution!, findings: placed },
    };
    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: null,
          onSelectFinding: () => {},
          viewportWidthPx: 390,
          viewportHeightPx: 844,
        })
      );
    });
    // All 43 are on screen, so the budget, not visibility, decides the labels
    expect(container.querySelectorAll('.alb-marker-hit').length).toBe(43);
    const labelled = labelledIds();
    expect(labelled.length).toBe(8);
    expect(labelled.every((id) => id.startsWith('prd_'))).toBe(true);
    expect(labelled.some((id) => id.startsWith('named_'))).toBe(false);
  });

  it('keeps the evidence-class glyph and data on a quiet marker', () => {
    const session = buildCrowdedSession(33, 30, 705);
    act(() => {
      root.render(
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: null,
          onSelectFinding: () => {},
          viewportWidthPx: 390,
          viewportHeightPx: 844,
        })
      );
    });
    for (const [id, evidenceClass] of [
      ['obs_0', 'observed'],
      ['unnamed_0', 'catalog_matched'],
    ] as const) {
      const marker = container.querySelector(`[data-testid="celestial-marker-${id}"]`) as HTMLButtonElement;
      expect(marker.classList.contains('is-quiet')).toBe(true);
      expect(marker.dataset.evidenceClass).toBe(evidenceClass);
      expect(marker.dataset.markerShape).toBeTruthy();
      expect(marker.querySelector('svg')).not.toBeNull();
    }
  });
});
