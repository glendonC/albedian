// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import {
  getEpistemicBadgeDescriptor,
  PARSEC_TO_LIGHT_YEAR,
} from '@albedian/evidence';

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

import { FlightAndRestoreHud } from '../FlightAndRestoreHud.js';
import { SpatialObservatoryViewport } from '../SpatialObservatoryViewport.js';

describe('FlightAndRestoreHud (moments 06 & 08: smooth flight readout & Back to Moment)', () => {
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

  it('renders nothing while session.machines.navigation is source', async () => {
    const session = createSyntheticCalibratedSession();
    expect(session.machines.navigation).toBe('source');

    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session,
          setSession: () => {},
        })
      );
    });

    expect(
      container.querySelector('[data-testid="flight-and-restore-hud"]')
    ).toBeNull();
  });

  it('renders the Back to moment button and saved CameraCheckpoint summary pill in sky_registered state', async () => {
    let session = createSyntheticCalibratedSession();
    session = {
      ...session,
      currentSourceTimeSec: 3.45,
      audioGain: 0.78,
      isPlaying: true,
      navigationHistory: {
        ...session.navigationHistory,
        anchorMomentCheckpoint: {
          ...session.navigationHistory.anchorMomentCheckpoint,
          label: 'Original Night Sky Clip',
          sourceTimeSec: 3.45,
          audioGain: 0.78,
          playbackWasPlaying: true,
        },
      },
    };
    session = dispatchAlbedianAction(session, {
      type: 'sky.show',
      requestId: 'req-sky-show',
      expectedViewRevision: session.viewRevision,
      mode: 'measured',
    }).session;

    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session,
          setSession: () => {},
        })
      );
    });

    const hud = container.querySelector<HTMLElement>(
      '[data-testid="flight-and-restore-hud"]'
    );
    expect(hud).not.toBeNull();
    expect(hud?.getAttribute('data-hud-interactive')).toBe('true');
    expect(hud?.getAttribute('role')).toBe('region');
    expect(hud?.getAttribute('aria-label')).toBe(
      'Flight and checkpoint controls'
    );

    const backBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="back-to-moment-btn"]'
    );
    expect(backBtn).not.toBeNull();
    expect(backBtn?.textContent).toContain('Back to moment (Esc)');

    const pill = container.querySelector<HTMLElement>(
      '[data-testid="checkpoint-summary-pill"]'
    );
    expect(pill).not.toBeNull();
    expect(pill?.textContent).toContain('Original Night Sky Clip');
    expect(pill?.textContent).toContain('3.45s');
    expect(pill?.textContent).toContain('78%');
    expect(pill?.textContent).toContain('playing');

    // Not in 3D space or active flight transition, so flight scale readout is hidden
    expect(
      container.querySelector('[data-testid="flight-scale-readout"]')
    ).toBeNull();
  });

  it('dispatches source.return on Back to moment click and on Escape key outside text inputs', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    const inSpace = dispatchAlbedianAction(base, {
      type: 'space.enter',
      requestId: 'req-enter-space',
      expectedViewRevision: base.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;
    expect(inSpace.machines.navigation).toBe('space');

    let latestSession = inSpace;
    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });

    // Pressing Escape inside an input must NOT trigger source.return
    const dummyInput = document.createElement('input');
    document.body.appendChild(dummyInput);
    dummyInput.focus();
    await act(async () => {
      dummyInput.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })
      );
    });
    expect(latestSession.machines.navigation).toBe('space');
    dummyInput.remove();

    // Pressing Escape on window restores the anchor checkpoint
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })
      );
    });
    expect(latestSession.machines.navigation).toBe('source');
    expect(latestSession.currentSourceTimeSec).toBe(
      base.navigationHistory.anchorMomentCheckpoint.sourceTimeSec
    );
    expect(latestSession.audioGain).toBe(
      base.navigationHistory.anchorMomentCheckpoint.audioGain
    );

    // Reset to inSpace and test clicking the Back to moment button
    latestSession = inSpace;
    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });

    const backBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="back-to-moment-btn"]'
    )!;
    await act(async () => {
      backBtn.click();
    });
    expect(latestSession.machines.navigation).toBe('source');
    expect(latestSession.spatialView.mode).toBe('source_2d');
  });

  it('renders live parsec and light-year distance, Powers of Ten scale rung, and simulated disclosure in 3D space', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    const inSpace = dispatchAlbedianAction(base, {
      type: 'space.enter',
      requestId: 'req-enter-space-readout',
      expectedViewRevision: base.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;

    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session: inSpace,
          setSession: () => {},
        })
      );
    });

    const readout = container.querySelector<HTMLElement>(
      '[data-testid="flight-scale-readout"]'
    );
    expect(readout).not.toBeNull();

    const origin = inSpace.spatialView.observerOriginParsec;
    const expectedPc = Math.hypot(origin[0], origin[1], origin[2]);
    const expectedLy = expectedPc * PARSEC_TO_LIGHT_YEAR;

    const distEl = container.querySelector<HTMLElement>(
      '[data-testid="flight-distance-readout"]'
    );
    expect(distEl?.textContent).toContain(`${expectedLy.toFixed(2)} ly`);
    expect(distEl?.textContent).toContain(`${expectedPc.toFixed(2)} pc`);

    const rungGroup = container.querySelector<HTMLElement>(
      '[data-testid="flight-scale-rung"]'
    );
    expect(rungGroup?.getAttribute('aria-label')).toBe(
      'Powers of Ten parsec scale'
    );
    const rungs = container.querySelectorAll<HTMLElement>(
      '[data-testid="flight-scale-rung"] [data-rung]'
    );
    expect(rungs.length).toBe(6);
    const activeRungs = Array.from(rungs).filter(
      (el) => el.getAttribute('aria-current') === 'step'
    );
    expect(activeRungs.length).toBe(1);
    expect(activeRungs[0]?.getAttribute('data-rung')).toBe(
      String(
        Math.max(
          -3,
          Math.min(2, Math.round(Math.log10(Math.max(expectedPc, 0.001))))
        )
      )
    );

    const simBadge = getEpistemicBadgeDescriptor('simulated');
    const simDisclosure = container.querySelector<HTMLElement>(
      '[data-testid="flight-simulated-disclosure"]'
    );
    expect(simDisclosure).not.toBeNull();
    expect(simDisclosure?.textContent).toContain(simBadge.shortCode);
    expect(simDisclosure?.textContent).toContain(simBadge.label);
    expect(simDisclosure?.textContent).toContain(simBadge.disclosure);
    expect(simDisclosure?.textContent).toContain('Gaia DR3');
    expect(simDisclosure?.textContent).toContain('Hipparcos');
  });

  it('renders Stop flight button during active transitions and dispatches navigation.cancel on click', async () => {
    const base = createSyntheticCalibratedSession();
    const inFlight: ObservationSession = {
      ...base,
      machines: {
        ...base.machines,
        navigation: 'transitioning',
      },
      spatialView: {
        ...base.spatialView,
        mode: 'catalog_3d',
        transitionPhase: 'phase_b_sky_to_space',
        transitionProgress: 0.42,
        activeTransitionId: 'tx-flight-42',
        observerOriginParsec: [12, -16, 15],
      },
    };

    let latestSession = inFlight;
    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession =
              typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });

    const phaseEl = container.querySelector<HTMLElement>(
      '[data-testid="flight-transition-phase"]'
    );
    expect(phaseEl?.textContent).toContain(
      'Phase B: Calibrated Sky to 3D Catalog Space'
    );
    expect(phaseEl?.textContent).toContain('42%');

    const stopBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="stop-flight-btn"]'
    );
    expect(stopBtn).not.toBeNull();

    await act(async () => {
      stopBtn!.click();
    });

    expect(latestSession.machines.navigation).toBe('sky_registered');
    expect(latestSession.spatialView.activeTransitionId).toBeUndefined();
  });

  it('wires FlightAndRestoreHud in SpatialObservatoryViewport and defines restore HUD styles in albedian.css', async () => {
    const base = createSyntheticCalibratedSession();
    const inSky = dispatchAlbedianAction(base, {
      type: 'sky.show',
      requestId: 'req-vp-sky',
      expectedViewRevision: base.viewRevision,
      mode: 'measured',
    }).session;

    let latestSession = inSky;
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
      container.querySelector('[data-testid="flight-and-restore-hud"]')
    ).toBeNull();

    // Launch Albedian
    const launchBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="enter-observatory-btn"]'
    )!;
    await act(async () => {
      launchBtn.click();
    });

    expect(
      container.querySelector('[data-testid="flight-and-restore-hud"]')
    ).not.toBeNull();
    const backBtn = container.querySelector<HTMLButtonElement>(
      '[data-testid="back-to-moment-btn"]'
    )!;
    await act(async () => {
      backBtn.click();
    });
    expect(latestSession.machines.navigation).toBe('source');

    const cssSrc = readFileSync(
      resolve(__dirname, '../../styles/albedian.css'),
      'utf8'
    );
    expect(cssSrc).toMatch(
      /\.alb-restore-hud-shell\s*\{[^}]*position:\s*absolute;/
    );
    expect(cssSrc).toMatch(/\.alb-restore-hud-shell\s*\{[^}]*left:\s*32px;/);
    expect(cssSrc).toMatch(
      /\.alb-flight-scale-card\s*\{[^}]*display:\s*flex;/
    );
    expect(cssSrc).toMatch(
      /@media\s*\(max-width:\s*640px\)\s*\{\s*\.alb-restore-hud-shell\s*\{[^}]*left:\s*12px;[^}]*right:\s*12px;/
    );
  });
});
