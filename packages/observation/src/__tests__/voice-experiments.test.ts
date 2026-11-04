import { describe, expect, it } from 'vitest';
import { buildActionContext, executeVoiceOrTextCommand, parseUtteranceToAction } from '../index';
import { createSyntheticCalibratedSession, createSyntheticUnresolvedSession } from '../testing/syntheticSessions';

describe('EXP-09: Voice & Text Utterance Context Resolution, Disambiguation & Barge-In (40+ Cases)', () => {
  const baseSession = createSyntheticCalibratedSession();
  const withBetelgeuseSelected = {
    ...baseSession,
    selectedFindingId: 'finding_betelgeuse',
    focusedFindingId: 'finding_betelgeuse',
  };
  const withRigelSelected = {
    ...baseSession,
    selectedFindingId: 'finding_rigel',
    focusedFindingId: 'finding_rigel',
  };

  const representativeUtteranceCases: Array<{
    name: string;
    utterance: string;
    session: typeof baseSession;
    recentPointer?: ReturnType<typeof buildActionContext>['recentPointer'];
    expectedActionType: string;
    verify?: (action: NonNullable<ReturnType<typeof parseUtteranceToAction>['action']>) => void;
  }> = [
    // Media restoration commands (6 cases)
    {
      name: '01. Make the sky clearer -> media.restore (star_protected)',
      utterance: 'Make the sky clearer',
      session: baseSession,
      expectedActionType: 'media.restore',
      verify: (a) => {
        if (a.type === 'media.restore') {
          expect(a.profile).toBe('star_protected');
          expect(a.constraints.preservePeople).toBe(true);
          expect(a.constraints.allowSyntheticSky).toBe(false);
        }
      },
    },
    {
      name: "02. Don't change the people -> media.restore",
      utterance: "Don't change the people",
      session: baseSession,
      expectedActionType: 'media.restore',
      verify: (a) => {
        if (a.type === 'media.restore') {
          expect(a.constraints.preservePeople).toBe(true);
        }
      },
    },
    {
      name: '03. Temporal stack -> media.restore (temporal)',
      utterance: 'Temporal stack',
      session: baseSession,
      expectedActionType: 'media.restore',
      verify: (a) => {
        if (a.type === 'media.restore') {
          expect(a.profile).toBe('temporal');
        }
      },
    },
    {
      name: '04. Clean up noise in the sky -> media.restore',
      utterance: 'Clean up noise in the sky',
      session: baseSession,
      expectedActionType: 'media.restore',
    },
    {
      name: '05. Bring out faint stars -> media.restore (temporal)',
      utterance: 'Bring out faint stars',
      session: baseSession,
      expectedActionType: 'media.restore',
      verify: (a) => {
        if (a.type === 'media.restore') {
          expect(a.profile).toBe('temporal');
        }
      },
    },
    {
      name: '06. Denoise recording -> media.restore',
      utterance: 'Denoise recording',
      session: baseSession,
      expectedActionType: 'media.restore',
    },

    // Media comparison & seek commands (5 cases)
    {
      name: '07. Compare -> media.compare',
      utterance: 'Compare',
      session: baseSession,
      expectedActionType: 'media.compare',
    },
    {
      name: '08. Show original -> media.compare',
      utterance: 'Show original',
      session: baseSession,
      expectedActionType: 'media.compare',
    },
    {
      name: '09. Before and after -> media.compare',
      utterance: 'Before and after',
      session: baseSession,
      expectedActionType: 'media.compare',
    },
    {
      name: '10. Original recording -> media.compare',
      utterance: 'Original recording',
      session: baseSession,
      expectedActionType: 'media.compare',
    },
    {
      name: '11. Seek to 7.5 seconds -> media.seek',
      utterance: 'Seek to 7.5 seconds',
      session: baseSession,
      expectedActionType: 'media.seek',
      verify: (a) => {
        if (a.type === 'media.seek') {
          expect(a.sourceTimeSec).toBe(7.5);
        }
      },
    },

    // Sky registration commands (4 cases)
    {
      name: '12. What were we looking at? -> sky.show (measured)',
      utterance: 'What were we looking at?',
      session: baseSession,
      expectedActionType: 'sky.show',
      verify: (a) => {
        if (a.type === 'sky.show') {
          expect(a.mode).toBe('measured');
        }
      },
    },
    {
      name: '13. Show me what was overhead -> sky.show (measured)',
      utterance: 'Show me what was overhead',
      session: baseSession,
      expectedActionType: 'sky.show',
    },
    {
      name: '14. Identify the stars -> sky.show',
      utterance: 'Identify the stars',
      session: baseSession,
      expectedActionType: 'sky.show',
    },
    {
      name: '15. What were we looking at? on predicted_only session -> sky.show (predicted)',
      utterance: 'What were we looking at?',
      session: createSyntheticUnresolvedSession(),
      expectedActionType: 'sky.show',
      verify: (a) => {
        if (a.type === 'sky.show') {
          expect(a.mode).toBe('predicted');
        }
      },
    },

    // Named & deictic selection commands (9 cases)
    {
      name: '16. Select Betelgeuse -> finding.select (finding_betelgeuse)',
      utterance: 'Select Betelgeuse',
      session: baseSession,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_betelgeuse');
        }
      },
    },
    {
      name: '17. Select Rigel -> finding.select (finding_rigel)',
      utterance: 'Select Rigel',
      session: baseSession,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_rigel');
        }
      },
    },
    {
      name: '18. Look at Aldebaran -> finding.select (finding_aldebaran)',
      utterance: 'Look at Aldebaran',
      session: baseSession,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_aldebaran');
        }
      },
    },
    {
      name: '19. Focus on Sirius -> finding.select (finding_sirius)',
      utterance: 'Focus on Sirius',
      session: baseSession,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_sirius');
        }
      },
    },
    {
      name: '20. Inspect Alpha Orionis (Bayer designation) -> finding.select (finding_betelgeuse)',
      utterance: 'Inspect Alpha Orionis',
      session: baseSession,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_betelgeuse');
        }
      },
    },
    {
      name: '21. Inspect HIP 24436 (catalog ID) -> finding.select (finding_rigel)',
      utterance: 'Inspect HIP 24436',
      session: baseSession,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_rigel');
        }
      },
    },
    {
      name: "22. What's that one? with active selectedFindingId -> finding.select",
      utterance: "What's that one?",
      session: withBetelgeuseSelected,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_betelgeuse');
        }
      },
    },
    {
      name: "23. What's that one? with fresh recentPointer -> finding.select (nearestFindingId)",
      utterance: "What's that one?",
      session: baseSession,
      recentPointer: {
        xPx: 1031.9,
        yPx: 472.9,
        atTimestampMs: 4000,
        viewRevision: baseSession.viewRevision,
        nearestFindingId: 'finding_bellatrix',
      },
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_bellatrix');
        }
      },
    },
    {
      name: '24. Named object overrides active selectedFindingId ("Select Procyon" while Betelgeuse selected)',
      utterance: 'Select Procyon',
      session: withBetelgeuseSelected,
      expectedActionType: 'finding.select',
      verify: (a) => {
        if (a.type === 'finding.select') {
          expect(a.findingId).toBe('finding_procyon');
        }
      },
    },

    // Finding description commands (4 cases)
    {
      name: '25. How far away is it? -> finding.describe ([distance, evidence])',
      utterance: 'How far away is it?',
      session: withBetelgeuseSelected,
      expectedActionType: 'finding.describe',
      verify: (a) => {
        if (a.type === 'finding.describe') {
          expect(a.findingId).toBe('finding_betelgeuse');
          expect(a.fields).toEqual(['distance', 'evidence']);
        }
      },
    },
    {
      name: '26. How far away is Rigel? -> finding.describe on named target',
      utterance: 'How far away is Rigel?',
      session: withBetelgeuseSelected,
      expectedActionType: 'finding.describe',
      verify: (a) => {
        if (a.type === 'finding.describe') {
          expect(a.findingId).toBe('finding_rigel');
          expect(a.fields).toEqual(['distance', 'evidence']);
        }
      },
    },
    {
      name: "27. What's behind it? -> finding.describe ([behind_sightline])",
      utterance: "What's behind it?",
      session: withBetelgeuseSelected,
      expectedActionType: 'finding.describe',
      verify: (a) => {
        if (a.type === 'finding.describe') {
          expect(a.findingId).toBe('finding_betelgeuse');
          expect(a.fields).toEqual(['behind_sightline']);
        }
      },
    },
    {
      name: '28. What lies beyond Alnilam? -> finding.describe ([behind_sightline])',
      utterance: 'What lies beyond Alnilam?',
      session: baseSession,
      expectedActionType: 'finding.describe',
      verify: (a) => {
        if (a.type === 'finding.describe') {
          expect(a.findingId).toBe('finding_alnilam');
          expect(a.fields).toEqual(['behind_sightline']);
        }
      },
    },

    // 3D space entry & viewpoint commands (6 cases)
    {
      name: '29. Take me there -> space.enter (selected finding)',
      utterance: 'Take me there',
      session: withBetelgeuseSelected,
      expectedActionType: 'space.enter',
      verify: (a) => {
        if (a.type === 'space.enter') {
          expect(a.findingId).toBe('finding_betelgeuse');
        }
      },
    },
    {
      name: '30. Explore -> space.enter (selected finding)',
      utterance: 'Explore',
      session: withRigelSelected,
      expectedActionType: 'space.enter',
      verify: (a) => {
        if (a.type === 'space.enter') {
          expect(a.findingId).toBe('finding_rigel');
        }
      },
    },
    {
      name: '31. Take me to Aldebaran -> space.enter (named finding)',
      utterance: 'Take me to Aldebaran',
      session: baseSession,
      expectedActionType: 'space.enter',
      verify: (a) => {
        if (a.type === 'space.enter') {
          expect(a.findingId).toBe('finding_aldebaran');
        }
      },
    },
    {
      name: '32. Show from another viewpoint -> space.viewpoint (orbit)',
      utterance: 'Show from another viewpoint',
      session: withBetelgeuseSelected,
      expectedActionType: 'space.viewpoint',
      verify: (a) => {
        if (a.type === 'space.viewpoint') {
          expect(a.targetFindingId).toBe('finding_betelgeuse');
          expect(a.trajectory).toBe('orbit');
        }
      },
    },
    {
      name: '33. Orbit -> space.viewpoint (orbit)',
      utterance: 'Orbit',
      session: withRigelSelected,
      expectedActionType: 'space.viewpoint',
      verify: (a) => {
        if (a.type === 'space.viewpoint') {
          expect(a.targetFindingId).toBe('finding_rigel');
          expect(a.trajectory).toBe('orbit');
        }
      },
    },
    {
      name: '34. Translate viewpoint -> space.viewpoint (translate)',
      utterance: 'Translate viewpoint',
      session: withBetelgeuseSelected,
      expectedActionType: 'space.viewpoint',
      verify: (a) => {
        if (a.type === 'space.viewpoint') {
          expect(a.trajectory).toBe('translate');
        }
      },
    },

    // Overlays & Epoch simulation commands (5 cases)
    {
      name: '35. Where is Earth? -> ui.overlay (earthDirectionVector: true)',
      utterance: 'Where is Earth?',
      session: withBetelgeuseSelected,
      expectedActionType: 'ui.overlay',
      verify: (a) => {
        if (a.type === 'ui.overlay') {
          expect(a.overlay).toBe('earthDirectionVector');
          expect(a.enabled).toBe(true);
        }
      },
    },
    {
      name: '36. Show constellation lines -> ui.overlay (constellationLines: true)',
      utterance: 'Show constellation lines',
      session: baseSession,
      expectedActionType: 'ui.overlay',
      verify: (a) => {
        if (a.type === 'ui.overlay') {
          expect(a.overlay).toBe('constellationLines');
          expect(a.enabled).toBe(true);
        }
      },
    },
    {
      name: '37. Show me this sky a thousand years ago -> space.epoch (yearCE: 1026)',
      utterance: 'Show me this sky a thousand years ago',
      session: baseSession,
      expectedActionType: 'space.epoch',
      verify: (a) => {
        if (a.type === 'space.epoch') {
          expect(a.yearCE).toBe(1026);
        }
      },
    },
    {
      name: '38. 1026 CE -> space.epoch (yearCE: 1026)',
      utterance: '1026 CE',
      session: baseSession,
      expectedActionType: 'space.epoch',
      verify: (a) => {
        if (a.type === 'space.epoch') {
          expect(a.yearCE).toBe(1026);
        }
      },
    },
    {
      name: '39. 2000 BCE -> space.epoch (yearCE: -2000)',
      utterance: '2000 BCE',
      session: baseSession,
      expectedActionType: 'space.epoch',
      verify: (a) => {
        if (a.type === 'space.epoch') {
          expect(a.yearCE).toBe(-2000);
        }
      },
    },

    // Reversible navigation & cancellation commands (5 cases)
    {
      name: '40. Go back -> source.return',
      utterance: 'Go back',
      session: withBetelgeuseSelected,
      expectedActionType: 'source.return',
    },
    {
      name: '41. Take me back -> source.return',
      utterance: 'Take me back',
      session: withBetelgeuseSelected,
      expectedActionType: 'source.return',
    },
    {
      name: '42. Back to moment -> source.return',
      utterance: 'Back to moment',
      session: withBetelgeuseSelected,
      expectedActionType: 'source.return',
    },
    {
      name: '43. Go back a little -> history.back (partial, fraction: 0.35)',
      utterance: 'Go back a little',
      session: withBetelgeuseSelected,
      expectedActionType: 'history.back',
      verify: (a) => {
        if (a.type === 'history.back') {
          expect(a.mode).toBe('partial');
          expect(a.fraction).toBe(0.35);
        }
      },
    },
    {
      name: '44. Stop -> navigation.cancel',
      utterance: 'Stop',
      session: withBetelgeuseSelected,
      expectedActionType: 'navigation.cancel',
    },
    {
      name: '45. Cancel -> navigation.cancel',
      utterance: 'Cancel',
      session: withBetelgeuseSelected,
      expectedActionType: 'navigation.cancel',
    },
  ];

  it.each(representativeUtteranceCases)(
    '$name',
    ({ utterance, session, recentPointer, expectedActionType, verify }) => {
      const context = buildActionContext(session, recentPointer);
      const parsed = parseUtteranceToAction(utterance, context, session);
      expect(parsed.action).toBeDefined();
      expect(parsed.action?.type).toBe(expectedActionType);
      if (parsed.action && verify) {
        verify(parsed.action);
      }
    }
  );

  it('46. triggers disambiguation (needs_input) when pointing at overlapping belt stars and asking "What\'s that one?"', () => {
    const res = executeVoiceOrTextCommand(baseSession, "What's that one?", 'voice', {
      xPx: 940,
      yPx: 695,
      atTimestampMs: 4100,
      viewRevision: baseSession.viewRevision,
      overlappingCandidateIds: ['finding_alnilam', 'finding_alnitak'],
    });

    expect(res.outcome.status).toBe('needs_input');
    if (res.outcome.status === 'needs_input') {
      expect(res.outcome.choices).toEqual([
        { id: 'finding_alnilam', label: 'Alnilam' },
        { id: 'finding_alnitak', label: 'Alnitak' },
      ]);
      expect(res.outcome.question).toContain('Alnilam or Alnitak');
    }
  });

  it('47. rejects deictic "What\'s that one?" when pointer is expired (> 10s old) or has mismatched viewRevision', () => {
    const expiredPointerRes = executeVoiceOrTextCommand(baseSession, "What's that one?", 'voice', {
      xPx: 817,
      yPx: 442,
      atTimestampMs: Date.now() - 25_000,
      viewRevision: baseSession.viewRevision,
      nearestFindingId: 'finding_betelgeuse',
    });
    expect(expiredPointerRes.outcome.status).toBe('rejected');
    if (expiredPointerRes.outcome.status === 'rejected') {
      expect(expiredPointerRes.outcome.reason).toBe('ambiguous_target');
    }

    const mismatchedRevRes = executeVoiceOrTextCommand(baseSession, "What's that one?", 'voice', {
      xPx: 817,
      yPx: 442,
      atTimestampMs: Date.now() - 1_000,
      viewRevision: baseSession.viewRevision + 5,
      nearestFindingId: 'finding_betelgeuse',
    });
    expect(mismatchedRevRes.outcome.status).toBe('rejected');
    if (mismatchedRevRes.outcome.status === 'rejected') {
      expect(mismatchedRevRes.outcome.reason).toBe('ambiguous_target');
    }
  });

  it('48. executes barge-in cancellation ("Stop") and settles navigation state', () => {
    const midFlightSession = {
      ...withBetelgeuseSelected,
      machines: {
        ...withBetelgeuseSelected.machines,
        navigation: 'transitioning' as const,
      },
      spatialView: {
        ...withBetelgeuseSelected.spatialView,
        mode: 'registered_sky' as const,
        activeTransitionId: 'trans_1',
      },
    };

    const res = executeVoiceOrTextCommand(midFlightSession, 'Stop');
    expect(res.outcome.status).toBe('canceled');
    expect(res.session.machines.voice).toBe('interrupted');
    expect(res.session.machines.navigation).toBe('sky_registered');
    expect(res.session.spatialView.activeTransitionId).toBeUndefined();
  });

  it('49. rejects unsafe synthetic sky generation commands', () => {
    const res = executeVoiceOrTextCommand(baseSession, 'Add a fake nebula to the sky');
    expect(res.outcome.status).toBe('rejected');
    if (res.outcome.status === 'rejected') {
      expect(res.outcome.reason).toBe('unsafe_synthetic_edit');
    }
  });

  it('50. rejects 3D space entry on an unresolved sky session', () => {
    const unresolvedBase = createSyntheticUnresolvedSession();
    const completelyUnresolved = {
      ...unresolvedBase,
      machines: {
        ...unresolvedBase.machines,
        calibration: 'unresolved' as const,
      },
      observationSolution: {
        ...unresolvedBase.observationSolution,
        status: 'unresolved' as const,
      },
    };

    const res = executeVoiceOrTextCommand(completelyUnresolved, 'Take me to Vega');
    expect(res.outcome.status).toBe('rejected');
    if (res.outcome.status === 'rejected') {
      expect(res.outcome.reason).toBe('unresolved_sky');
    }
  });
});
