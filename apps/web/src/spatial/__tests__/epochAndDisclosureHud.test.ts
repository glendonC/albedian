// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
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

import { EpochAndDisclosureHud } from '../EpochAndDisclosureHud.js';
import { SpatialObservatoryViewport } from '../../components/SpatialObservatoryViewport.js';
import { choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';

describe('EpochAndDisclosureHud (moment 05/06: alternative 3D viewpoint, marker filters, & epoch honesty HUD)', () => {
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

  it('renders nothing in source navigation state when simulationEpochISO matches observationEpochISO', async () => {
    const session = createSyntheticCalibratedSession();

    await act(async () => {
      root.render(
        React.createElement(EpochAndDisclosureHud, {
          session,
          setSession: () => {},
        })
      );
    });

    expect(
      container.querySelector('[data-testid="epoch-and-disclosure-hud"]')
    ).toBeNull();
  });

  it('renders simulated badge and disclosure in 3D space and dispatches space.viewpoint, finding.describe, and marker filter toggles', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    let latestSession = dispatchAlbedianAction(base, {
      type: 'space.enter',
      requestId: 'req-enter-space-epoch',
      expectedViewRevision: base.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;

    const rerender = async () => {
      await act(async () => {
        root.render(
          React.createElement(EpochAndDisclosureHud, {
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

    const hud = container.querySelector<HTMLElement>(
      '[data-testid="epoch-and-disclosure-hud"]'
    );
    expect(hud).not.toBeNull();
    expect(hud?.getAttribute('data-hud-interactive')).toBe('true');
    expect(hud?.getAttribute('aria-label')).toBe(
      'Simulated viewpoint, overlay, and epoch disclosure controls'
    );

    const simBadge = getEpistemicBadgeDescriptor('simulated');
    const badgeEl = container.querySelector<HTMLElement>(
      '[data-testid="epoch-simulated-badge"]'
    );
    expect(badgeEl?.textContent).toContain(simBadge.shortCode);
    expect(badgeEl?.textContent).toContain(simBadge.label);

    const discEl = container.querySelector<HTMLElement>(
      '[data-testid="epoch-simulated-disclosure"]'
    );
    expect(discEl?.textContent).toContain(simBadge.disclosure);

    // Click Orbit Viewpoint (+1 pc): space.viewpoint places the camera at the star plus the offset,
    // wherever space.enter left it (one light year short of the star)
    const beforeOrigin = [...resolveFindingParsecPosition(finding)!];
    const orbitBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="shift-viewpoint-orbit-btn"]'
    )!;
    expect(orbitBtn.disabled).toBe(false);
    await act(async () => {
      orbitBtn.click();
    });
    expect(latestSession.spatialView.observerOriginParsec[0]).toBeCloseTo(
      beforeOrigin[0]! + 1,
      4
    );

    await rerender();

    // Click Describe Sightline
    const sightlineBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="describe-sightline-btn"]'
    )!;
    expect(sightlineBtn.disabled).toBe(false);
    await act(async () => {
      sightlineBtn.click();
    });
    expect(latestSession.lastStatusBanner).toContain(finding.title);
    expect(latestSession.lastStatusBanner).toContain('RA ');

    await rerender();

    // Toggle Observed Markers (read by CelestialMarkerOverlay)
    const obsBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="toggle-observed-markers-btn"]'
    )!;
    const prevObs = latestSession.spatialView.activeOverlays.observedMarkers;
    await act(async () => {
      obsBtn.click();
    });
    expect(latestSession.spatialView.activeOverlays.observedMarkers).toBe(
      !prevObs
    );

    await rerender();

    // Toggle Predicted Ephemerides (read by CelestialMarkerOverlay)
    const prdBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="toggle-predicted-ephemerides-btn"]'
    )!;
    const prevPrd =
      latestSession.spatialView.activeOverlays.predictedEphemerides;
    await act(async () => {
      prdBtn.click();
    });
    expect(latestSession.spatialView.activeOverlays.predictedEphemerides).toBe(
      !prevPrd
    );
  });

  it('displays non-observation epoch delta, honest propagation limitation note, and resets to observationEpochISO on click', async () => {
    const base = createSyntheticCalibratedSession();
    let latestSession: ObservationSession = {
      ...base,
      machines: {
        ...base.machines,
        navigation: 'space',
      },
      spatialView: {
        ...base.spatialView,
        mode: 'modeled_epoch',
        simulationEpochISO: '1030-01-01T02:00:00Z',
      },
    };

    await act(async () => {
      root.render(
        React.createElement(EpochAndDisclosureHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });

    const deltaEl = container.querySelector<HTMLElement>(
      '[data-testid="epoch-delta-readout"]'
    );
    expect(deltaEl).not.toBeNull();
    expect(deltaEl?.textContent).toContain('-1000 yr');
    expect(deltaEl?.textContent).toContain(
      'the view does not move the stars to a chosen year'
    );

    const resetBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="reset-observation-epoch-btn"]'
    );
    expect(resetBtn).not.toBeNull();

    await act(async () => {
      resetBtn!.click();
    });

    expect(latestSession.spatialView.simulationEpochISO).toBe(
      latestSession.spatialView.observationEpochISO
    );
    expect(latestSession.spatialView.mode).toBe('catalog_3d');

    // Orbit viewpoint and Describe Sightline buttons are disabled when selectedFindingId is null
    const orbitBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="shift-viewpoint-orbit-btn"]'
    )!;
    expect(orbitBtn.disabled).toBe(true);
    const sightlineBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="describe-sightline-btn"]'
    )!;
    expect(sightlineBtn.disabled).toBe(true);
  });

  it('wires EpochAndDisclosureHud in SpatialObservatoryViewport and defines scoped CSS rules in albedian.css', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    let latestSession = dispatchAlbedianAction(base, {
      type: 'space.enter',
      requestId: 'req-vp-epoch',
      expectedViewRevision: base.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;

    const noop = () => {};

    await act(async () => {
      root.render(
        React.createElement(SpatialObservatoryViewport, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
          selectedFinding: finding,
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

    expect(
      container.querySelector('[data-testid="epoch-and-disclosure-hud"]')
    ).toBeNull();

    choosePhotoOnHome(container);

    expect(
      container.querySelector('[data-testid="epoch-and-disclosure-hud"]')
    ).not.toBeNull();

    const sightlineBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="describe-sightline-btn"]'
    )!;
    await act(async () => {
      sightlineBtn.click();
    });
    expect(latestSession.lastStatusBanner).toContain('RA ');

    const cssSrc = readFileSync(
      resolve(__dirname, '../../styles/albedian.css'),
      'utf8'
    );
    expect(cssSrc).toMatch(
      /\.albedian-epoch-disclosure-hud\s*\{[^}]*position:\s*absolute;/
    );
    expect(cssSrc).toMatch(
      /@media\s*\(max-width:\s*640px\)\s*\{\s*\.albedian-epoch-disclosure-hud\s*\{[^}]*left:\s*12px;[^}]*right:\s*12px;/
    );
  });
});
