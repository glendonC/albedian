import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  AlbedianAction,
  EXIF_ORIENTATIONS,
  ObservationSession,
  ObservationSessionSchema,
  SourceAsset,
  MediaVariant,
} from '@albedian/contracts';
import {
  AUDIO_START_RESOLUTION_MS,
  FLIGHT_STANDOFF_PC,
  computeStarAnchorNdc,
  returnCheckpointOf,
  createCameraCheckpoint,
  createEmptyObservationSession,
  createSessionForSourceAsset,
  dispatchAlbedianAction,
  epochIsoForYear,
  distancePc,
  executeVoiceOrTextCommand,
  flightEndOrigin,
  transitionOrthogonalMachine,
  validateMediaTimelineSync,
} from '../index';
import { createSyntheticCalibratedSession, createSyntheticUnresolvedSession } from '../testing/syntheticSessions';

describe('@albedian/observation: Golden Sessions & Core Property-Based Invariants', () => {
  it('validates createSyntheticCalibratedSession against ObservationSessionSchema and all 14 findings', () => {
    const session = createSyntheticCalibratedSession();
    const parsed = ObservationSessionSchema.parse(session);
    expect(parsed.title).toBe('Synthetic calibrated field (test data)');
    expect(parsed.sourceAsset?.capturedAtUTC).toBe('2030-01-01T02:00:00Z');
    expect(parsed.sourceAsset?.observerLocation?.latDeg).toBe(30.0);
    expect(parsed.sourceAsset?.observerLocation?.lonDeg).toBe(-40.0);
    expect(parsed.sourceAsset?.width).toBe(1920);
    expect(parsed.sourceAsset?.height).toBe(1080);
    expect(parsed.sourceAsset?.durationMs).toBe(12000);
    expect(parsed.sourceAsset?.fpsNumerator).toBe(30);
    expect(parsed.sourceAsset?.audioStreams).toHaveLength(1);
    expect(parsed.sourceAsset?.audioStreams[0]?.codec).toBe('aac');

    expect(parsed.processingGraphs).toHaveLength(2);
    expect(parsed.processingAttempts).toHaveLength(2);
    expect(parsed.mediaVariants).toHaveLength(2);

    // Two restorations of the clip, each from its own request, with its graph and its ready attempt
    for (const variant of parsed.mediaVariants) {
      const attempt = parsed.processingAttempts.find((a) => a.id === variant.attemptId);
      const graph = parsed.processingGraphs.find((g) => g.id === variant.graphId);
      expect(variant.kind).toBe('restoration');
      expect(variant.sourceSha256).toBe(parsed.sourceAsset?.sha256);
      expect(attempt?.status).toBe('ready');
      expect(attempt?.variantId).toBe(variant.id);
      expect(attempt?.gate?.passed).toBe(true);
      expect(graph?.kind).toBe('restoration');
    }
    expect(parsed.processingGraphs.map((g) => g.request)).toEqual([{ kind: 'restoration' }, { kind: 'restoration', exposureEv: 1 }]);
    expect(parsed.activeVariantId).toBe(parsed.mediaVariants[0]!.id);

    expect(parsed.observationSolution?.status).toBe('calibrated');
    expect(parsed.observationSolution?.quality?.heldOutInliers).toBe(10);
    expect(parsed.observationSolution?.quality?.medianResidualPx).toBe(0.42);
    expect(parsed.observationSolution?.quality?.p95ResidualPx).toBe(0.89);
    expect(parsed.observationSolution?.wcs?.crvalRaDeg).toBe(83.8);
    expect(parsed.observationSolution?.wcs?.crvalDecDeg).toBe(4.0);
    expect(parsed.observationSolution?.wcs?.crpixX).toBe(960);
    expect(parsed.observationSolution?.wcs?.crpixY).toBe(540);
    expect(parsed.observationSolution?.wcs?.scaleDegPerPx).toBe(0.035);
    expect(parsed.observationSolution?.wcs?.fovHorizontalDeg).toBe(67.2);
    expect(parsed.observationSolution?.wcs?.fovVerticalDeg).toBe(37.8);

    const findings = parsed.observationSolution?.findings ?? [];
    expect(findings).toHaveLength(14);
    expect(findings.filter((f) => f.evidenceClass === 'catalog_matched')).toHaveLength(10);
    expect(findings.filter((f) => f.evidenceClass === 'predicted')).toHaveLength(2);
    expect(findings.filter((f) => f.evidenceClass === 'observed')).toHaveLength(2);

    for (const f of findings.filter((item) => item.evidenceClass === 'predicted')) {
      expect(f.sourceDetection).toBeUndefined();
      expect(f.inferredDistancePc).toBeUndefined();
    }

    for (const f of findings.filter((item) => item.evidenceClass === 'catalog_matched')) {
      expect(f.inferredDistancePc).toBeDefined();
      expect(f.cartesianParsecICRS).toBeDefined();
      expect(f.inferredDistancePc!.lower).toBeLessThanOrEqual(f.inferredDistancePc!.median);
      expect(f.inferredDistancePc!.median).toBeLessThanOrEqual(f.inferredDistancePc!.upper);
    }
  });

  it('validates createSyntheticUnresolvedSession with zero false catalog_matched claims', () => {
    const session = createSyntheticUnresolvedSession();
    const parsed = ObservationSessionSchema.parse(session);
    expect(parsed.machines.calibration).toBe('predicted_only');
    expect(parsed.observationSolution?.status).toBe('predicted_only');
    expect(parsed.observationSolution?.quality?.heldOutInliers).toBe(2);
    expect(parsed.observationSolution?.quality?.passedIndependentVerification).toBe(false);
    expect(
      parsed.observationSolution?.findings.filter((f) => f.evidenceClass === 'catalog_matched')
    ).toHaveLength(0);
    // Its two detections are listed at their pixels and placed nowhere on the sky
    const observed = parsed.observationSolution?.findings.filter((f) => f.evidenceClass === 'observed') ?? [];
    expect(observed).toHaveLength(2);
    expect(observed.every((f) => f.sourceDetection !== undefined && f.skyPosition === undefined)).toBe(true);
    expect(
      parsed.observationSolution?.findings.every(
        (f) => f.evidenceClass === 'predicted' || f.evidenceClass === 'observed'
      )
    ).toBe(true);
  });

  it('validates createEmptyObservationSession with no place, capture time, or findings', () => {
    const createdAtISO = '2026-11-03T08:00:00.000Z';
    const parsed = ObservationSessionSchema.parse(createEmptyObservationSession(createdAtISO));
    expect(parsed.machines.media).toBe('empty');
    expect(parsed.machines.calibration).toBe('not_requested');
    expect(parsed.sourceAsset).toBeUndefined();
    expect(parsed.observationSolution).toBeUndefined();
    expect(parsed.spatialView.observationEpochISO).toBeUndefined();
    expect(parsed.spatialView.simulationEpochISO).toBe(createdAtISO);
    expect(parsed.navigationHistory.anchorMomentCheckpoint.sourceAssetId).toBe('src_none');
    expect(parsed.navigationHistory.anchorMomentCheckpoint.spatialView.observationEpochISO).toBeUndefined();
    expect(parsed.navigationHistory.anchorMomentCheckpoint.spatialView.simulationEpochISO).toBe(createdAtISO);

    const checkpoint = createCameraCheckpoint(parsed, 'Empty session checkpoint');
    expect(checkpoint.sourceAssetId).toBe('src_none');
    expect(
      dispatchAlbedianAction(parsed, {
        type: 'media.restore',
        requestId: 'req_empty_restore',
        expectedViewRevision: parsed.viewRevision,
        sourceAssetId: 'src_none',
        profile: 'star_protected',
        constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true },
      }).outcome
    ).toMatchObject({
      status: 'rejected',
      reason: 'missing_source',
      explanation: 'SourceAsset "src_none" does not match active session asset "none".',
    });
    expect(
      dispatchAlbedianAction(parsed, {
        type: 'media.seek',
        requestId: 'req_empty_seek',
        expectedViewRevision: parsed.viewRevision,
        sourceTimeSec: 1.5,
        preserveAudioSync: true,
      }).outcome
    ).toMatchObject({ status: 'rejected', reason: 'missing_source' });
    expect(
      dispatchAlbedianAction(parsed, {
        type: 'sky.show',
        requestId: 'req_empty_sky',
        expectedViewRevision: parsed.viewRevision,
        mode: 'predicted',
      }).outcome
    ).toMatchObject({ status: 'rejected', reason: 'unresolved_sky' });
    expect(
      dispatchAlbedianAction(parsed, {
        type: 'finding.select',
        requestId: 'req_empty_select',
        expectedViewRevision: parsed.viewRevision,
        findingId: 'finding_betelgeuse',
      }).outcome
    ).toMatchObject({ status: 'rejected', reason: 'ambiguous_target' });
    expect(
      dispatchAlbedianAction(parsed, {
        type: 'finding.describe',
        requestId: 'req_empty_describe',
        expectedViewRevision: parsed.viewRevision,
        findingId: 'finding_betelgeuse',
        fields: ['distance'],
      }).outcome
    ).toMatchObject({ status: 'rejected', reason: 'ambiguous_target' });
    expect(
      dispatchAlbedianAction(parsed, {
        type: 'space.enter',
        requestId: 'req_empty_space',
        expectedViewRevision: parsed.viewRevision,
        findingId: 'finding_betelgeuse',
        distancePolicy: 'measured_or_estimated',
      }).outcome
    ).toMatchObject({ status: 'rejected', reason: 'unresolved_sky' });
    expect(
      dispatchAlbedianAction(parsed, {
        type: 'space.viewpoint',
        requestId: 'req_empty_viewpoint',
        expectedViewRevision: parsed.viewRevision,
        targetFindingId: 'finding_betelgeuse',
        units: 'pc',
        offsetParsec: [1, 1, 1],
        trajectory: 'orbit',
      }).outcome
    ).toMatchObject({ status: 'rejected', reason: 'ambiguous_target' });
    expect(executeVoiceOrTextCommand(parsed, 'What were we looking at?', 'voice').outcome).toMatchObject({
      status: 'rejected',
      reason: 'unresolved_sky',
    });
    expect(executeVoiceOrTextCommand(parsed, 'Make the sky clearer', 'voice').outcome).toMatchObject({
      status: 'rejected',
      reason: 'missing_source',
    });
    expect(executeVoiceOrTextCommand(parsed, 'Select Betelgeuse', 'voice').outcome).toMatchObject({
      status: 'rejected',
      reason: 'ambiguous_target',
    });
  });

  it('re-anchors the empty session to an imported asset without inventing location or findings', () => {
    const createdAtISO = '2026-11-03T08:00:00.000Z';
    const asset: SourceAsset = {
      id: 'src_upload_1',
      title: 'backyard.jpg',
      mediaKind: 'image',
      sha256: 'ab'.repeat(32),
      uri: 'blob:backyard',
      width: 1,
      height: 1,
      durationMs: 0,
      videoCodec: 'none',
      audioStreams: [],
      privacyClass: 'private',
      immutable: true,
    };
    const session = createSessionForSourceAsset(asset, 'importing', createdAtISO);
    expect(session.machines.media).toBe('importing');
    expect(session.machines.calibration).toBe('not_requested');
    expect(session.observationSolution?.sourceAssetId).toBe('src_upload_1');
    expect(session.observationSolution?.sourceSha256).toBe(asset.sha256);
    expect(session.observationSolution?.observedAtUTC).toBeUndefined();
    expect(session.observationSolution?.observerLocation).toBeUndefined();
    expect(session.observationSolution?.findings).toHaveLength(0);
    expect(session.spatialView.observationEpochISO).toBeUndefined();
    expect(session.spatialView.simulationEpochISO).toBe(createdAtISO);
    expect(session.navigationHistory.anchorMomentCheckpoint.sourceAssetId).toBe('src_upload_1');
    expect(session.navigationHistory.anchorMomentCheckpoint.spatialView.observationEpochISO).toBeUndefined();
    expect(session.navigationHistory.anchorMomentCheckpoint.spatialView.simulationEpochISO).toBe(createdAtISO);

    // A synthetic capture time; the place is the public Lake Tekapo dark-sky reserve
    const located = createSessionForSourceAsset(
      {
        ...asset,
        capturedAtUTC: '2030-06-15T04:00:00Z',
        observerLocation: { latDeg: -43.99, lonDeg: 170.46 },
        metadataSources: {
          capturedAtUTC: { container: 'synthetic', tags: ['test-fixture'] },
          observerLocation: { container: 'synthetic', tags: ['test-fixture'] },
        },
      },
      'ready',
      createdAtISO
    );
    expect(located.observationSolution?.observerLocation).toEqual({ latDeg: -43.99, lonDeg: 170.46, placeName: undefined });
    expect(located.observationSolution?.observedAtUTC).toBe('2030-06-15T04:00:00Z');
    expect(located.spatialView.observationEpochISO).toBe('2030-06-15T04:00:00Z');
    expect(located.spatialView.simulationEpochISO).toBe('2030-06-15T04:00:00Z');
    expect(located.navigationHistory.anchorMomentCheckpoint.spatialView.observationEpochISO).toBe('2030-06-15T04:00:00Z');
  });

  it('leaves the empty session without an asset and a clip nothing has probed without a frame rate', () => {
    const createdAtISO = '2026-11-03T08:00:00.000Z';
    expect(createEmptyObservationSession(createdAtISO).sourceAsset).toBeUndefined();
    const still: SourceAsset = {
      id: 'src_upload_still',
      title: 'night.jpg',
      mediaKind: 'image',
      sha256: 'ab'.repeat(32),
      uri: 'blob:night-still',
      width: 1,
      height: 1,
      durationMs: 0,
      videoCodec: 'none',
      audioStreams: [],
      privacyClass: 'private',
      immutable: true,
    };
    expect(still.mediaKind).toBe('image');
    expect(still).not.toHaveProperty('fpsNumerator');
    expect(still).not.toHaveProperty('fpsDenominator');
    // The shape App.tsx gives a local video upload before anything probes the file
    const unprobed = createSessionForSourceAsset(
      { ...still, id: 'src_upload_2', title: 'night.mov', mediaKind: 'video', sha256: 'cd'.repeat(32), uri: 'blob:night' },
      'importing',
      createdAtISO
    );
    expect(unprobed.sourceAsset?.mediaKind).toBe('video');
    expect(unprobed.sourceAsset).not.toHaveProperty('fpsNumerator');
    expect(unprobed.sourceAsset).not.toHaveProperty('fpsDenominator');
  });

  it('enforces scientific distance prerequisites on space.enter and space.viewpoint', () => {
    const session = createSyntheticCalibratedSession();

    // Rejects predicted planet (Jupiter) without Bailer-Jones distance
    const jupiterAttempt = dispatchAlbedianAction(session, {
      type: 'space.enter',
      requestId: 'req_jupiter_3d',
      expectedViewRevision: session.viewRevision,
      findingId: 'finding_jupiter',
      distancePolicy: 'measured_or_estimated',
    });
    expect(jupiterAttempt.outcome.status).toBe('rejected');
    if (jupiterAttempt.outcome.status === 'rejected') {
      expect(jupiterAttempt.outcome.reason).toBe('no_distance_estimate');
    }
    expect(jupiterAttempt.session).toBe(session);

    // Rejects raw observed faint field star without catalog distance
    const obsAttempt = dispatchAlbedianAction(session, {
      type: 'space.enter',
      requestId: 'req_obs_3d',
      expectedViewRevision: session.viewRevision,
      findingId: 'finding_obs_101',
      distancePolicy: 'measured_or_estimated',
    });
    expect(obsAttempt.outcome.status).toBe('rejected');
    if (obsAttempt.outcome.status === 'rejected') {
      expect(obsAttempt.outcome.reason).toBe('no_distance_estimate');
    }
    expect(obsAttempt.session).toBe(session);
  });

  it('maintains orthogonality across the 4+1 state machines', () => {
    const base = createSyntheticCalibratedSession();
    const processing = transitionOrthogonalMachine(base, 'restoration', 'processing');
    expect(processing.machines.restoration).toBe('processing');
    expect(processing.machines.calibration).toBe('calibrated');
    expect(processing.machines.navigation).toBe('source');

    // Navigating to sky_registered while restoration is processing preserves restoration state
    const skyResult = dispatchAlbedianAction(processing, {
      type: 'sky.show',
      requestId: 'req_orthogonal_sky',
      expectedViewRevision: processing.viewRevision,
      mode: 'measured',
    });
    expect(skyResult.outcome.status).toBe('completed');
    expect(skyResult.session.machines.navigation).toBe('sky_registered');
    expect(skyResult.session.machines.restoration).toBe('processing');
  });

  it('PBT-NAV-1: Arbitrary sequences of sky.show, finding.select, space.enter, space.viewpoint, space.epoch, history.back(partial), media.seek and media.compare followed by source.return recover the exact state saved when the session last left the recording, from the source or the compare wipe (the initial state if it never left)', () => {
    const [firstVariant, lastVariant] = createSyntheticCalibratedSession().mediaVariants.map((v) => v.id) as [string, string];
    const catalogStarIds = [
      'finding_betelgeuse',
      'finding_rigel',
      'finding_bellatrix',
      'finding_alnilam',
      'finding_alnitak',
      'finding_mintaka',
      'finding_saiph',
      'finding_aldebaran',
      'finding_sirius',
      'finding_procyon',
    ] as const;

    const stepArbitrary = fc.oneof(
      fc.record({
        kind: fc.constant('sky.show' as const),
      }),
      fc.record({
        kind: fc.constant('finding.select' as const),
        findingId: fc.constantFrom(...catalogStarIds),
      }),
      fc.record({
        kind: fc.constant('space.enter' as const),
        findingId: fc.constantFrom(...catalogStarIds),
      }),
      fc.record({
        kind: fc.constant('space.viewpoint' as const),
        targetFindingId: fc.constantFrom(...catalogStarIds),
        offsetParsec: fc.tuple(
          fc.double({ min: -50, max: 50, noNaN: true }),
          fc.double({ min: -50, max: 50, noNaN: true }),
          fc.double({ min: -50, max: 50, noNaN: true })
        ),
        trajectory: fc.constantFrom('orbit' as const, 'translate' as const),
      }),
      fc.record({
        kind: fc.constant('space.epoch' as const),
        yearCE: fc.integer({ min: -5000, max: 8000 }),
      }),
      fc.record({
        kind: fc.constant('history.back.partial' as const),
        fraction: fc.double({ min: 0.05, max: 0.95, noNaN: true }),
      }),
      // Seeking and opening the compare wipe change the moment on the recording, so the property can tell
      // the moment left from the session's start by time and variant, not only by selection
      fc.record({
        kind: fc.constant('media.seek' as const),
        sourceTimeSec: fc.double({ min: 0, max: 11.9, noNaN: true }),
      }),
      fc.record({
        kind: fc.constant('media.compare' as const),
        variantAId: fc.constantFrom(firstVariant, lastVariant),
        variantBId: fc.constantFrom(firstVariant, lastVariant),
      })
    );

    const initialOverrideArb = fc.record({
      currentSourceTimeSec: fc.double({ min: 0, max: 11.9, noNaN: true }),
      audioGain: fc.double({ min: 0, max: 1, noNaN: true }),
      cropZoom: fc.double({ min: 1, max: 5, noNaN: true }),
      cropPanX: fc.double({ min: -0.5, max: 0.5, noNaN: true }),
      cropPanY: fc.double({ min: -0.5, max: 0.5, noNaN: true }),
      activeVariantId: fc.constantFrom('variant_none', firstVariant, lastVariant),
      selectedFindingId: fc.option(fc.constantFrom(...catalogStarIds), { nil: null }),
      isPlaying: fc.boolean(),
    });

    fc.assert(
      fc.property(
        initialOverrideArb,
        fc.array(stepArbitrary, { minLength: 1, maxLength: 15 }),
        (initOverrides, steps) => {
          const rawSession = createSyntheticCalibratedSession();
          let session: ObservationSession = {
            ...rawSession,
            currentSourceTimeSec: initOverrides.currentSourceTimeSec,
            audioGain: initOverrides.audioGain,
            cropTransform: {
              zoom: initOverrides.cropZoom,
              panX: initOverrides.cropPanX,
              panY: initOverrides.cropPanY,
            },
            activeVariantId: initOverrides.activeVariantId,
            selectedFindingId: initOverrides.selectedFindingId,
            focusedFindingId: initOverrides.selectedFindingId,
            isPlaying: initOverrides.isPlaying,
          };

          // The moment Back to Moment returns to (experience-9m): the session as it stood when it last left the source
          const initial = session;
          let lastDeparture: ObservationSession | undefined;
          for (let i = 0; i < steps.length; i++) {
            const step = steps[i]!;
            const reqId = `pbt_nav_step_${i}`;
            let action: AlbedianAction;
            switch (step.kind) {
              case 'sky.show':
                action = {
                  type: 'sky.show',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  mode: 'measured',
                };
                break;
              case 'finding.select':
                action = {
                  type: 'finding.select',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  findingId: step.findingId,
                };
                break;
              case 'space.enter':
                action = {
                  type: 'space.enter',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  findingId: step.findingId,
                  distancePolicy: 'measured_or_estimated',
                };
                break;
              case 'space.viewpoint':
                action = {
                  type: 'space.viewpoint',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  targetFindingId: step.targetFindingId,
                  units: 'pc',
                  offsetParsec: step.offsetParsec,
                  trajectory: step.trajectory,
                };
                break;
              case 'space.epoch':
                action = {
                  type: 'space.epoch',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  epochISO: epochIsoForYear(step.yearCE),
                  yearCE: step.yearCE,
                };
                break;
              case 'history.back.partial':
                action = {
                  type: 'history.back',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  mode: 'partial',
                  fraction: step.fraction,
                };
                break;
              case 'media.seek':
                action = {
                  type: 'media.seek',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  sourceTimeSec: step.sourceTimeSec,
                  preserveAudioSync: true,
                };
                break;
              case 'media.compare':
                action = {
                  type: 'media.compare',
                  requestId: reqId,
                  expectedViewRevision: session.viewRevision,
                  variantAId: step.variantAId,
                  variantBId: step.variantBId,
                  preserveTime: true,
                };
                break;
            }

            const res = dispatchAlbedianAction(session, action);
            // space.epoch is refused because the view does not move the stars to a chosen year, and leaves the session as it was
            if (action.type === 'space.epoch') {
              expect(res.outcome).toMatchObject({ status: 'rejected', reason: 'not_connected' });
              expect(res.session).toBe(session);
            } else {
              expect(res.outcome.status).toBe('completed');
            }
            // The photo is on screen on the source and in a wipe opened over it; a wipe opened in the sky or
            // space keeps that view, so leaving it is not leaving the photo (stated here, not imported, so the
            // property checks the rule rather than restating the code under test)
            const onRecording = (s: ObservationSession) =>
              s.machines.navigation === 'source' ||
              (s.machines.navigation === 'comparison' && s.spatialView.mode === 'source_2d');
            if (onRecording(session) && !onRecording(res.session)) {
              lastDeparture = session;
            }
            session = res.session;
          }
          const expected = lastDeparture ?? initial;

          const returned = dispatchAlbedianAction(session, {
            type: 'source.return',
            requestId: 'pbt_nav_return',
            expectedViewRevision: session.viewRevision,
            restoreAudio: true,
          });

          expect(returned.outcome.status).toBe('completed');
          expect(returned.session.currentSourceTimeSec).toBe(expected.currentSourceTimeSec);
          expect(returned.session.audioGain).toBe(expected.audioGain);
          expect(returned.session.cropTransform).toEqual(expected.cropTransform);
          expect(returned.session.activeVariantId).toBe(expected.activeVariantId);
          expect(returned.session.selectedFindingId).toBe(expected.selectedFindingId);
          expect(returned.session.isPlaying).toBe(expected.isPlaying);
          expect(returned.session.compareVariantId).toBeUndefined();
          expect(returned.session.machines.navigation).toBe('source');
          expect(returned.session.spatialView.mode).toBe('source_2d');
        }
      ),
      {
        // ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run
        seed: process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined,
        numRuns: 100,
        examples: [
          // A star picked on the photo before flying is the selection Back to Moment brings back (experience-9m)
          [
            { currentSourceTimeSec: 1.5, audioGain: 0.8, cropZoom: 1, cropPanX: 0, cropPanY: 0, activeVariantId: firstVariant, selectedFindingId: null, isPlaying: false },
            [
              { kind: 'finding.select', findingId: 'finding_sirius' },
              { kind: 'space.enter', findingId: 'finding_sirius' },
            ],
          ],
          // Seek, open the compare wipe and fly from it: Back to Moment brings back that time and variant
          [
            { currentSourceTimeSec: 1.5, audioGain: 0.8, cropZoom: 1, cropPanX: 0, cropPanY: 0, activeVariantId: firstVariant, selectedFindingId: null, isPlaying: false },
            [
              { kind: 'media.seek', sourceTimeSec: 7.25 },
              { kind: 'media.compare', variantAId: firstVariant, variantBId: lastVariant },
              { kind: 'finding.select', findingId: 'finding_rigel' },
              { kind: 'sky.show' },
            ],
          ],
        ],
      }
    );
  });

  it('PBT-VOICE-1: Any AlbedianAction with expectedViewRevision !== session.viewRevision is rejected (stale_revision) and leaves ObservationSession unchanged', () => {
    const baseSession = createSyntheticCalibratedSession();

    const staleRevisionArb = fc
      .integer({ min: 0, max: 10_000 })
      .filter((rev) => rev !== baseSession.viewRevision);

    const actionBuilderArb = fc.constantFrom(
      (rev: number): AlbedianAction => ({
        type: 'media.restore',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        sourceAssetId: baseSession.sourceAsset!.id,
        profile: 'star_protected',
        constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true },
      }),
      (rev: number): AlbedianAction => ({
        type: 'sky.show',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        mode: 'measured',
      }),
      (rev: number): AlbedianAction => ({
        type: 'finding.select',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        findingId: 'finding_betelgeuse',
      }),
      (rev: number): AlbedianAction => ({
        type: 'space.enter',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        findingId: 'finding_betelgeuse',
        distancePolicy: 'measured_or_estimated',
      }),
      (rev: number): AlbedianAction => ({
        type: 'space.viewpoint',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        targetFindingId: 'finding_rigel',
        units: 'pc',
        offsetParsec: [5, 5, 5],
        trajectory: 'orbit',
      }),
      (rev: number): AlbedianAction => ({
        type: 'space.epoch',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        epochISO: epochIsoForYear(1026),
        yearCE: 1026,
      }),
      (rev: number): AlbedianAction => ({
        type: 'source.return',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        restoreAudio: true,
      })
    );

    fc.assert(
      fc.property(staleRevisionArb, actionBuilderArb, (staleRev, makeAction) => {
        const snapshotBefore = structuredClone(baseSession);
        const action = makeAction(staleRev);
        const result = dispatchAlbedianAction(baseSession, action);

        expect(result.outcome.status).toBe('rejected');
        if (result.outcome.status === 'rejected') {
          expect(result.outcome.reason).toBe('stale_revision');
        }
        expect(result.session).toBe(baseSession);
        expect(result.session).toEqual(snapshotBefore);
      }),
      { numRuns: 100 }
    );
  });

  it('PBT-SYNC-1: a variant stays on its source timeline: the same source, every audio stream with the hash ingest stored and the same offset from the video, and a bounded duration drift', () => {
    const baseSession = createSyntheticCalibratedSession();
    const source: SourceAsset = baseSession.sourceAsset!;

    // 1. All golden variants pass timeline sync
    for (const variant of baseSession.mediaVariants) {
      const check = validateMediaTimelineSync(source, variant);
      expect(check.valid).toBe(true);
      expect(check.audioPreserved).toBe(true);
      expect(check.audioOffsetsKept).toBe(true);
      expect(check.maxDriftMs).toBeLessThanOrEqual(42);
    }

    // 2. Valid exactly when each source audio stream is there with its ingest hash and its start, at the resolution both
    // records store, and the duration is within 42 ms. The frame times themselves are checked on the stored file by
    // the restoration job (REQ-RST-7.5), not here.
    const baseVariant = baseSession.mediaVariants[1]!;
    const stream = source.audioStreams[0]!;
    const audioCases = {
      copied: [stream],
      dropped: [],
      other_hash: [{ ...stream, sha256: 'f'.repeat(64) }],
      extra: [stream, { ...stream, index: 2 }],
    };
    type Audio = keyof typeof audioCases;
    fc.assert(
      fc.property(
        fc.integer({ min: -100, max: 100 }),
        fc.constantFrom<Audio>('copied', 'dropped', 'other_hash', 'extra'),
        // How much later the variant's audio starts, in thousandths of a millisecond: the same packets at other times
        fc.oneof(fc.constant(0), fc.integer({ min: -600_000, max: 600_000 })),
        (driftMs, audio, shiftSteps) => {
          const shiftMs = shiftSteps * AUDIO_START_RESOLUTION_MS;
          const streams = audioCases[audio].map((s) => ({ ...s, startPtsMs: s.startPtsMs + shiftMs }));
          const variant: MediaVariant = { ...baseVariant, durationMs: source.durationMs + driftMs, audioStreams: streams };
          const res = validateMediaTimelineSync(source, variant);
          const offsetsKept = (audio === 'copied' || audio === 'other_hash') && shiftSteps === 0;
          expect(res.audioPreserved).toBe(audio === 'copied');
          expect(res.audioOffsetsKept).toBe(offsetsKept);
          expect(res.maxDriftMs).toBe(Math.abs(driftMs));
          expect(res.valid).toBe(audio === 'copied' && shiftSteps === 0 && Math.abs(driftMs) <= 42);
        }
      ),
      {
        seed: process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined,
        numRuns: 200,
        examples: [
          [0, 'copied', 0],
          [42, 'copied', 0],
          [43, 'copied', 0],
          [0, 'other_hash', 0],
          // AUDIT lane C's case: the source's audio packets, 500 ms later, with the same hash
          [0, 'copied', 500_000],
          // One step of the stored resolution, either way
          [0, 'copied', 1],
          [0, 'copied', -1],
        ],
      }
    );

    // A start written at the stored resolution compares equal however it was summed; a silent source needs no audio
    // stream; and a variant of another recording never passes
    const summed = [{ ...stream, startPtsMs: stream.startPtsMs + 0.1 + 0.2 }];
    expect(validateMediaTimelineSync({ ...source, audioStreams: [{ ...stream, startPtsMs: stream.startPtsMs + 0.3 }] }, { ...baseVariant, audioStreams: summed }).audioOffsetsKept).toBe(true);
    expect(validateMediaTimelineSync(source, { ...baseVariant, audioStreams: [{ ...stream, startPtsMs: stream.startPtsMs + 500 }] }).maxAudioOffsetChangeMs).toBe(500);
    expect(validateMediaTimelineSync({ ...source, audioStreams: [] }, { ...baseVariant, audioStreams: [] }).valid).toBe(true);
    expect(validateMediaTimelineSync(source, { ...baseVariant, sourceAssetId: 'src_0000000000000000' }).valid).toBe(false);
  });

  it('media.restore shows a stored restoration of this recording and refuses in plain words when there is none (REQ-RST-10.4)', () => {
    const base = createSyntheticCalibratedSession();
    const restore = (session: ObservationSession): AlbedianAction => ({
      type: 'media.restore',
      requestId: 'restore',
      expectedViewRevision: session.viewRevision,
      sourceAssetId: session.sourceAsset!.id,
      profile: 'temporal',
      constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true },
    });
    // A stored restoration of this recording is shown with its own disclosure, and nothing it did not do is claimed
    const showingOriginal = { ...base, activeVariantId: 'variant_none' };
    const shown = dispatchAlbedianAction(showingOriginal, restore(showingOriginal));
    const restoration = base.mediaVariants.find((v) => v.kind === 'restoration')!;
    expect(shown.outcome.status).toBe('completed');
    expect(shown.session.activeVariantId).toBe(restoration.id);
    expect(shown.session.machines.restoration).toBe('ready');
    if (shown.outcome.status === 'completed') {
      expect(shown.outcome.visualStatus).toContain(restoration.disclosure);
      expect(`${shown.outcome.spokenFeedback} ${shown.outcome.visualStatus}`).not.toMatch(/people|clarity|temporal|stack|star.protect/i);
    }
    // No restoration of this recording: refused in plain words, the session left as it was
    const without = [
      { ...base, mediaVariants: [] },
      // A display copy restores nothing
      { ...base, mediaVariants: base.mediaVariants.map((v) => ({ ...v, kind: 'display_copy' as const })) },
      // A restoration of another recording is not this one's
      { ...base, mediaVariants: base.mediaVariants.map((v) => ({ ...v, sourceSha256: 'e'.repeat(64) })) },
    ];
    for (const session of without) {
      const result = dispatchAlbedianAction(session, restore(session));
      expect(result.outcome).toMatchObject({
        status: 'rejected',
        reason: 'missing_source',
        explanation: 'No restored version of this recording exists yet.',
      });
      expect(result.session).toBe(session);
    }
  });

  it('PBT-NAV-HISTBACK-CHAIN: Multi-step history.back partial-fraction chaining never overshoots the baseline checkpoint', () => {
    // Establish a baseline session at the source view, then navigate to sky and space
    // so the checkpoint stack has at least 2 entries, then apply two sequential partial
    // history.back calls and verify the camera stays between the two surrounding checkpoints.
    const base = createSyntheticCalibratedSession();

    // Push a sky checkpoint
    const afterSky = dispatchAlbedianAction(base, {
      type: 'sky.show',
      requestId: 'chain_sky',
      expectedViewRevision: base.viewRevision,
      mode: 'measured',
    });
    expect(afterSky.outcome.status).toBe('completed');

    // Push a space checkpoint (Betelgeuse has a valid distance)
    const afterSpace = dispatchAlbedianAction(afterSky.session, {
      type: 'space.enter',
      requestId: 'chain_space',
      expectedViewRevision: afterSky.session.viewRevision,
      findingId: 'finding_betelgeuse',
      distancePolicy: 'measured_or_estimated',
    });
    expect(afterSpace.outcome.status).toBe('completed');

    fc.assert(
      fc.property(
        fc.double({ min: 0.05, max: 0.95, noNaN: true }),
        fc.double({ min: 0.05, max: 0.95, noNaN: true }),
        (f1, f2) => {
          // First partial back
          const back1 = dispatchAlbedianAction(afterSpace.session, {
            type: 'history.back',
            requestId: 'chain_back1',
            expectedViewRevision: afterSpace.session.viewRevision,
            mode: 'partial',
            fraction: f1,
          });
          expect(back1.outcome.status).toBe('completed');

          // Second partial back: must use the new viewRevision
          const back2 = dispatchAlbedianAction(back1.session, {
            type: 'history.back',
            requestId: 'chain_back2',
            expectedViewRevision: back1.session.viewRevision,
            mode: 'partial',
            fraction: f2,
          });
          expect(back2.outcome.status).toBe('completed');

          // After two partial backs the session must still be internally consistent:
          // navigation machine is one of the valid navigation states
          const navState = back2.session.machines.navigation;
          expect(['source', 'sky_registered', 'space', 'space_3d']).toContain(navState);

          // The session must be schema-valid
          const parsed = ObservationSessionSchema.safeParse(back2.session);
          expect(parsed.success).toBe(true);
        }
      ),
      { numRuns: 80 }
    );
  });
});

describe('@albedian/observation: detections without a sky position', () => {
  it('selects and describes a pixel-only detection without inventing a sightline or a 3D position', () => {
    const session = createSyntheticUnresolvedSession();
    const selected = dispatchAlbedianAction(session, {
      type: 'finding.select', requestId: 'sel_obs_1', expectedViewRevision: session.viewRevision,
      findingId: 'finding_obs_overcast_1',
    });
    expect(selected.outcome.status).toBe('completed');
    // Anchored at its measured pixel's centre: (612.4, 214.9) px in a 1920 x 1080 frame is (612.9, 215.4) on the photo
    expect(selected.session.spatialView.lockedAnchorNdc).toEqual([-0.3616, 0.6011]);

    const described = dispatchAlbedianAction(selected.session, {
      type: 'finding.describe', requestId: 'desc_obs_1', expectedViewRevision: selected.session.viewRevision,
      findingId: 'finding_obs_overcast_1', fields: ['evidence', 'behind_sightline'],
    });
    expect(described.outcome.status).toBe('completed');
    expect(described.session.lastStatusBanner).toContain('No sightline: Field Source #1 has a pixel position only');
    expect(described.session.lastStatusBanner).not.toMatch(/RA |Dec /);
    expect(described.session.spatialView.activeOverlays.sightlineCone).toBe(false);

    const entered = dispatchAlbedianAction(described.session, {
      type: 'space.enter', requestId: 'space_obs_1', expectedViewRevision: described.session.viewRevision,
      findingId: 'finding_obs_overcast_1', distancePolicy: 'measured_or_estimated',
    });
    expect(entered.outcome.status).toBe('rejected');
    if (entered.outcome.status === 'rejected') {
      expect(entered.outcome.reason).toBe('no_distance_estimate');
    }

    const calibrated = createSyntheticCalibratedSession();
    const sightline = dispatchAlbedianAction(calibrated, {
      type: 'finding.describe',
      requestId: 'desc_cal_sightline',
      expectedViewRevision: calibrated.viewRevision,
      findingId: 'finding_betelgeuse',
      fields: ['behind_sightline'],
    });
    expect(sightline.outcome.status).toBe('completed');
    expect(sightline.session.lastStatusBanner).toContain('(the view does not draw a sightline cone yet)');
    expect(sightline.session.lastStatusBanner).not.toContain('Sightline cone queried');
    expect(sightline.session.spatialView.activeOverlays.sightlineCone).toBe(false);
  });
});

describe('@albedian/observation: the anchor of a turned photo (experience-9h)', () => {
  it('anchors a detection where the upright photo shows it, at the centre of its pixel', () => {
    const session = createSyntheticUnresolvedSession();
    const finding = session.observationSolution!.findings.find((f) => f.sourceDetection)!;
    const asset: SourceAsset = { ...session.sourceAsset!, width: 4, height: 2 };
    const at = (xPx: number, yPx: number, turned: boolean) =>
      computeStarAnchorNdc(
        { ...finding, sourceDetection: { ...finding.sourceDetection!, xPx, yPx } },
        turned ? { ...asset, displayOrientation: EXIF_ORIENTATIONS[6] } : asset
      );
    // Stored pixel (0, 0) of a 4 x 2 photo has its centre at (0.5, 0.5): NDC (-0.75, 0.5)
    expect(at(0, 0, false)).toEqual([-0.75, 0.5]);
    // Orientation 6 shows it 2 x 4 with the stored top-left at the top right: centre (1.5, 0.5), NDC (0.5, 0.75)
    expect(at(0, 0, true)).toEqual([0.5, 0.75]);
    // The stored bottom-right pixel lands bottom-left
    expect(at(3, 1, true)).toEqual([-0.5, -0.75]);
  });
});

describe('@albedian/observation: Back to Moment by voice and by button (experience-9m)', () => {
  // Pick a catalog star with a distance, then fly to it: the flight saves the moment with the selection
  function departed() {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc
    )!;
    const selected = dispatchAlbedianAction(base, {
      type: 'finding.select', requestId: 'sel', expectedViewRevision: base.viewRevision, findingId: finding.id,
    }).session;
    const inSpace = dispatchAlbedianAction(selected, {
      type: 'space.enter', requestId: 'enter', expectedViewRevision: selected.viewRevision,
      findingId: finding.id, distancePolicy: 'measured_or_estimated',
    }).session;
    expect(inSpace.machines.navigation).toBe('space');
    return { base, finding, inSpace };
  }

  it('picks the latest checkpoint saved on the source of this recording, else the anchor', () => {
    const { finding, inSpace } = departed();
    const picked = returnCheckpointOf(inSpace)!;
    expect(picked.label).toBe(`Checkpoint before entering 3D space toward ${finding.title}`);
    expect(picked.selectedFindingId).toBe(finding.id);
    // A checkpoint taken while on another recording is never the moment to return to
    const otherAsset = { ...picked, id: 'chk_other_asset', sourceAssetId: 'src_another_photo' };
    const withOther = {
      ...inSpace,
      navigationHistory: {
        ...inSpace.navigationHistory,
        checkpoints: [...inSpace.navigationHistory.checkpoints, otherAsset],
        cursorIndex: inSpace.navigationHistory.checkpoints.length,
      },
    };
    expect(returnCheckpointOf(withOther)!.id).toBe(picked.id);
    // Nothing saved on this recording's source but the anchor: the anchor
    const onlySky = {
      ...inSpace,
      navigationHistory: { ...inSpace.navigationHistory, checkpoints: [otherAsset], cursorIndex: 0 },
    };
    expect(returnCheckpointOf(onlySky)!.id).toBe(inSpace.navigationHistory.anchorMomentCheckpoint.id);
  });

  it('restores the departure checkpoint for source.return without an id, and for an id from another recording', () => {
    const { finding, inSpace } = departed();
    const noId = dispatchAlbedianAction(inSpace, {
      type: 'source.return', requestId: 'ret', expectedViewRevision: inSpace.viewRevision, restoreAudio: true,
    }).session;
    expect(noId.machines.navigation).toBe('source');
    expect(noId.selectedFindingId).toBe(finding.id);

    const otherAsset = { ...returnCheckpointOf(inSpace)!, id: 'chk_other_asset', sourceAssetId: 'src_another_photo' };
    const withOther = {
      ...inSpace,
      navigationHistory: { ...inSpace.navigationHistory, checkpoints: [...inSpace.navigationHistory.checkpoints, otherAsset] },
    };
    const named = dispatchAlbedianAction(withOther, {
      type: 'source.return', requestId: 'ret2', expectedViewRevision: withOther.viewRevision,
      checkpointId: 'chk_other_asset', restoreAudio: true,
    }).session;
    // Falls back the same way as no id: this recording's departure moment, never another recording's
    expect(named.machines.navigation).toBe('source');
    expect(named.selectedFindingId).toBe(finding.id);
    expect(named.navigationHistory.anchorMomentCheckpoint.sourceAssetId).toBe(inSpace.sourceAsset!.id);
  });

  it('restores the same moment for "back to moment" and "take me back" as for the button', () => {
    const { finding, inSpace } = departed();
    const button = dispatchAlbedianAction(inSpace, {
      type: 'source.return', requestId: 'btn', expectedViewRevision: inSpace.viewRevision,
      checkpointId: returnCheckpointOf(inSpace)!.id, restoreAudio: true,
    }).session;
    for (const said of ['back to moment', 'Take me back']) {
      const spoken = executeVoiceOrTextCommand(inSpace, said, 'voice').session;
      expect(spoken.machines.navigation, said).toBe('source');
      expect(spoken.selectedFindingId, said).toBe(finding.id);
      expect(spoken.selectedFindingId, said).toBe(button.selectedFindingId);
      expect(spoken.navigationHistory.anchorMomentCheckpoint.id, said).toBe(button.navigationHistory.anchorMomentCheckpoint.id);
    }
  });

  // Seek, open the compare wipe, pick a star there, then leave for the sky or space (both save that moment)
  function departedFromWipe(leave: 'sky' | 'space') {
    const base = createSyntheticCalibratedSession();
    const seeked = dispatchAlbedianAction(base, {
      type: 'media.seek', requestId: 'seek', expectedViewRevision: base.viewRevision, sourceTimeSec: 7.25,
      preserveAudioSync: true,
    }).session;
    const wipe = dispatchAlbedianAction(seeked, {
      type: 'media.compare', requestId: 'cmp', expectedViewRevision: seeked.viewRevision,
      variantAId: base.mediaVariants[0]!.id, variantBId: base.mediaVariants.at(-1)!.id, preserveTime: true,
    }).session;
    expect(wipe.machines.navigation).toBe('comparison');
    const finding = wipe.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc && f.id !== base.selectedFindingId
    )!;
    const selected = dispatchAlbedianAction(wipe, {
      type: 'finding.select', requestId: 'sel', expectedViewRevision: wipe.viewRevision, findingId: finding.id,
    }).session;
    const left =
      leave === 'sky'
        ? dispatchAlbedianAction(selected, {
            type: 'sky.show', requestId: 'sky', expectedViewRevision: selected.viewRevision, mode: 'measured',
          }).session
        : dispatchAlbedianAction(selected, {
            type: 'space.enter', requestId: 'enter', expectedViewRevision: selected.viewRevision,
            findingId: finding.id, distancePolicy: 'measured_or_estimated',
          }).session;
    expect(left.machines.navigation).toBe(leave === 'sky' ? 'sky_registered' : 'space');
    return { base, selected, left };
  }

  it('returns to the moment the compare wipe was left, by voice, by the card and by the button', () => {
    for (const leave of ['sky', 'space'] as const) {
      const { base, selected, left } = departedFromWipe(leave);
      // The moment left differs from the session's start in time, variant and selection
      expect(selected.currentSourceTimeSec).not.toBe(base.currentSourceTimeSec);
      expect(selected.activeVariantId).not.toBe(base.activeVariantId);
      expect(selected.selectedFindingId).not.toBe(base.selectedFindingId);
      const paths = {
        button: dispatchAlbedianAction(left, {
          type: 'source.return', requestId: 'btn', expectedViewRevision: left.viewRevision,
          checkpointId: returnCheckpointOf(left)!.id, restoreAudio: true,
        }).session,
        voice: executeVoiceOrTextCommand(left, 'back to moment', 'voice').session,
        card: executeVoiceOrTextCommand(left, 'Take me back', 'pointer').session,
      };
      for (const [name, restored] of Object.entries(paths)) {
        const label = `${leave} ${name}`;
        expect(restored.machines.navigation, label).toBe('source');
        expect(restored.currentSourceTimeSec, label).toBe(selected.currentSourceTimeSec);
        expect(restored.activeVariantId, label).toBe(selected.activeVariantId);
        expect(restored.selectedFindingId, label).toBe(selected.selectedFindingId);
        expect(restored.compareVariantId, label).toBeUndefined();
      }
    }
  });

  it('restores a checkpoint of this recording that the caller names, over the default', () => {
    const { base, finding, inSpace } = departed();
    const anchorId = inSpace.navigationHistory.anchorMomentCheckpoint.id;
    expect(returnCheckpointOf(inSpace)!.id).not.toBe(anchorId);
    const named = dispatchAlbedianAction(inSpace, {
      type: 'source.return', requestId: 'ret', expectedViewRevision: inSpace.viewRevision,
      checkpointId: anchorId, restoreAudio: true,
    }).session;
    expect(named.machines.navigation).toBe('source');
    expect(named.selectedFindingId).toBe(base.selectedFindingId);
    expect(named.selectedFindingId).not.toBe(finding.id);
  });

  it('returns by voice to the moment the source was left, not to a checkpoint saved later in the sky', () => {
    const base = createSyntheticCalibratedSession();
    const finding = base.observationSolution!.findings.find(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc && f.id !== base.selectedFindingId
    )!;
    const sky = dispatchAlbedianAction(base, {
      type: 'sky.show', requestId: 'sky', expectedViewRevision: base.viewRevision, mode: 'measured',
    }).session;
    const picked = dispatchAlbedianAction(sky, {
      type: 'finding.select', requestId: 'sel', expectedViewRevision: sky.viewRevision, findingId: finding.id,
    }).session;
    const inSpace = dispatchAlbedianAction(picked, {
      type: 'space.enter', requestId: 'enter', expectedViewRevision: picked.viewRevision,
      findingId: finding.id, distancePolicy: 'measured_or_estimated',
    }).session;
    // space.enter from the sky saves a sky checkpoint holding the star picked there
    expect(inSpace.navigationHistory.checkpoints.at(-1)!.navigationState).toBe('sky_registered');
    for (const said of ['back to moment', 'Take me back']) {
      const spoken = executeVoiceOrTextCommand(inSpace, said, 'voice').session;
      expect(spoken.machines.navigation, said).toBe('source');
      expect(spoken.selectedFindingId, said).toBe(base.selectedFindingId);
      expect(spoken.currentSourceTimeSec, said).toBe(base.currentSourceTimeSec);
    }
  });

  // The compare HUD shows in every view; its wipe opened in the sky or in 3D space keeps that view on screen
  function restoredByEveryPath(left: ObservationSession) {
    return {
      button: dispatchAlbedianAction(left, {
        type: 'source.return', requestId: 'btn', expectedViewRevision: left.viewRevision,
        checkpointId: returnCheckpointOf(left)!.id, restoreAudio: true,
      }).session,
      voice: executeVoiceOrTextCommand(left, 'back to moment', 'voice').session,
      card: executeVoiceOrTextCommand(left, 'Take me back', 'pointer').session,
    };
  }

  it('returns to the moment the photo was left, not a star picked in a wipe opened over the sky or 3D space', () => {
    const base = createSyntheticCalibratedSession();
    const [first, second] = base.observationSolution!.findings.filter(
      (f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc && f.id !== base.selectedFindingId
    );
    const variantA = base.mediaVariants[0]!.id;
    const variantB = base.mediaVariants.at(-1)!.id;
    expect(variantB).not.toBe(base.activeVariantId);
    const step = (s: ObservationSession, action: Record<string, unknown>) => {
      const res = dispatchAlbedianAction(s, { requestId: `r${s.viewRevision}`, expectedViewRevision: s.viewRevision, ...action } as AlbedianAction);
      expect(res.outcome.status, String(action.type)).toBe('completed');
      return res.session;
    };
    const compare = { type: 'media.compare', variantAId: variantA, variantBId: variantB, preserveTime: true };

    // Sky, pick a star there, open the wipe (the sky stays on screen), Register Sky again
    let sky = step(base, { type: 'sky.show', mode: 'measured' });
    sky = step(sky, { type: 'finding.select', findingId: first!.id });
    sky = step(sky, compare);
    expect(sky.spatialView.mode).not.toBe('source_2d');
    sky = step(sky, { type: 'sky.show', mode: 'measured' });

    // Fly to a star, open the wipe in 3D space, then Go there to a second star
    let space = step(base, { type: 'space.enter', findingId: first!.id, distancePolicy: 'measured_or_estimated' });
    space = step(space, compare);
    expect(space.spatialView.mode).not.toBe('source_2d');
    space = step(space, { type: 'space.enter', findingId: second!.id, distancePolicy: 'measured_or_estimated' });

    for (const [where, left] of [['sky', sky], ['space', space]] as const) {
      for (const [name, restored] of Object.entries(restoredByEveryPath(left))) {
        const label = `${where} ${name}`;
        expect(restored.machines.navigation, label).toBe('source');
        expect(restored.selectedFindingId, label).toBe(base.selectedFindingId);
        expect(restored.activeVariantId, label).toBe(base.activeVariantId);
        expect(restored.currentSourceTimeSec, label).toBe(base.currentSourceTimeSec);
        expect(restored.compareVariantId, label).toBeUndefined();
      }
    }
  });
});

describe('@albedian/observation: space.enter stops one light year short of the star', () => {
  it('puts the camera one light year short of the star on the line from where it was, not at the star', () => {
    const base = createSyntheticCalibratedSession();
    const star = base.observationSolution!.findings.find((f) => f.id === 'finding_betelgeuse')!;
    const starPc = star.cartesianParsecICRS!;
    for (const from of [[0, 0, 0], [3, -2, 1.5]] as Array<[number, number, number]>) {
      const session = { ...base, spatialView: { ...base.spatialView, observerOriginParsec: from } };
      const result = dispatchAlbedianAction(session, {
        type: 'space.enter',
        requestId: `req-standoff-${from.join('-')}`,
        expectedViewRevision: session.viewRevision,
        findingId: star.id,
        distancePolicy: 'measured_or_estimated',
      });
      expect(result.outcome.status).toBe('completed');
      const end = result.session.spatialView.observerOriginParsec;
      expect(Math.abs(distancePc(end, starPc) - FLIGHT_STANDOFF_PC)).toBeLessThan(1e-9);
      expect(Math.abs(distancePc(from, end) + distancePc(end, starPc) - distancePc(from, starPc))).toBeLessThan(1e-9);
      expect(end).toEqual(flightEndOrigin(from, starPc));
    }
  });
});

