// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { createSessionForSourceAsset, dispatchAlbedianAction } from '@albedian/observation';
import {
  createSyntheticCalibratedSession,
  createSyntheticUnresolvedSession,
} from '@albedian/observation/testing';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';

vi.mock('../SparkPortalRenderer.js', () => ({
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

vi.mock('../../components/CoalescingBar.js', () => ({
  CoalescingBar: () => null,
}));

import { ObservationSolveAndCompareHud } from '../ObservationSolveAndCompareHud.js';
import { SpatialObservatoryViewport } from '../../components/SpatialObservatoryViewport.js';

describe('ObservationSolveAndCompareHud (moments 02 & 03: solve receipt & A/B wipe HUD)', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders calibrated solve receipt with held-out verification metrics, solver provenance, and dispatches sky.show (measured)', async () => {
    let latestSession = createSyntheticCalibratedSession();
    const q = latestSession.observationSolution!.quality!;
    const solver = latestSession.observationSolution!.solver!;

    await act(async () => {
      root.render(
        React.createElement(ObservationSolveAndCompareHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });

    const hud = container.querySelector<HTMLElement>(
      '[data-testid="observation-solve-compare-hud"]'
    );
    expect(hud).not.toBeNull();
    expect(hud?.getAttribute('data-hud-interactive')).toBe('true');
    expect(hud?.getAttribute('aria-label')).toBe(
      'Observation solve receipt and restoration compare controls'
    );

    const catBadge = getEpistemicBadgeDescriptor('catalog_matched');
    const badgeEl = container.querySelector<HTMLElement>(
      '[data-testid="solve-receipt-badge"]'
    );
    expect(badgeEl?.textContent).toContain(catBadge.shortCode);
    expect(badgeEl?.textContent).toContain(catBadge.label);

    const metricsEl = container.querySelector<HTMLElement>(
      '[data-testid="solve-receipt-metrics"]'
    );
    expect(metricsEl?.textContent).toContain(
      `${q.detectedCandidateCount} detected`
    );
    expect(metricsEl?.textContent).toContain(
      `${q.matchedInlierCount} matched inliers`
    );
    expect(metricsEl?.textContent).toContain(
      `${q.heldOutInliers}/${q.heldOutPairCount} held-out inliers`
    );
    expect(metricsEl?.textContent).toContain(
      `${q.medianResidualPx!.toFixed(2)} px`
    );

    const solverEl = container.querySelector<HTMLElement>(
      '[data-testid="solve-receipt-solver"]'
    );
    expect(solverEl?.textContent).toContain(solver.name);
    expect(solverEl?.textContent).toContain(solver.catalogRelease);

    const regBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="solve-register-sky-btn"]'
    )!;
    expect(regBtn.disabled).toBe(false);
    expect(regBtn.textContent).toContain('Register Sky');

    await act(async () => {
      regBtn.click();
    });
    expect(latestSession.machines.navigation).toBe('sky_registered');
    expect(latestSession.spatialView.mode).toBe('registered_sky');
  });

  it('says no solver ran on a stored file that has not been solved, instead of counting zeros (experience-9f)', async () => {
    const asset = createSyntheticCalibratedSession().sourceAsset!;
    const pending = createSessionForSourceAsset(asset, 'ready');
    expect(pending.observationSolution!.status).toBe('pending');
    expect(pending.observationSolution!.quality).toBeUndefined();
    expect(pending.observationSolution!.solver).toBeUndefined();

    await act(async () => {
      root.render(React.createElement(ObservationSolveAndCompareHud, { session: pending, setSession: () => {} }));
    });

    const metricsEl = container.querySelector<HTMLElement>('[data-testid="solve-receipt-metrics"]');
    expect(metricsEl?.textContent).toContain('No solver ran');
    expect(metricsEl?.textContent).not.toMatch(/\d+ detected|matched inliers|held-out/);
    const solverEl = container.querySelector<HTMLElement>('[data-testid="solve-receipt-solver"]');
    expect(solverEl?.textContent).toContain('No solver ran');
    expect(solverEl?.textContent).toContain('Status: pending');
    expect(solverEl?.textContent).not.toMatch(/none|v0/);
  });

  it('renders predicted-only disclosure without held-out residuals and dispatches sky.show (predicted)', async () => {
    let latestSession = createSyntheticUnresolvedSession();
    const prdBadge = getEpistemicBadgeDescriptor('predicted');

    await act(async () => {
      root.render(
        React.createElement(ObservationSolveAndCompareHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });

    const badgeEl = container.querySelector<HTMLElement>(
      '[data-testid="solve-receipt-badge"]'
    );
    expect(badgeEl?.textContent).toContain(prdBadge.shortCode);
    expect(badgeEl?.textContent).toContain(prdBadge.label);

    const discEl = container.querySelector<HTMLElement>(
      '[data-testid="solve-receipt-disclosure"]'
    );
    expect(discEl?.textContent).toContain(prdBadge.disclosure);

    const regBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="solve-register-sky-btn"]'
    )!;
    expect(regBtn.disabled).toBe(false);
    expect(regBtn.textContent).toContain('Show Predicted Sky');

    await act(async () => {
      regBtn.click();
    });
    expect(latestSession.machines.navigation).toBe('sky_registered');
  });

  it('disables sky registration and renders warnings verbatim when solution status is unresolved', async () => {
    const base = createSyntheticUnresolvedSession();
    const unresolved: ObservationSession = {
      ...base,
      machines: {
        ...base.machines,
        calibration: 'unresolved',
      },
      observationSolution: {
        ...base.observationSolution!,
        status: 'unresolved',
      },
    };

    await act(async () => {
      root.render(
        React.createElement(ObservationSolveAndCompareHud, {
          session: unresolved,
          setSession: () => {},
        })
      );
    });

    const regBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="solve-register-sky-btn"]'
    )!;
    expect(regBtn.disabled).toBe(true);

    const warningsEl = container.querySelector<HTMLElement>(
      '[data-testid="solve-receipt-warnings"]'
    );
    expect(warningsEl).not.toBeNull();
    for (const w of unresolved.observationSolution!.warnings) {
      expect(warningsEl?.textContent).toContain(w);
    }
  });

  it('dispatches media.restore and media.compare and updates compareWipePosition from the A/B wipe slider', async () => {
    const base = createSyntheticCalibratedSession();
    let latestSession: ObservationSession = {
      ...base,
      activeVariantId: 'variant_original',
      machines: {
        ...base.machines,
        restoration: 'idle',
      },
    };

    const rerender = async () => {
      await act(async () => {
        root.render(
          React.createElement(ObservationSolveAndCompareHud, {
            session: latestSession,
            setSession: (updater) => {
              latestSession =
                typeof updater === 'function'
                  ? updater(latestSession)
                  : updater;
            },
          })
        );
      });
    };

    await rerender();

    // Source asset SHA-256 prefix & immutability badge shown
    const assetEl = container.querySelector<HTMLElement>(
      '[data-testid="solve-source-asset-pill"]'
    );
    expect(assetEl?.textContent).toContain(
      latestSession.sourceAsset!.sha256.slice(0, 12)
    );
    expect(assetEl?.textContent).toContain('Immutable SourceAsset');

    // Click Restore Star-Protected
    const restoreBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="restore-star-protected-btn"]'
    )!;
    await act(async () => {
      restoreBtn.click();
    });
    expect(latestSession.machines.restoration).toBe('ready');

    await rerender();

    // Click Compare A/B Wipe
    const compareBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="toggle-compare-wipe-btn"]'
    )!;
    await act(async () => {
      compareBtn.click();
    });
    expect(latestSession.machines.navigation).toBe('comparison');
    expect(latestSession.compareWipePosition).toBe(0.5);

    await rerender();

    const slider = container.querySelector<HTMLInputElement>(
      '[data-testid="compare-wipe-slider"]'
    )!;
    expect(slider).not.toBeNull();
    expect(slider.getAttribute('aria-label')).toBe(
      'Original vs restored sky A/B wipe position'
    );
    expect(slider.value).toBe('50');

    const nativeValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value'
    )?.set;
    await act(async () => {
      nativeValueSetter?.call(slider, '72');
      slider.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(latestSession.compareWipePosition).toBeCloseTo(0.72, 2);
  });

  it('wires ObservationSolveAndCompareHud in SpatialObservatoryViewport and defines scoped CSS rules in albedian.css', async () => {
    let latestSession = createSyntheticCalibratedSession();
    const noop = () => {};

    await act(async () => {
      root.render(
        React.createElement(SpatialObservatoryViewport, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
          selectedFinding: null,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onSelectFinding: noop,
          onPushToTalk: noop,
          onTriggerUpload: noop,
        })
      );
    });

    // Hidden while entryStageActive is true
    expect(
      container.querySelector('[data-testid="observation-solve-compare-hud"]')
    ).toBeNull();

    const launchBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="enter-observatory-btn"]'
    )!;
    await act(async () => {
      launchBtn.click();
    });

    expect(
      container.querySelector('[data-testid="observation-solve-compare-hud"]')
    ).not.toBeNull();

    const regBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="solve-register-sky-btn"]'
    )!;
    await act(async () => {
      regBtn.click();
    });
    expect(latestSession.machines.navigation).toBe('sky_registered');

    const cssSrc = readFileSync(
      resolve(__dirname, '../../styles/albedian.css'),
      'utf8'
    );
    expect(cssSrc).toMatch(
      /\.albedian-solve-compare-hud\s*\{[^}]*position:\s*absolute;/
    );
    expect(cssSrc).toMatch(
      /\.albedian-solve-compare-hud\s*\{[^}]*left:\s*32px;/
    );
    expect(cssSrc).toMatch(
      /@media\s*\(max-width:\s*640px\)\s*\{\s*\.albedian-solve-compare-hud\s*\{[^}]*left:\s*12px;[^}]*right:\s*12px;/
    );
  });
});
