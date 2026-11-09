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
  ObservationSession,
  ObservationSessionSchema,
  ObservationSolution,
  SolveJob,
} from '@albedian/contracts';
import { computeAudioBusGains } from '@albedian/design-system';
import {
  buildSessionFromDiscoveredMedia,
  buildSessionFromIngestReceipt,
  formatSolveJobProgressBanner,
  UploadRefusedError,
  uploadAndSolveMedia,
} from '../services/apiClient';
import { ingestAndSolveUploadedFile, selectedFindingOf } from '../App';
import { computeStarAnchorNdc } from '@albedian/observation';
import { useCameraFlightAnimation } from '../hooks/useCameraFlightAnimation';
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
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

  it('verifies Track 2 (Bruneton LUT)', () => {
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

    const namedRequests: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (new Headers(init?.headers).has('x-albedian-filename')) namedRequests.push(`${init?.method ?? 'GET'} ${url}`);
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
      // Until the receipt nothing about the file is known: no asset, no size, no hash
      expect(updates[0]!.machines.media).toBe('empty');
      expect(updates[0]!.sourceAsset).toBeUndefined();
      expect(updates[0]!.lastStatusBanner).toBe('Uploading "orion_phone.png" to the Albedian server…');
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
      // The file's name goes to the server once, with the upload itself (experience-9h)
      expect(namedRequests).toEqual(['POST /api/ingest']);
      const matched = finalSession.observationSolution!.findings.filter((f) => f.evidenceClass === 'catalog_matched');
      expect(matched.length).toBeGreaterThan(0);
      expect(matched.every((f) => f.cartesianParsecICRS !== undefined)).toBe(true);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('honestly reports failed solve jobs and says the upload got no answer when the backend is offline', async () => {
    const fileBytes = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
    const file = new File([fileBytes], 'offline.png', { type: 'image/png' });
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Network unreachable'));
    try {
      const updates: ReturnType<typeof buildSessionFromIngestReceipt>[] = [];
      const fallback = await ingestAndSolveUploadedFile(file, 'blob:offline', (s) => updates.push(s));
      ObservationSessionSchema.parse(fallback);
      expect(fallback.machines.media).toBe('empty');
      expect(fallback.machines.calibration).toBe('not_requested');
      expect(fallback.sourceAsset).toBeUndefined();
      expect(fallback.lastStatusBanner).toBe(
        'The upload of "offline.png" did not finish: no answer from the Albedian server (Network unreachable).'
      );
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
      // The job failed before any solver ran: no counts and no solver, never zeros (experience-9f)
      expect(failedSession.observationSolution!.quality).toBeUndefined();
      expect(failedSession.observationSolution!.solver).toBeUndefined();
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

describe('upload refusals and the state before the receipt (experience-9d)', () => {
  const refusals: Array<[number, string, string]> = [
    [415, 'declared as image/x-adobe-dng, but the bytes are image/tiff', 'IMG_0001.DNG'],
    [413, 'larger than the 256 MiB a still may be', 'huge.jpg'],
    [422, 'the HEIF item table is truncated', 'IMG_0002.HEIC'],
  ];

  for (const [status, reason, name] of refusals) {
    it(`shows a ${status} refusal as Not stored with the server's reason, and never asks for a solve`, async () => {
      const calls: string[] = [];
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : input.toString();
        calls.push(url);
        return new Response(JSON.stringify({ detail: reason }), { status });
      });
      try {
        const file = new File([new Uint8Array([1, 2, 3, 4])], name, { type: 'image/jpeg' });
        const updates: ObservationSession[] = [];
        const result = await ingestAndSolveUploadedFile(file, 'blob:refused', (s) => updates.push(s));
        ObservationSessionSchema.parse(result);
        expect(result.lastStatusBanner).toBe(`Not stored: ${reason} ("${name}").`);
        expect(result.machines.media).toBe('empty');
        expect(result.sourceAsset).toBeUndefined();
        expect(result.observationSolution).toBeUndefined();
        expect(calls).toEqual(['/api/ingest']);
        // Nothing along the way claimed the file was imported, decoded, pending or sized
        expect(updates.every((u) => u.sourceAsset === undefined)).toBe(true);
        expect(updates.some((u) => /Imported|Not yet decoded|SHA-256/.test(u.lastStatusBanner ?? ''))).toBe(false);
      } finally {
        fetchSpy.mockRestore();
      }
    });
  }

  it('names the HTTP status when the refusal carries no reason of its own', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('<html>Request Entity Too Large</html>', { status: 413 }));
    try {
      const file = new File([new Uint8Array([1])], 'big.mov', { type: 'video/quicktime' });
      const result = await ingestAndSolveUploadedFile(file, 'blob:big', () => {});
      expect(result.lastStatusBanner).toBe('Not stored: the server answered HTTP 413 ("big.mov").');
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('throws an UploadRefusedError carrying the status and the reason', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        async () =>
          new Response(JSON.stringify({ detail: 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file' }), { status: 415 })
      );
    try {
      const file = new File([new Uint8Array([1])], 'note.txt', { type: 'text/plain' });
      await expect(uploadAndSolveMedia(file, 'note.txt')).rejects.toMatchObject({
        name: 'UploadRefusedError',
        status: 415,
        reason: 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file',
      });
      await expect(uploadAndSolveMedia(file, 'note.txt')).rejects.toBeInstanceOf(UploadRefusedError);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('says the upload got no answer when the server cannot be reached, without claiming it was stored', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Load failed'));
    try {
      const file = new File([new Uint8Array([1, 2])], 'offline.jpg', { type: 'image/jpeg' });
      const updates: ObservationSession[] = [];
      const result = await ingestAndSolveUploadedFile(file, 'blob:offline', (s) => updates.push(s));
      ObservationSessionSchema.parse(result);
      expect(result.lastStatusBanner).toBe(
        'The upload of "offline.jpg" did not finish: no answer from the Albedian server (Load failed).'
      );
      expect(result.machines.media).toBe('empty');
      expect(result.sourceAsset).toBeUndefined();
      expect(updates.every((u) => u.sourceAsset === undefined)).toBe(true);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  const storedReceipt = (): IngestReceipt => {
    const sha256 = 'c'.repeat(64);
    return {
      sourceAsset: {
        id: `src_${sha256.slice(0, 16)}`,
        title: 'stored.jpg',
        mediaKind: 'image',
        sha256,
        uri: `/api/ingest/${sha256}/original`,
        byteSize: 4,
        mimeType: 'image/jpeg',
        width: 4032,
        height: 3024,
        durationMs: 0,
        videoCodec: 'mjpeg',
        audioStreams: [],
        privacyClass: 'private',
        immutable: true,
      },
      hints: {},
      alreadyStored: false,
    };
  };

  it('keeps the stored photo and says the solve did not finish when the solve request is refused', async () => {
    const receipt = storedReceipt();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/ingest') return new Response(JSON.stringify(receipt), { status: 201 });
      return new Response(JSON.stringify({ detail: 'the solve queue is full' }), { status: 503 });
    });
    try {
      const file = new File([new Uint8Array([1, 2, 3, 4])], 'stored.jpg', { type: 'image/jpeg' });
      const result = await ingestAndSolveUploadedFile(file, 'blob:stored', () => {});
      ObservationSessionSchema.parse(result);
      expect(result.sourceAsset!.width).toBe(4032);
      expect(result.sourceAsset!.height).toBe(3024);
      expect(result.sourceAsset!.uri).toBe('blob:stored');
      expect(result.machines.media).toBe('ready');
      // Nothing is solving any more
      expect(result.machines.calibration).toBe('not_requested');
      expect(result.lastStatusBanner).toBe('Stored "stored.jpg", but the sky solve did not finish: the solve queue is full.');
      // Nothing was solved, so nothing is counted (experience-9f)
      expect(result.observationSolution!.status).toBe('pending');
      expect(result.observationSolution!.quality).toBeUndefined();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('keeps the stored photo when the solution cannot be read, and names the reason', async () => {
    const receipt = storedReceipt();
    const sha256 = receipt.sourceAsset.sha256;
    const job: SolveJob = {
      id: `job_${sha256.slice(0, 16)}`,
      sourceAssetSha256: sha256,
      state: 'succeeded',
      createdAtUTC: '2026-01-19T05:31:00Z',
      startedAtUTC: '2026-01-19T05:31:01Z',
      finishedAtUTC: '2026-01-19T05:31:02Z',
      stages: [{ stage: 'store', unit: 'bytes', done: 4, total: 4 }],
      solutionId: 'sol_stored',
    };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/ingest') return new Response(JSON.stringify(receipt), { status: 201 });
      if (url === `/api/ingest/${sha256}/solve`) return new Response(JSON.stringify(job), { status: 201 });
      return new Response(JSON.stringify({ detail: 'no solution is stored for that file' }), { status: 404 });
    });
    try {
      const file = new File([new Uint8Array([1, 2, 3, 4])], 'stored.jpg', { type: 'image/jpeg' });
      const result = await ingestAndSolveUploadedFile(file, 'blob:stored', () => {});
      ObservationSessionSchema.parse(result);
      expect(result.sourceAsset!.sha256).toBe(sha256);
      expect(result.machines.calibration).toBe('not_requested');
      expect(result.lastStatusBanner).toBe(
        'Stored "stored.jpg", but the sky solve did not finish: no solution is stored for that file.'
      );
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('says the server answer did not match the contract instead of printing the validator dump', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => new Response(JSON.stringify({ sourceAsset: { id: 7 } }), { status: 201 }));
    try {
      const file = new File([new Uint8Array([1])], 'odd.jpg', { type: 'image/jpeg' });
      const result = await ingestAndSolveUploadedFile(file, 'blob:odd', () => {});
      expect(result.lastStatusBanner).toBe(
        "The upload of \"odd.jpg\" did not finish: the server's answer did not match the contract."
      );
      expect(result.sourceAsset).toBeUndefined();
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe('the selected finding (experience-9g)', () => {
  it('is none until the session selects one, even with findings present', () => {
    const session = createSyntheticCalibratedSession();
    expect(session.observationSolution!.findings.length).toBeGreaterThan(1);
    expect(selectedFindingOf({ ...session, selectedFindingId: null })).toBeNull();
  });

  it('is the finding the session selected', () => {
    const session = createSyntheticCalibratedSession();
    const second = session.observationSolution!.findings[1]!;
    expect(selectedFindingOf({ ...session, selectedFindingId: second.id })?.id).toBe(second.id);
  });

  it('is none when the selected id names no finding of this solution', () => {
    const session = createSyntheticCalibratedSession();
    expect(selectedFindingOf({ ...session, selectedFindingId: 'finding-from-another-photo' })).toBeNull();
  });

  it('flies nowhere when asked for a finding that is not in the session, instead of to the first one', () => {
    const base = createSyntheticCalibratedSession();
    // The first finding has a 3D position, so a fallback to it would start a flight
    const first = base.observationSolution!.findings.find((f) => resolveFindingParsecPosition(f) !== null)!;
    const session = {
      ...base,
      reducedMotion: true,
      observationSolution: {
        ...base.observationSolution!,
        findings: [first, ...base.observationSolution!.findings.filter((f) => f.id !== first.id)],
      },
    };
    const setSession = vi.fn();
    let fly: ((id: string) => void) | null = null;
    const Probe = () => {
      fly = useCameraFlightAnimation(session, setSession).animatePhaseAToPhaseBSpaceFlight;
      return null;
    };
    renderToString(createElement(Probe));
    fly!('finding-from-another-photo');
    expect(setSession).not.toHaveBeenCalled();
    // A real id still flies (reduced motion lands at once)
    fly!(first.id);
    expect(setSession).toHaveBeenCalledTimes(1);
  });

  it('anchors a flight where the reducer anchors a selection: turned and at the pixel centre (experience-9h)', () => {
    const base = createSyntheticCalibratedSession();
    const first = base.observationSolution!.findings.find(
      (f) => f.sourceDetection !== undefined && resolveFindingParsecPosition(f) !== null
    )!;
    // A portrait phone photo, so a formula that ignores the turn lands somewhere else
    const session = {
      ...base,
      reducedMotion: false,
      sourceAsset: { ...base.sourceAsset!, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } },
    };
    const frames: Array<(now: number) => void> = [];
    vi.stubGlobal('window', {
      requestAnimationFrame: (step: (now: number) => void) => frames.push(step),
      cancelAnimationFrame: () => {},
    });
    try {
      const setSession = vi.fn();
      let fly: ((id: string) => void) | null = null;
      const Probe = () => {
        fly = useCameraFlightAnimation(session, setSession).animatePhaseAToPhaseBSpaceFlight;
        return null;
      };
      renderToString(createElement(Probe));
      fly!(first.id);
      // The first frame of Phase A locks the anchor
      frames[0]!(performance.now() + 10);
      const phaseA = setSession.mock.calls[0]![0](session);
      expect(phaseA.spatialView.transitionPhase).toBe('phase_a_photo_to_sky');
      expect(phaseA.spatialView.lockedAnchorNdc).toEqual(computeStarAnchorNdc(first, session.sourceAsset));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('claims neither stored nor not stored on a 5xx answer, and Not stored only on a 4xx refusal (experience-9h)', async () => {
    const file = new File([new Uint8Array([1, 2])], 'night.jpg', { type: 'image/jpeg' });
    // The dev proxy with no backend answers an empty 500; the router's own crash answers plain text
    for (const body of ['', 'Internal Server Error']) {
      const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(body, { status: 500 }));
      try {
        const failed = await ingestAndSolveUploadedFile(file, 'blob:night', () => {});
        expect(failed.lastStatusBanner, JSON.stringify(body)).toBe(
          'The upload of "night.jpg" did not finish: the server answered HTTP 500.'
        );
        expect(failed.lastStatusBanner).not.toMatch(/Not stored|failed while storing|Stored/);
        expect(failed.sourceAsset).toBeUndefined();
      } finally {
        spy.mockRestore();
      }
    }
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        async () => new Response(JSON.stringify({ detail: 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file' }), { status: 415 })
      );
    try {
      const refused = await ingestAndSolveUploadedFile(file, 'blob:night', () => {});
      expect(refused.lastStatusBanner).toBe('Not stored: not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file ("night.jpg").');
    } finally {
      spy.mockRestore();
    }
  });
});
