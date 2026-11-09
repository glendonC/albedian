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
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { FLIGHT_STANDOFF_PC, flightEndOrigin } from '@albedian/observation';

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
    // Leaving for the sky saved the moment you left (experience-9j): its label, time, gain and playback
    expect(pill?.textContent).toContain('Departure from Source to Registered Sky');
    expect(pill?.textContent).not.toContain('Original Night Sky Clip');
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
    const finding = base.observationSolution!.findings.find(
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

  it('returns to the moment you left, with the star you had selected, not the session start (experience-9j)', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    // Pick the star on the photo, then fly to it: the flight saves the moment with the selection
    const selected = dispatchAlbedianAction(base, {
      type: 'finding.select',
      requestId: 'req-select',
      expectedViewRevision: base.viewRevision,
      findingId: finding.id,
    }).session;
    expect(selected.selectedFindingId).toBe(finding.id);
    expect(base.navigationHistory.anchorMomentCheckpoint.selectedFindingId).toBeNull();
    const inSpace = dispatchAlbedianAction(selected, {
      type: 'space.enter',
      requestId: 'req-enter-space',
      expectedViewRevision: selected.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;

    for (const press of ['button', 'escape'] as const) {
      let latestSession = inSpace;
      await act(async () => {
        root.render(
          React.createElement(FlightAndRestoreHud, {
            session: latestSession,
            setSession: (updater) => {
              latestSession = typeof updater === 'function' ? updater(latestSession) : updater;
            },
          })
        );
      });
      // The pill names the moment it will return to
      const pill = container.querySelector<HTMLElement>('[data-testid="checkpoint-summary-pill"]')!;
      expect(pill.textContent).toContain(`Checkpoint before entering 3D space toward ${finding.title}`);
      await act(async () => {
        if (press === 'button') container.querySelector<HTMLButtonElement>('[data-testid="back-to-moment-btn"]')!.click();
        else window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });
      expect(latestSession.machines.navigation, press).toBe('source');
      expect(latestSession.spatialView.mode, press).toBe('source_2d');
      expect(latestSession.selectedFindingId, press).toBe(finding.id);
    }
  });

  it('returns to the photo, not the sky, after leaving in two steps (experience-9j)', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    const onSky = dispatchAlbedianAction(base, {
      type: 'sky.show',
      requestId: 'req-sky-show',
      expectedViewRevision: base.viewRevision,
      mode: 'measured',
    }).session;
    // From the sky into 3D saves a second checkpoint, taken in the sky, not on the photo
    const inSpace = dispatchAlbedianAction(onSky, {
      type: 'space.enter',
      requestId: 'req-enter-space',
      expectedViewRevision: onSky.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;
    const saved = inSpace.navigationHistory.checkpoints;
    expect(saved[saved.length - 1]!.navigationState).toBe('sky_registered');
    let latestSession = inSpace;
    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession = typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });
    const pill = container.querySelector<HTMLElement>('[data-testid="checkpoint-summary-pill"]')!;
    expect(pill.textContent).toContain('Departure from Source to Registered Sky');
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="back-to-moment-btn"]')!.click();
    });
    expect(latestSession.machines.navigation).toBe('source');
    expect(latestSession.spatialView.mode).toBe('source_2d');
  });

  it('falls back to the session start when no moment was saved on the source (experience-9j)', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    const onSky = dispatchAlbedianAction(base, {
      type: 'sky.show',
      requestId: 'req-sky-show',
      expectedViewRevision: base.viewRevision,
      mode: 'measured',
    }).session;
    const entered = dispatchAlbedianAction(onSky, {
      type: 'space.enter',
      requestId: 'req-enter-space',
      expectedViewRevision: onSky.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;
    // A history that kept only the checkpoint taken in the sky: nothing saved on the photo but the anchor
    const skyOnly = entered.navigationHistory.checkpoints.slice(-1);
    expect(skyOnly[0]!.navigationState).toBe('sky_registered');
    const inSpace: ObservationSession = {
      ...entered,
      navigationHistory: { ...entered.navigationHistory, checkpoints: skyOnly, cursorIndex: 0 },
    };
    let latestSession: ObservationSession = inSpace;
    await act(async () => {
      root.render(
        React.createElement(FlightAndRestoreHud, {
          session: latestSession,
          setSession: (updater) => {
            latestSession = typeof updater === 'function' ? updater(latestSession) : updater;
          },
        })
      );
    });
    const pill = container.querySelector<HTMLElement>('[data-testid="checkpoint-summary-pill"]')!;
    expect(pill.textContent).toContain(inSpace.navigationHistory.anchorMomentCheckpoint.label);
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="back-to-moment-btn"]')!.click();
    });
    expect(latestSession.machines.navigation).toBe('source');
    expect(latestSession.spatialView.mode).toBe('source_2d');
  });

  it('renders live parsec and light-year distance, Powers of Ten scale rung, and simulated disclosure in 3D space', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
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
    // Three significant figures: the distance rests on a catalog estimate
    expect(distEl?.textContent).toContain(`${Number(expectedLy.toPrecision(3))} ly`);
    expect(distEl?.textContent).toContain(`${Number(expectedPc.toPrecision(3))} pc`);

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

  it('says how far the camera is from the target, and that the flight moves the markers, not the stars', async () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    const inSpace = dispatchAlbedianAction(base, {
      type: 'space.enter',
      requestId: 'req-enter-space-target',
      expectedViewRevision: base.viewRevision,
      findingId: finding.id,
      distancePolicy: 'measured_or_estimated',
    }).session;
    // Where a Go There flight now ends: one light year short of the star (flightEndOrigin, packages/observation/src/flight.ts)
    const target = resolveFindingParsecPosition(finding)!;
    const arrived: ObservationSession = {
      ...inSpace,
      spatialView: { ...inSpace.spatialView, observerOriginParsec: flightEndOrigin([0, 0, 0], target) },
    };

    await act(async () => {
      root.render(React.createElement(FlightAndRestoreHud, { session: arrived, setSession: () => {} }));
    });

    const toTarget = container.querySelector<HTMLElement>('[data-testid="flight-target-distance-readout"]');
    expect(toTarget?.textContent).toMatch(new RegExp(`^1\\.00 ly from ${finding.title}`));
    // Three significant figures from Earth: the star's own distance is a catalog estimate
    const fromEarth = container.querySelector<HTMLElement>('[data-testid="flight-distance-readout"]');
    const earthLy = (Math.hypot(...target) - FLIGHT_STANDOFF_PC) * PARSEC_TO_LIGHT_YEAR;
    expect(fromEarth?.textContent).toContain(`${Number(earthLy.toPrecision(3))} ly`);
    expect(fromEarth?.textContent).not.toMatch(/\.\d\d ly/);
    const disclosure = container.querySelector<HTMLElement>('[data-testid="flight-simulated-disclosure"]');
    expect(disclosure?.textContent).toMatch(/the flight moves the markers, not the stars/i);
  });

  it("gives a prior-placed star's catalog distance with its range, at three significant figures", async () => {
    const base = createSyntheticCalibratedSession();
    const betelgeuse = base.observationSolution!.findings.find((f) => f.title === 'Betelgeuse')!;
    const inSpace = dispatchAlbedianAction(base, {
      type: 'space.enter',
      requestId: 'req-enter-space-betelgeuse',
      expectedViewRevision: base.viewRevision,
      findingId: betelgeuse.id,
      distancePolicy: 'measured_or_estimated',
    }).session;
    const arrived: ObservationSession = {
      ...inSpace,
      spatialView: {
        ...inSpace.spatialView,
        observerOriginParsec: flightEndOrigin([0, 0, 0], resolveFindingParsecPosition(betelgeuse)!),
      },
    };
    await act(async () => {
      root.render(React.createElement(FlightAndRestoreHud, { session: arrived, setSession: () => {} }));
    });
    const toTarget = container.querySelector<HTMLElement>('[data-testid="flight-target-distance-readout"]');
    // 168.067 pc (156.25 to 181.818) in the synthetic Hipparcos row
    expect(toTarget?.textContent).toContain('548 ly (510 to 593 ly) from Earth');
  });

  it('gives the catalog clause only for a catalog distance, not an ephemeris one or none', async () => {
    const base = createSyntheticCalibratedSession();
    const star = base.observationSolution!.findings.find((f) => f.title === 'Betelgeuse')!;
    const at = resolveFindingParsecPosition(star)!;
    const variants = {
      // A solar system body's distance comes from the ephemeris, in a few au
      ephemeris: { ...star, title: 'Mars', inferredDistancePc: { lower: 6.0e-6, median: 6.1e-6, upper: 6.2e-6, method: 'ephemeris_au' as const } },
      // A finding placed in 3D with no distance prior at all
      noPrior: { ...star, title: 'Unpriored star', cartesianParsecICRS: at, inferredDistancePc: undefined },
    };
    for (const [name, finding] of Object.entries(variants)) {
      const session: ObservationSession = {
        ...base,
        selectedFindingId: finding.id,
        machines: { ...base.machines, navigation: 'space' },
        observationSolution: { ...base.observationSolution!, findings: [finding as typeof star] },
        spatialView: { ...base.spatialView, mode: 'catalog_3d', transitionPhase: 'at_space', observerOriginParsec: [0, 0, 0] },
      };
      await act(async () => {
        root.render(React.createElement(FlightAndRestoreHud, { session, setSession: () => {} }));
      });
      const toTarget = container.querySelector<HTMLElement>('[data-testid="flight-target-distance-readout"]');
      expect(toTarget?.textContent, name).toMatch(new RegExp(`ly from ${finding.title}$`));
      expect(toTarget?.textContent, name).not.toMatch(/catalog/);
      expect(toTarget?.textContent, name).not.toMatch(/e[-+]\d/);
    }
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
    // The animation's progress is not a job's, so the phase is named without a percentage
    expect(phaseEl?.textContent).not.toMatch(/%/);
    expect(phaseEl?.textContent).not.toContain('42');

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
