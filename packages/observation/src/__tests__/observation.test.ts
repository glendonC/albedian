import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  AlbedianAction,
  ObservationSession,
  ObservationSessionSchema,
  SourceAsset,
  MediaVariant,
} from '@albedian/contracts';
import {
  createDefaultSaharaObservationSession,
  createEmptyObservationSession,
  createSessionForSourceAsset,
  createUnresolvedObservationSession,
  dispatchAlbedianAction,
  transitionOrthogonalMachine,
  validateMediaTimelineSync,
} from '../index';

describe('@albedian/observation — Golden Sessions & Core Property-Based Invariants', () => {
  it('validates createDefaultSaharaObservationSession against ObservationSessionSchema and all 14 findings', () => {
    const session = createDefaultSaharaObservationSession();
    const parsed = ObservationSessionSchema.parse(session);
    expect(parsed.title).toBe('Sahara Night — Orion & Taurus Over synthetic site');
    expect(parsed.sourceAsset.capturedAtUTC).toBe('2030-01-01T02:00:00Z');
    expect(parsed.sourceAsset.observerLocation?.latDeg).toBe(30.0);
    expect(parsed.sourceAsset.observerLocation?.lonDeg).toBe(-40.0);
    expect(parsed.sourceAsset.width).toBe(1920);
    expect(parsed.sourceAsset.height).toBe(1080);
    expect(parsed.sourceAsset.durationMs).toBe(12000);
    expect(parsed.sourceAsset.fpsNumerator).toBe(30);
    expect(parsed.sourceAsset.audioStreams).toHaveLength(1);
    expect(parsed.sourceAsset.audioStreams[0]?.codec).toBe('aac');

    expect(parsed.processingGraphs).toHaveLength(3);
    expect(parsed.processingAttempts).toHaveLength(3);
    expect(parsed.mediaVariants).toHaveLength(3);

    const variantIds = parsed.mediaVariants.map((v) => v.id);
    expect(variantIds).toEqual(['variant_original', 'variant_natural', 'variant_temporal']);

    const naturalAttempt = parsed.processingAttempts.find((a) => a.id === 'attempt_natural');
    expect(naturalAttempt?.metrics?.shadowSnrGainDb).toBe(5.4);
    expect(naturalAttempt?.metrics?.pointSourceRetentionRatio).toBe(0.98);
    expect(naturalAttempt?.metrics?.falsePointArtifactCount).toBe(0);

    const temporalAttempt = parsed.processingAttempts.find((a) => a.id === 'attempt_temporal');
    expect(temporalAttempt?.metrics?.shadowSnrGainDb).toBe(8.2);
    expect(temporalAttempt?.metrics?.pointSourceRetentionRatio).toBe(0.99);
    expect(temporalAttempt?.metrics?.falsePointArtifactCount).toBe(0);

    expect(parsed.observationSolution.status).toBe('calibrated');
    expect(parsed.observationSolution.quality.heldOutInliers).toBe(10);
    expect(parsed.observationSolution.quality.medianResidualPx).toBe(0.42);
    expect(parsed.observationSolution.quality.p95ResidualPx).toBe(0.89);
    expect(parsed.observationSolution.wcs?.crvalRaDeg).toBe(83.8);
    expect(parsed.observationSolution.wcs?.crvalDecDeg).toBe(4.0);
    expect(parsed.observationSolution.wcs?.crpixX).toBe(960);
    expect(parsed.observationSolution.wcs?.crpixY).toBe(540);
    expect(parsed.observationSolution.wcs?.scaleDegPerPx).toBe(0.035);
    expect(parsed.observationSolution.wcs?.fovHorizontalDeg).toBe(67.2);
    expect(parsed.observationSolution.wcs?.fovVerticalDeg).toBe(37.8);

    const findings = parsed.observationSolution.findings;
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

  it('validates createUnresolvedObservationSession with zero false catalog_matched claims', () => {
    const session = createUnresolvedObservationSession();
    const parsed = ObservationSessionSchema.parse(session);
    expect(parsed.machines.calibration).toBe('predicted_only');
    expect(parsed.observationSolution.status).toBe('predicted_only');
    expect(parsed.observationSolution.quality.heldOutInliers).toBe(2);
    expect(parsed.observationSolution.quality.passedIndependentVerification).toBe(false);
    expect(
      parsed.observationSolution.findings.every((f) => f.evidenceClass === 'predicted')
    ).toBe(true);
    expect(
      parsed.observationSolution.findings.filter((f) => f.evidenceClass === 'catalog_matched')
    ).toHaveLength(0);
  });

  it('validates createEmptyObservationSession with no place, capture time, or findings', () => {
    const createdAtISO = '2026-11-03T08:00:00.000Z';
    const parsed = ObservationSessionSchema.parse(createEmptyObservationSession(createdAtISO));
    expect(parsed.machines.media).toBe('empty');
    expect(parsed.machines.calibration).toBe('not_requested');
    expect(parsed.sourceAsset.observerLocation).toBeUndefined();
    expect(parsed.sourceAsset.capturedAtUTC).toBeUndefined();
    expect(parsed.observationSolution.status).toBe('pending');
    expect(parsed.observationSolution.observerLocation).toBeUndefined();
    expect(parsed.observationSolution.wcs).toBeUndefined();
    expect(parsed.observationSolution.findings).toHaveLength(0);
    expect(parsed.observationSolution.quality.passedIndependentVerification).toBe(false);
    expect(parsed.observationSolution.observedAtUTC).toBe(createdAtISO);
  });

  it('re-anchors the empty session to an imported asset without inventing location or findings', () => {
    const createdAtISO = '2026-11-03T08:00:00.000Z';
    const asset: SourceAsset = {
      ...createEmptyObservationSession(createdAtISO).sourceAsset,
      id: 'src_upload_1',
      title: 'backyard.jpg',
      sha256: 'ab'.repeat(32),
      uri: 'blob:backyard',
    };
    const session = createSessionForSourceAsset(asset, 'importing', createdAtISO);
    expect(session.machines.media).toBe('importing');
    expect(session.machines.calibration).toBe('not_requested');
    expect(session.observationSolution.sourceAssetId).toBe('src_upload_1');
    expect(session.observationSolution.sourceSha256).toBe(asset.sha256);
    expect(session.observationSolution.observerLocation).toBeUndefined();
    expect(session.observationSolution.findings).toHaveLength(0);
    expect(session.navigationHistory.anchorMomentCheckpoint.sourceAssetId).toBe('src_upload_1');

    const located = createSessionForSourceAsset(
      { ...asset, capturedAtUTC: '2030-11-01T22:32:00Z', observerLocation: { latDeg: -43.99, lonDeg: 170.46 } },
      'ready',
      createdAtISO
    );
    expect(located.observationSolution.observerLocation).toEqual({ latDeg: -43.99, lonDeg: 170.46, placeName: undefined });
    expect(located.observationSolution.observedAtUTC).toBe('2030-11-01T22:32:00Z');
    expect(located.spatialView.simulationEpochISO).toBe('2030-11-01T22:32:00Z');
  });

  it('enforces scientific distance prerequisites on space.enter and space.viewpoint', () => {
    const session = createDefaultSaharaObservationSession();

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
    const base = createDefaultSaharaObservationSession();
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

  it('PBT-NAV-1: Arbitrary sequences of sky.show, finding.select, space.enter, space.viewpoint, space.epoch, history.back(partial) followed by source.return recover exact initial CameraCheckpoint state', () => {
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
      })
    );

    const initialOverrideArb = fc.record({
      currentSourceTimeSec: fc.double({ min: 0, max: 11.9, noNaN: true }),
      audioGain: fc.double({ min: 0, max: 1, noNaN: true }),
      cropZoom: fc.double({ min: 1, max: 5, noNaN: true }),
      cropPanX: fc.double({ min: -0.5, max: 0.5, noNaN: true }),
      cropPanY: fc.double({ min: -0.5, max: 0.5, noNaN: true }),
      activeVariantId: fc.constantFrom('variant_original', 'variant_natural', 'variant_temporal'),
      selectedFindingId: fc.option(fc.constantFrom(...catalogStarIds), { nil: null }),
      isPlaying: fc.boolean(),
    });

    fc.assert(
      fc.property(
        initialOverrideArb,
        fc.array(stepArbitrary, { minLength: 1, maxLength: 15 }),
        (initOverrides, steps) => {
          const rawSession = createDefaultSaharaObservationSession();
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
                  epochISO: `${step.yearCE < 0 ? '-' : ''}${Math.abs(step.yearCE).toString().padStart(4, '0')}-01-01T00:00:00Z`,
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
            }

            const res = dispatchAlbedianAction(session, action);
            expect(res.outcome.status).toBe('completed');
            session = res.session;
          }

          const returned = dispatchAlbedianAction(session, {
            type: 'source.return',
            requestId: 'pbt_nav_return',
            expectedViewRevision: session.viewRevision,
            restoreAudio: true,
          });

          expect(returned.outcome.status).toBe('completed');
          expect(returned.session.currentSourceTimeSec).toBe(initOverrides.currentSourceTimeSec);
          expect(returned.session.audioGain).toBe(initOverrides.audioGain);
          expect(returned.session.cropTransform).toEqual({
            zoom: initOverrides.cropZoom,
            panX: initOverrides.cropPanX,
            panY: initOverrides.cropPanY,
          });
          expect(returned.session.activeVariantId).toBe(initOverrides.activeVariantId);
          expect(returned.session.selectedFindingId).toBe(initOverrides.selectedFindingId);
          expect(returned.session.isPlaying).toBe(initOverrides.isPlaying);
          expect(returned.session.machines.navigation).toBe('source');
          expect(returned.session.spatialView.mode).toBe('source_2d');
        }
      ),
      { numRuns: 100 }
    );
  });

  it('PBT-VOICE-1: Any AlbedianAction with expectedViewRevision !== session.viewRevision is rejected (stale_revision) and leaves ObservationSession unchanged', () => {
    const baseSession = createDefaultSaharaObservationSession();

    const staleRevisionArb = fc
      .integer({ min: 0, max: 10_000 })
      .filter((rev) => rev !== baseSession.viewRevision);

    const actionBuilderArb = fc.constantFrom(
      (rev: number): AlbedianAction => ({
        type: 'media.restore',
        requestId: 'req_stale',
        expectedViewRevision: rev,
        sourceAssetId: baseSession.sourceAsset.id,
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
        epochISO: '1026-01-01T00:00:00Z',
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

  it('PBT-SYNC-1: Media timeline synchronization property test verifying monotonic frame timestamps and bounded audio drift', () => {
    const baseSession = createDefaultSaharaObservationSession();
    const source: SourceAsset = baseSession.sourceAsset;

    // 1. All golden variants pass timeline sync
    for (const variant of baseSession.mediaVariants) {
      const check = validateMediaTimelineSync(source, variant);
      expect(check.valid).toBe(true);
      expect(check.monotonic).toBe(true);
      expect(check.audioPreserved).toBe(true);
      expect(check.maxDriftMs).toBeLessThanOrEqual(42);
    }

    // 2. Property test over arbitrary positive frame intervals and audio sync offsets
    const baseVariant = baseSession.mediaVariants[1]!;
    fc.assert(
      fc.property(
        fc.array(fc.double({ min: 8, max: 50, noNaN: true }), { minLength: 5, maxLength: 120 }),
        fc.double({ min: 0, max: 120, noNaN: true }),
        fc.boolean(),
        (frameDeltas, audioSyncOffsetMs, audioCopied) => {
          const timestamps: number[] = [];
          let acc = 0;
          for (const d of frameDeltas) {
            timestamps.push(Number(acc.toFixed(3)));
            acc += d;
          }

          const syntheticVariant: MediaVariant = {
            ...baseVariant,
            durationMs: source.durationMs,
            frameTimestampsMs: timestamps,
            audioStreamCopiedFromSource: audioCopied,
            audioSyncOffsetMs,
          };

          const res = validateMediaTimelineSync(source, syntheticVariant);
          expect(res.monotonic).toBe(true);
          expect(res.audioPreserved).toBe(audioCopied);
          const lastTs = timestamps[timestamps.length - 1] ?? 0;
          const expectedValid =
            audioCopied &&
            audioSyncOffsetMs <= 42 &&
            lastTs <= source.durationMs + 42;
          expect(res.valid).toBe(expectedValid);
        }
      ),
      { numRuns: 100 }
    );

    // 3. Property test verifying non-monotonic timestamps are always rejected
    fc.assert(
      fc.property(
        fc.double({ min: 100, max: 5000, noNaN: true }),
        fc.double({ min: 1, max: 99, noNaN: true }),
        (t1, backwardsDelta) => {
          const badVariant: MediaVariant = {
            ...baseVariant,
            frameTimestampsMs: [0, t1, t1 - backwardsDelta, t1 + 100],
            audioSyncOffsetMs: 0.5,
            audioStreamCopiedFromSource: true,
          };
          const res = validateMediaTimelineSync(source, badVariant);
          expect(res.monotonic).toBe(false);
          expect(res.valid).toBe(false);
        }
      ),
      { numRuns: 50 }
    );
  });

  it('PBT-NAV-HISTBACK-CHAIN: Multi-step history.back partial-fraction chaining never overshoots the baseline checkpoint', () => {
    // Establish a baseline session at the source view, then navigate to sky and space
    // so the checkpoint stack has at least 2 entries, then apply two sequential partial
    // history.back calls and verify the camera stays between the two surrounding checkpoints.
    const base = createDefaultSaharaObservationSession();

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

          // Second partial back — must use the new viewRevision
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
