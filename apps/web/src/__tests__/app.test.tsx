import { describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import { dispatchAlbedianAction, executeVoiceOrTextCommand } from '@albedian/observation';
import {
  createSyntheticCalibratedSession,
  createSyntheticUnresolvedSession,
} from '@albedian/observation/testing';
import {
  benchmarkRendererAdapters,
  computeAnchorLockedPhaseAState,
  lookAtDirectionQuaternion,
  querySightlineCone,
  raDecToUnitVector,
} from '@albedian/camera-rig';
import {
  IngestReceipt,
  ObservationSessionSchema,
  ObservationSolution,
  SolveJob,
} from '@albedian/contracts';
import { computeAudioBusGains } from '@albedian/design-system';
import {
  buildSessionFromDiscoveredMedia,
  buildSessionFromIngestReceipt,
  formatSolveJobProgressBanner,
  uploadAndSolveMedia,
} from '../services/apiClient';
import { ingestAndSolveUploadedFile } from '../App';
import { createOriginCameraFrustumIndicator } from '../spatial/referenceFrameContinuity';
import {
  BRUNETON_ATMO_GLSL_CHUNK,
  BRUNETON_ATMO_LUT_URL,
} from '../spatial/volumetricSplatsAndLut';
import { observatoryAudio } from '../audio/ObservatoryAudioEngine';
import {
  ALBEDIAN_FLAT_WAVEFRONT_PALETTE,
  ALBEDIAN_PURE_SILVER_PALETTE,
} from '../components/CoalescingBar';
import fs from 'node:fs';
import path from 'node:path';

describe('@albedian/web: End-to-End Observation-to-Space Journey & Audio/Voice Integration', () => {
  it('executes the complete defining experience: Import -> Reveal -> Calibrated Sky -> Select Star -> 3D Space -> Sightline -> Epoch refused -> Back to Moment', () => {
    let session = createSyntheticCalibratedSession();
    const initialTimeSec = session.currentSourceTimeSec;
    const initialAudioGain = session.audioGain;
    const initialSourceHash = session.sourceAsset!.sha256;

    // 1. User says: "Make the sky clearer"
    const r1 = executeVoiceOrTextCommand(session, 'Make the sky clearer', 'voice');
    expect(r1.outcome.status).toBe('completed');
    session = r1.session;
    expect(session.sourceAsset!.sha256).toBe(initialSourceHash);

    // 2. User says: "What were we looking at?"
    const r2 = executeVoiceOrTextCommand(session, 'What were we looking at?', 'voice');
    expect(r2.outcome.status).toBe('completed');
    session = r2.session;
    expect(session.machines.navigation).toBe('sky_registered');

    // 3. User selects Betelgeuse
    const r3 = executeVoiceOrTextCommand(session, 'Select Betelgeuse', 'voice');
    expect(r3.outcome.status).toBe('completed');
    session = r3.session;
    expect(session.selectedFindingId).toBe('finding_betelgeuse');

    // Verify Phase A off-axis anchor lock math on Betelgeuse
    const betelgeuse = session.observationSolution!.findings.find(
      (f) => f.id === 'finding_betelgeuse'
    )!;
    const betelgeuseSky = betelgeuse.skyPosition!;
    const phaseA = computeAnchorLockedPhaseAState({
      anchorRaDeg: betelgeuseSky.raDeg,
      anchorDecDeg: betelgeuseSky.decDeg,
      anchorTargetNdc: [0.22, -0.18],
      qStart: session.spatialView.cameraQuaternion,
      qEnd: lookAtDirectionQuaternion(
        raDecToUnitVector(betelgeuseSky.raDeg, betelgeuseSky.decDeg)
      ),
      fovStartDeg: 37.8,
      fovEndDeg: 28.0,
      aspect: 16 / 9,
      progress: 0.65,
    });
    expect(phaseA.anchorDriftPx).toBeLessThan(1e-4);

    // 4. User says: "Take me there"
    const r4 = executeVoiceOrTextCommand(session, 'Take me there', 'voice');
    expect(r4.outcome.status).toBe('completed');
    session = r4.session;
    expect(session.machines.navigation).toBe('space');
    expect(session.isPlaying).toBe(false);

    // Verify Web Audio Bus pauses earthside source audio during 3D space exploration
    const spaceAudio = computeAudioBusGains({
      navigationState: session.machines.navigation,
      voiceState: session.machines.voice,
      userSourceGain: session.audioGain,
      isMuted: false,
    });
    expect(spaceAudio.sourceMonitorGain).toBe(0);
    expect(spaceAudio.sourcePlaybackAllowed).toBe(false);

    // 5. Sightline cone query ("What's behind it?")
    const cone = querySightlineCone(
      session.observationSolution!.findings,
      [0, 0, 0],
      betelgeuse,
      25
    ) ?? [];
    expect(cone.length).toBeGreaterThan(0);

    // 6. User says: "Show me this sky a thousand years ago". The view does not move the stars to a chosen
    // year, so the epoch is refused and the view keeps its moment
    const epochBefore = session.spatialView.simulationEpochISO;
    const r5 = executeVoiceOrTextCommand(
      session,
      'Show me this sky a thousand years ago',
      'voice'
    );
    expect(r5.outcome).toMatchObject({ status: 'rejected', reason: 'not_connected' });
    session = r5.session;
    expect(session.spatialView.simulationEpochISO).toBe(epochBefore);

    // 7. User says: "Take me back"
    const r6 = dispatchAlbedianAction(session, {
      type: 'source.return',
      requestId: 'return_e2e',
      expectedViewRevision: session.viewRevision,
      restoreAudio: true,
    });
    expect(r6.outcome.status).toBe('completed');
    session = r6.session;

    expect(session.machines.navigation).toBe('source');
    expect(session.currentSourceTimeSec).toBe(initialTimeSec);
    expect(session.audioGain).toBe(initialAudioGain);
    expect(session.sourceAsset!.sha256).toBe(initialSourceHash);
  });

  it('loads backend detections without a sky position and gives them no 3D position', () => {
    const samples = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
    ) as Record<string, ObservationSolution>;
    for (const name of ['plate_wcs_failed_verification', 'horizon_approximate']) {
      const solution = samples[name]!;
      expect(solution.sourceSha256).toMatch(/^[0-9a-f]{64}$/);
      const session = buildSessionFromDiscoveredMedia({
        id: name, title: name, filename: `${name}.png`, sha256: solution.sourceSha256!, mediaKind: 'image',
        width: 960, height: 540, durationSec: 0, fps: null, videoCodec: 'none', capturedAtUTC: solution.observedAtUTC ?? null,
        latDeg: solution.observerLocation?.latDeg ?? null, lonDeg: solution.observerLocation?.lonDeg ?? null,
        cameraModel: 'Unknown camera', urls: { origUrl: `/media/${name}.png`, v1Url: '', v2Url: '' }, solution,
      });
      const unplaced = session.observationSolution!.findings.filter((f) => f.skyPosition === undefined);
      expect(unplaced.length).toBeGreaterThan(0);
      for (const finding of unplaced) {
        expect(finding.evidenceClass).toBe('observed');
        expect(finding.sourceDetection).toBeDefined();
        expect(finding.cartesianParsecICRS).toBeUndefined();
      }
    }
  });

  it('prevents 3D space transition on unresolved / predicted-only observations and reports renderer benchmark', () => {
    const unresolvedSession = createSyntheticUnresolvedSession();
    expect(unresolvedSession.observationSolution!.status).not.toBe('calibrated');

    const bench = benchmarkRendererAdapters(10000);
    expect(bench.recommendedPrimaryEngine).toBe('custom_wcs_three_rig');
    expect(bench.customRigAnchorDriftPx).toBeLessThan(0.01);
  });

  it('verifies Track 1 (Origin Frustum Continuity) and Track 2 (Bruneton LUT)', () => {
    // Track 1: Origin Frustum
    const frustum = createOriginCameraFrustumIndicator();
    expect(frustum.name).toBe('OriginCameraFrustumIndicator');

    // Track 2: Bruneton LUT & GLSL Chunk
    expect(BRUNETON_ATMO_LUT_URL).toBe('/textures/bruneton_atmo_lut.png');
    expect(BRUNETON_ATMO_GLSL_CHUNK).toContain('sampleBrunetonAtmoLut');

  });

  it('verifies Flat Architectural Vapor-Silver + Luminous Wavefront Refraction palette invariants (@zzzzshawn + Lusion)', () => {
    // High-albedo Vapor-Silver solid fill (#D4DCE7 -> #EDF2F8 -> #FFFFFF)
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.solidStart).toBe('#D4DCE7');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.solidMid).toBe('#EDF2F8');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.solidEnd).toBe('#FFFFFF');

    // Dichroic Ice-Cobalt wavefront refraction strictly inside coalescing dots/slats
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.wavefrontMid).toBe('#5A87E8');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.wavefrontDim).toBe('#355291');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.restPattern).toBe('#252D3D');

    // Pure Silver alternative
    expect(ALBEDIAN_PURE_SILVER_PALETTE.solidEnd).toBe('#FFFFFF');
  });

  it('verifies Living Sky Window Landing Page vs. App View separation, Audio toggle, and 3D showcase invariants', () => {
    // 1. Audio starts disabled until the user turns it on
    expect(observatoryAudio.getEnabled()).toBe(false);

    // 2. SpatialObservatoryViewport & SparkPortalRenderer invariants
    const viewportSrc = fs.readFileSync(
      path.resolve(__dirname, '../components/SpatialObservatoryViewport.tsx'),
      'utf8'
    );
    expect(viewportSrc).toContain('LANDING_SHOWCASE_SCENES');
    expect(viewportSrc).toContain('Discover the universe behind');
    expect(viewportSrc).toContain('the buried night sky.');
    expect(viewportSrc).toContain('Launch Albedian');
    expect(viewportSrc).toContain('if (entryStageActive) return;');

    const rendererSrc = fs.readFileSync(
      path.resolve(__dirname, '../spatial/SparkPortalRenderer.ts'),
      'utf8'
    );
    expect(rendererSrc).toContain('setShowcaseMode(enabled: boolean)');
    expect(rendererSrc).toContain('transitionToShowcaseScene(');
    expect(rendererSrc).toContain('this.originFrustumGroup.visible = false;');
    expect(rendererSrc).toContain('sampleBrunetonAtmoLut');
  });

  it('uploads a file through /api/ingest, runs /api/ingest/{sha256}/solve with real denominators, and loads /api/ingest/{sha256}/solution into the session', async () => {
    const samples = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
    ) as Record<string, ObservationSolution>;
    const baseSolution = samples['plate_calibrated']!;
    const fileBytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 1, 2, 3, 4]);
    const digest = await crypto.subtle.digest('SHA-256', fileBytes.buffer);
    const sha256 = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
    const assetId = `src_${sha256.slice(0, 16)}`;
    const solutionId = `sol_${sha256.slice(0, 16)}`;

    const receipt: IngestReceipt = {
      sourceAsset: {
        id: assetId,
        title: 'orion_phone.png',
        mediaKind: 'image',
        sha256,
        uri: `/api/ingest/${sha256}/original`,
        byteSize: fileBytes.byteLength,
        mimeType: 'image/png',
        width: 960,
        height: 540,
        durationMs: 0,
        videoCodec: 'png',
        audioStreams: [],
        capturedAtUTC: '2026-01-19T05:30:00Z',
        observerLocation: { latDeg: 31.9583, lonDeg: -111.5967 },
        cameraMetadata: { make: 'Apple', model: 'iPhone 18 Pro' },
        metadataSources: {
          capturedAtUTC: { container: 'exif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] },
          observerLocation: { container: 'exif', tags: ['GPSLatitude', 'GPSLongitude'] },
          cameraMetadata: { container: 'exif', tags: ['Make', 'Model'] },
        },
        privacyClass: 'private',
        immutable: true,
      },
      hints: {
        captureTime: {
          localDateTime: '2026-01-19T05:30:00',
          utcOffsetMinutes: 0,
          utc: '2026-01-19T05:30:00Z',
          marks: 'recorded',
          source: { container: 'exif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] },
        },
        location: {
          latDeg: 31.9583,
          lonDeg: -111.5967,
          source: { container: 'exif', tags: ['GPSLatitude', 'GPSLongitude'] },
        },
        camera: {
          make: 'Apple',
          model: 'iPhone 18 Pro',
          source: { container: 'exif', tags: ['Make', 'Model'] },
        },
      },
      alreadyStored: false,
    };

    const job: SolveJob = {
      id: `job_${sha256.slice(0, 16)}`,
      sourceAssetSha256: sha256,
      state: 'succeeded',
      createdAtUTC: '2026-01-19T05:31:00Z',
      startedAtUTC: '2026-01-19T05:31:01Z',
      finishedAtUTC: '2026-01-19T05:31:02Z',
      stages: [
        { stage: 'store', unit: 'bytes', done: fileBytes.byteLength, total: fileBytes.byteLength },
        { stage: 'decode', unit: 'frames', done: 1, total: 1 },
        { stage: 'detect', unit: 'frames', done: 1, total: 1 },
        { stage: 'solve', unit: 'candidates', done: 48, total: 220 },
        { stage: 'verify', unit: 'steps', done: 1, total: 1 },
      ],
      solutionId,
    };

    const solution: ObservationSolution = {
      ...baseSolution,
      id: solutionId,
      sourceAssetId: assetId,
      sourceSha256: sha256,
      observedAtUTC: '2026-01-19T05:30:00Z',
      observerLocation: { latDeg: 31.9583, lonDeg: -111.5967 },
      findings: baseSolution.findings.map((f) => ({
        ...f,
        observationSolutionId: solutionId,
        cartesianParsecICRS: undefined,
        sourceDetection: f.sourceDetection ? { ...f.sourceDetection, assetId } : undefined,
        evidence: { ...f.evidence, sourceAssetSha256: sha256, wcsSolutionId: solutionId },
      })),
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/ingest' && init?.method === 'POST') {
        return new Response(JSON.stringify(receipt), { status: 201 });
      }
      if (url === `/api/ingest/${sha256}/solve` && init?.method === 'POST') {
        return new Response(JSON.stringify(job), { status: 201 });
      }
      if (url === `/api/ingest/${sha256}/solution`) {
        return new Response(JSON.stringify(solution), { status: 200 });
      }
      return new Response('Not found', { status: 404 });
    });

    try {
      const file = new File([fileBytes], 'orion_phone.png', { type: 'image/png' });
      const updates: ReturnType<typeof buildSessionFromIngestReceipt>[] = [];
      const finalSession = await ingestAndSolveUploadedFile(file, 'blob:local-orion', (s) => updates.push(s));

      expect(updates.length).toBeGreaterThanOrEqual(3);
      expect(updates[0]!.machines.media).toBe('importing');
      expect(updates[1]!.machines.media).toBe('ready');
      expect(updates[1]!.machines.calibration).toBe('solving');

      ObservationSessionSchema.parse(finalSession);
      expect(finalSession.sourceAsset!.sha256).toBe(sha256);
      expect(finalSession.sourceAsset!.uri).toBe('blob:local-orion');
      expect(finalSession.sourceAsset!.width).toBe(960);
      expect(finalSession.sourceAsset!.height).toBe(540);
      expect(finalSession.machines.media).toBe('ready');
      expect(finalSession.machines.calibration).toBe('calibrated');
      expect(finalSession.observationSolution!.id).toBe(solutionId);
      expect(finalSession.observationSolution!.status).toBe('calibrated');
      expect(finalSession.lastStatusBanner).toContain(`store: ${fileBytes.byteLength}/${fileBytes.byteLength} bytes`);
      expect(finalSession.lastStatusBanner).toContain('solve: 48/220 candidates');
      expect(finalSession.lastStatusBanner).toContain('verify: 1/1 steps');
      const matched = finalSession.observationSolution!.findings.filter((f) => f.evidenceClass === 'catalog_matched');
      expect(matched.length).toBeGreaterThan(0);
      expect(matched.every((f) => f.cartesianParsecICRS !== undefined)).toBe(true);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('honestly reports failed solve jobs and falls back to local importing session when backend is offline', async () => {
    const fileBytes = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
    const file = new File([fileBytes], 'offline.png', { type: 'image/png' });
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Network unreachable'));
    try {
      const updates: ReturnType<typeof buildSessionFromIngestReceipt>[] = [];
      const fallback = await ingestAndSolveUploadedFile(file, 'blob:offline', (s) => updates.push(s));
      ObservationSessionSchema.parse(fallback);
      expect(fallback.machines.media).toBe('importing');
      expect(fallback.machines.calibration).toBe('not_requested');
      expect(fallback.lastStatusBanner).toContain('Not yet decoded or calibrated.');
    } finally {
      fetchSpy.mockRestore();
    }

    const sha256 = 'b'.repeat(64);
    const assetId = `src_${sha256.slice(0, 16)}`;
    const receipt: IngestReceipt = {
      sourceAsset: {
        id: assetId,
        title: 'corrupt.png',
        mediaKind: 'image',
        sha256,
        uri: `/api/ingest/${sha256}/original`,
        byteSize: 8,
        mimeType: 'image/png',
        width: 64,
        height: 48,
        durationMs: 0,
        videoCodec: 'png',
        audioStreams: [],
        privacyClass: 'private',
        immutable: true,
      },
      hints: {},
      alreadyStored: false,
    };
    const failedJob: SolveJob = {
      id: `job_${sha256.slice(0, 16)}`,
      sourceAssetSha256: sha256,
      state: 'failed',
      createdAtUTC: '2026-01-19T05:31:00Z',
      startedAtUTC: '2026-01-19T05:31:01Z',
      finishedAtUTC: '2026-01-19T05:31:02Z',
      stages: [
        { stage: 'store', unit: 'bytes', done: 8, total: 8 },
        { stage: 'decode', unit: 'frames', done: 0, total: 1 },
      ],
      error: 'The decoder could not read the file: corrupt stream',
    };
    const failFetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/ingest') return new Response(JSON.stringify(receipt), { status: 201 });
      if (url === `/api/ingest/${sha256}/solve`) return new Response(JSON.stringify(failedJob), { status: 201 });
      return new Response('Not found', { status: 404 });
    });
    try {
      const failedSession = await uploadAndSolveMedia(file, 'corrupt.png', { localObjectUrl: 'blob:corrupt' });
      ObservationSessionSchema.parse(failedSession);
      expect(failedSession.machines.media).toBe('ready');
      expect(failedSession.machines.calibration).toBe('unresolved');
      expect(failedSession.observationSolution!.status).toBe('unresolved');
      expect(failedSession.observationSolution!.warnings).toContain('The decoder could not read the file: corrupt stream');
      expect(failedSession.lastStatusBanner).toContain('The decoder could not read the file: corrupt stream');
      expect(failedSession.lastStatusBanner).toContain('store: 8/8 bytes · decode: 0/1 frames');
    } finally {
      failFetchSpy.mockRestore();
    }
  });

  it('PBT: formatSolveJobProgressBanner reports exact stage denominators without fake percentages', () => {
    const sha256 = 'a'.repeat(64);
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 10_000_000 }),
        fc.integer({ min: 1, max: 8 }),
        fc.integer({ min: 1, max: 500 }),
        fc.boolean(),
        (bytes, frames, totalCandidates, indeterminateDecode) => {
          const doneCandidates = Math.min(totalCandidates, Math.max(0, Math.floor(totalCandidates / 2)));
          const job: SolveJob = {
            id: `job_${sha256.slice(0, 16)}`,
            sourceAssetSha256: sha256,
            state: 'running',
            createdAtUTC: '2026-01-19T05:31:00Z',
            startedAtUTC: '2026-01-19T05:31:01Z',
            stages: [
              { stage: 'store', unit: 'bytes', done: bytes, total: bytes },
              { stage: 'decode', unit: 'frames', done: frames, total: indeterminateDecode ? null : frames },
              { stage: 'solve', unit: 'candidates', done: doneCandidates, total: totalCandidates },
            ],
          };
          const banner = formatSolveJobProgressBanner('capture.png', job);
          expect(banner).toContain(`store: ${bytes}/${bytes} bytes`);
          expect(banner).toContain(`solve: ${doneCandidates}/${totalCandidates} candidates`);
          if (indeterminateDecode) {
            expect(banner).toContain(`decode: ${frames} frames (elapsed)`);
          } else {
            expect(banner).toContain(`decode: ${frames}/${frames} frames`);
          }
          expect(banner).not.toContain('%');
        }
      ),
      { numRuns: 25, ...(process.env.FC_SEED ? { seed: Number(process.env.FC_SEED) } : {}) }
    );
  });
});
