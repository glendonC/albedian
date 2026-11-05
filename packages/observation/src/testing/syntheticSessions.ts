import {
  CameraCheckpoint,
  CatalogReference,
  CelestialFinding,
  MediaVariant,
  ObservationSession,
  ObservationSessionSchema,
  ProcessingAttempt,
  ProcessingGraph,
  SourceAsset,
  SpatialViewState,
  computeNodeCacheKeys,
  deterministicHexDigest,
  validateProcessingGraphDAG,
} from '@albedian/contracts';
import { computeCartesianParsecICRS } from '../checkpoints.js';

/**
 * Synthetic observation sessions for tests, imported as `@albedian/observation/testing`.
 * The package entry point does not export them.
 *
 * Every value here is test data: no clip, catalog release, ephemeris, detection or plate solve
 * stands behind any of it, and each evidence record says so. The place and moments point at
 * nobody: the open North Atlantic at 30 N, 40 W (about 1,321 km from Flores in the Azores), in 2030.
 * Each hash is the SHA-256 of the UTF-8 label beside it, standing in for media bytes that do not exist.
 */

const SYNTHETIC_PLACE = { latDeg: 30.0, lonDeg: -40.0, placeName: 'Synthetic place, open North Atlantic' };
// Test data names its source as synthetic: every capture time and place a SourceAsset carries has one
const SYNTHETIC_METADATA_SOURCE = { container: 'synthetic', tags: ['test-fixture'] } as const;
const SYNTHETIC_METADATA_SOURCES = {
  capturedAtUTC: { ...SYNTHETIC_METADATA_SOURCE, tags: [...SYNTHETIC_METADATA_SOURCE.tags] },
  observerLocation: { ...SYNTHETIC_METADATA_SOURCE, tags: [...SYNTHETIC_METADATA_SOURCE.tags] },
};

type StarRow = [
  id: string,
  name: string,
  bayer: string,
  hip: string,
  constellation: string,
  ra: number,
  dec: number,
  mag: number,
  bpRp: number,
  spec: string,
  pmRa: number,
  pmDec: number,
  rv: number,
  plx: number,
  plxErr: number,
  xPx: number,
  yPx: number,
  snr: number,
  flux: number,
  resPx: number,
];

// Test rows keyed by Hipparcos numbers; the values are typed test data, not read from a catalog release
const TEST_STARS: StarRow[] = [
  ['finding_betelgeuse', 'Betelgeuse', 'Alpha Orionis', 'HIP 27989', 'Orion', 88.7929, 7.4071, 0.42, 2.05, 'M1-M2 Ia-ab', 27.54, 11.3, 21.91, 5.95, 0.45, 817.3, 442.6, 84.2, 18420, 0.34],
  ['finding_rigel', 'Rigel', 'Beta Orionis', 'HIP 24436', 'Orion', 78.6345, -8.2016, 0.13, -0.03, 'B8 Ia', 1.31, 0.5, 17.8, 3.78, 0.34, 1107.6, 888.6, 92.5, 22950, 0.39],
  ['finding_bellatrix', 'Bellatrix', 'Gamma Orionis', 'HIP 25336', 'Orion', 81.2828, 6.3497, 1.64, -0.22, 'B2 III', -8.11, -12.88, 18.2, 12.89, 0.42, 1031.9, 472.9, 54.1, 8120, 0.41],
  ['finding_alnilam', 'Alnilam', 'Epsilon Orionis', 'HIP 26311', 'Orion', 84.0534, -1.2019, 1.69, -0.18, 'B0 Ia', 1.44, -0.78, 27.3, 2.43, 0.25, 952.8, 688.6, 51.8, 7840, 0.29],
  ['finding_alnitak', 'Alnitak', 'Zeta Orionis', 'HIP 26727', 'Orion', 85.1897, -1.9426, 1.77, -0.2, 'O9.5 Iab', 3.19, 2.03, 18.5, 4.43, 0.3, 920.3, 709.8, 49.4, 7310, 0.44],
  ['finding_mintaka', 'Mintaka', 'Delta Orionis', 'HIP 25930', 'Orion', 83.0017, -0.2991, 2.23, -0.21, 'O9.5 II', 0.64, -0.69, 16.0, 2.63, 0.22, 982.8, 662.8, 38.6, 5120, 0.46],
  ['finding_saiph', 'Saiph', 'Kappa Orionis', 'HIP 27366', 'Orion', 86.9391, -9.6696, 2.06, -0.17, 'B0.5 Ia', 1.46, -1.28, 20.7, 5.04, 0.28, 870.3, 930.5, 42.1, 5980, 0.51],
  ['finding_aldebaran', 'Aldebaran', 'Alpha Tauri', 'HIP 21421', 'Taurus', 68.9802, 16.5093, 0.86, 1.54, 'K5+ III', 63.45, -188.94, 54.26, 48.94, 0.45, 1383.4, 182.6, 71.3, 14190, 0.37],
  ['finding_sirius', 'Sirius', 'Alpha Canis Majoris', 'HIP 32349', 'Canis Major', 101.2872, -16.7161, -1.46, 0.0, 'A0mA1 Va', -546.01, -1223.07, -5.5, 379.21, 1.58, 460.4, 1054.2, 98.7, 42800, 0.48],
  ['finding_procyon', 'Procyon', 'Alpha Canis Minoris', 'HIP 37279', 'Canis Minor', 114.8255, 5.225, 0.34, 0.42, 'F5 IV-V', -714.59, -1036.8, -3.2, 284.56, 1.26, 73.6, 505.0, 86.4, 19650, 0.89],
];

const SYNTHETIC_EVIDENCE_NOTE = 'Synthetic test data: nothing was measured, matched or computed for this record.';

function buildFrameTimestampsMs(durationMs: number, fps: number): number[] {
  const count = Math.round((durationMs / 1000) * fps);
  return Array.from({ length: count }, (_, i) => Number(((i * 1000) / fps).toFixed(3)));
}

/** Distance bounds as the inverse of a test row's parallax and its error, in parsecs. */
function parallaxDistancePc(parallaxMas: number, parallaxErrorMas: number) {
  return {
    lower: Number((1000 / (parallaxMas + parallaxErrorMas)).toFixed(3)),
    median: Number((1000 / parallaxMas).toFixed(3)),
    upper: Number((1000 / (parallaxMas - parallaxErrorMas)).toFixed(3)),
  };
}

const DEFAULT_FIDELITY_POLICY: ProcessingGraph['fidelityPolicy'] = {
  noSyntheticSceneContent: true,
  allowSyntheticSky: false,
  preserveOriginalAudio: true,
  preserveFacesAndPeople: true,
  protectObservedStarPoints: true,
};

function buildCalibratedFindings(assetId: string, sourceSha256: string, solutionId: string): CelestialFinding[] {
  const matched: CelestialFinding[] = TEST_STARS.map(
    ([id, commonName, bayerDesignation, hipId, constellation, raDeg, decDeg, mag, colorIndexBpRp, spectralType, properMotionRaMasYr, properMotionDecMasYr, radialVelocityKmS, parallaxMas, parallaxErrorMas, xPx, yPx, snr, flux, pixelResidualPx]) => {
      const distance = parallaxDistancePc(parallaxMas, parallaxErrorMas);
      const catalogRef: CatalogReference = {
        id: `cat_${id.replace('finding_', '')}`,
        provider: 'Hipparcos',
        releaseVersion: 'test row',
        catalogSourceId: hipId,
        commonName,
        bayerDesignation,
        constellation,
        objectType: 'star',
        coordinateEpoch: 'J2000',
        raDeg,
        decDeg,
        apparentMagnitude: mag,
        colorIndexBpRp,
        spectralType,
        properMotionRaMasYr,
        properMotionDecMasYr,
        radialVelocityKmS,
        parallaxMas,
        parallaxErrorMas,
        bailerJonesDistancePc: { ...distance, method: 'geometric_parallax' },
        citation: `Test row keyed by ${hipId}; the values are typed test data, not read from a catalog release`,
      };
      const angularResidualArcsec = Number((pixelResidualPx * 0.035 * 3600).toFixed(2));
      return {
        id,
        observationSolutionId: solutionId,
        evidenceClass: 'catalog_matched',
        title: commonName,
        subtitle: `${bayerDesignation} · ${constellation}`,
        sourceDetection: { assetId, frameTimeSec: 4.2, xPx, yPx, flux, snr, fwhmPx: 2.4, centroidUncertaintyPx: 0.18 },
        catalogRef,
        skyPosition: { raDeg, decDeg, uncertaintyArcsec: angularResidualArcsec },
        inferredDistancePc: { ...distance, method: 'geometric_parallax' },
        cartesianParsecICRS: computeCartesianParsecICRS(raDeg, decDeg, distance.median),
        evidence: {
          id: `ev_${id}`,
          evidenceClass: 'catalog_matched',
          verificationMethod: 'Synthetic test finding standing in for a held-out verified catalog match',
          sourceAssetSha256: sourceSha256,
          wcsSolutionId: solutionId,
          catalogReferenceId: catalogRef.id,
          angularResidualArcsec,
          pixelResidualPx,
          distanceUncertaintyNote: `Inverse of the test row's parallax and its error: ${distance.lower} to ${distance.upper} pc.`,
          limitations: [SYNTHETIC_EVIDENCE_NOTE],
          verifiedByIndependentHoldout: true,
        },
      };
    }
  );

  const predicted: CelestialFinding[] = [
    ['finding_jupiter', 'Jupiter', 76.42, 22.18, 1.2],
    ['finding_uranus', 'Uranus', 54.85, 19.12, 1.5],
  ].map(([id, title, raDeg, decDeg, uncertaintyArcsec]) => ({
    id: id as string,
    observationSolutionId: solutionId,
    evidenceClass: 'predicted' as const,
    title: title as string,
    subtitle: 'Synthetic predicted position · test data',
    skyPosition: { raDeg: raDeg as number, decDeg: decDeg as number, uncertaintyArcsec: uncertaintyArcsec as number },
    evidence: {
      id: `ev_${id}`,
      evidenceClass: 'predicted' as const,
      verificationMethod: 'Synthetic test position: no ephemeris was computed',
      sourceAssetSha256: sourceSha256,
      wcsSolutionId: solutionId,
      limitations: [SYNTHETIC_EVIDENCE_NOTE],
      verifiedByIndependentHoldout: false,
    },
  }));

  const observed: CelestialFinding[] = [
    ['finding_obs_101', 'Field Source #101', 1142.4, 618.7, 412.0, 4.6, 2.8, 0.52, 84.52, 1.84, 68.0],
    ['finding_obs_102', 'Field Source #102', 784.1, 492.3, 318.5, 3.9, 3.1, 0.64, 81.94, 5.71, 74.0],
  ].map(([id, title, xPx, yPx, flux, snr, fwhmPx, centroidUncertaintyPx, raDeg, decDeg, uncertaintyArcsec]) => ({
    id: id as string,
    observationSolutionId: solutionId,
    evidenceClass: 'observed' as const,
    title: title as string,
    subtitle: `Synthetic point detection · SNR ${snr}`,
    sourceDetection: {
      assetId, frameTimeSec: 4.2, xPx: xPx as number, yPx: yPx as number, flux: flux as number, snr: snr as number,
      fwhmPx: fwhmPx as number, centroidUncertaintyPx: centroidUncertaintyPx as number,
    },
    skyPosition: { raDeg: raDeg as number, decDeg: decDeg as number, uncertaintyArcsec: uncertaintyArcsec as number },
    evidence: {
      id: `ev_${id}`,
      evidenceClass: 'observed' as const,
      verificationMethod: 'Synthetic test detection: no pixels were measured',
      sourceAssetSha256: sourceSha256,
      wcsSolutionId: solutionId,
      limitations: [SYNTHETIC_EVIDENCE_NOTE, 'No catalog identification or distance constraint.'],
      verifiedByIndependentHoldout: false,
    },
  }));

  return [...matched, ...predicted, ...observed];
}

/**
 * A schema-valid calibrated session over a synthetic Orion field: 10 catalog-matched test stars,
 * 2 predicted test planets and 2 observed test detections, with three processing attempts.
 */
export function createSyntheticCalibratedSession(): ObservationSession {
  const assetId = 'asset_synthetic_calibrated_field';
  // SHA-256 of 'albedian synthetic test media: calibrated field'
  const sourceSha256 = '0daa8e7f6a078e20329379d50ed100633cd4d9369e3f0680868fb7d1ffce44e1';
  // Synthetic moment: Orion high on a moonless night at the synthetic place
  const capturedAtUTC = '2030-01-01T02:00:00Z';
  const solutionId = 'sol_synthetic_calibrated_field';
  const frameTimestampsMs = buildFrameTimestampsMs(12000, 30);

  const sourceAsset: SourceAsset = {
    id: assetId, title: 'Synthetic calibrated field (test data)', mediaKind: 'video', sha256: sourceSha256,
    uri: 'synthetic://test/calibrated-field-1080p30.mp4', width: 1920, height: 1080, durationMs: 12000,
    fpsNumerator: 30, fpsDenominator: 1, videoCodec: 'h264',
    // SHA-256 of 'albedian synthetic test media: calibrated field audio'
    audioStreams: [{ index: 0, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 12000, sha256: '59b13889a6d1fee939a00b80131383714a1bb70e43f609dcadb9035f116b53f4' }],
    capturedAtUTC,
    observerLocation: { ...SYNTHETIC_PLACE, elevationM: 0, uncertaintyMeters: 15 },
    metadataSources: SYNTHETIC_METADATA_SOURCES,
    privacyClass: 'public_sample', immutable: true,
  };

  const graphOriginal: ProcessingGraph = {
    id: 'graph_original', sourceAssetId: assetId, sourceSha256,
    revisionSha256: deterministicHexDigest(`${sourceSha256}:original`),
    profileName: 'original', fidelityPolicy: DEFAULT_FIDELITY_POLICY, createdAtUTC: capturedAtUTC,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { colorSpace: 'rec709', passthrough: true }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['decode_root'], parameters: { copyAudio: true, crf: 14 }, preservePointSources: true },
    ],
  };

  const graphNatural: ProcessingGraph = {
    id: 'graph_natural', sourceAssetId: assetId, sourceSha256, parentGraphId: 'graph_original',
    revisionSha256: deterministicHexDigest(`${sourceSha256}:star_protected`),
    profileName: 'star_protected', fidelityPolicy: DEFAULT_FIDELITY_POLICY, createdAtUTC: capturedAtUTC,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { colorSpace: 'linear_rec709' }, preservePointSources: true },
      { nodeId: 'mask_foreground', kind: 'region_mask', operationVersion: '1.1.0', inputNodeIds: ['decode_root'], parameters: { protectPeopleAndForeground: true, featherPx: 6 }, maskId: 'mask_horizon_people', preservePointSources: true },
      { nodeId: 'tone_curve', kind: 'tone_transfer', operationVersion: '1.2.0', inputNodeIds: ['decode_root', 'mask_foreground'], parameters: { shadowLiftEv: 1.35, highlightRollOff: 0.85 }, preservePointSources: true },
      { nodeId: 'chroma_denoise', kind: 'spatial_denoise', operationVersion: '1.2.0', inputNodeIds: ['tone_curve'], parameters: { lumaStrength: 0.32, chromaStrength: 0.68 }, preservePointSources: true },
      { nodeId: 'star_guard', kind: 'star_protect_merge', operationVersion: '1.1.0', inputNodeIds: ['decode_root', 'chroma_denoise'], parameters: { minPointSnr: 3.2, psfRadiusPx: 3.0 }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['star_guard'], parameters: { copyAudio: true, crf: 15 }, preservePointSources: true },
    ],
  };

  const graphTemporal: ProcessingGraph = {
    id: 'graph_temporal', sourceAssetId: assetId, sourceSha256, parentGraphId: 'graph_natural',
    revisionSha256: deterministicHexDigest(`${sourceSha256}:temporal`),
    profileName: 'temporal', fidelityPolicy: DEFAULT_FIDELITY_POLICY, createdAtUTC: capturedAtUTC,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { colorSpace: 'linear_rec709' }, preservePointSources: true },
      { nodeId: 'temporal_align', kind: 'temporal_stack', operationVersion: '1.3.0', inputNodeIds: ['decode_root'], parameters: { windowFrames: 9, motionCompensated: true, rejectOutlierSigma: 2.8 }, maskId: 'mask_horizon_people', preservePointSources: true },
      { nodeId: 'tone_curve', kind: 'tone_transfer', operationVersion: '1.2.0', inputNodeIds: ['temporal_align'], parameters: { shadowLiftEv: 1.65, highlightRollOff: 0.88 }, preservePointSources: true },
      { nodeId: 'star_guard', kind: 'star_protect_merge', operationVersion: '1.1.0', inputNodeIds: ['decode_root', 'tone_curve'], parameters: { minPointSnr: 3.0, psfRadiusPx: 2.8 }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['star_guard'], parameters: { copyAudio: true, crf: 15 }, preservePointSources: true },
    ],
  };

  for (const g of [graphOriginal, graphNatural, graphTemporal]) {
    const check = validateProcessingGraphDAG(g);
    if (!check.valid) throw new Error(`Invalid ProcessingGraph ${g.id}: ${check.errors.join('; ')}`);
  }

  const processingAttempts: ProcessingAttempt[] = [
    {
      id: 'attempt_original', sourceAssetId: assetId, graphId: 'graph_original', label: 'Original Recording Passthrough',
      status: 'ready', cacheKey: computeNodeCacheKeys(graphOriginal).mux_out ?? graphOriginal.revisionSha256,
      startedAtUTC: '2030-01-01T02:00:01Z', completedAtUTC: '2030-01-01T02:00:02Z',
      elapsedMs: 120, cpuMs: 95, gpuMs: 0, peakMemoryBytes: 67108864, outputVariantId: 'variant_original',
      metrics: { evaluationMethod: 'synthetic_test_values', hasPairedReference: true, shadowSnrGainDb: 0, estimatedNoiseReductionPct: 0, pointSourceRetentionRatio: 1.0, falsePointArtifactCount: 0, faceRegionStabilityScore: 1.0, temporalFlickerIndex: 0.08, audioSyncOffsetMs: 0, validityNotes: ['Synthetic test values: nothing was measured.'] },
    },
    {
      id: 'attempt_natural', sourceAssetId: assetId, graphId: 'graph_natural', parentAttemptId: 'attempt_original',
      label: 'Natural Tone + Star-Protected Denoise', status: 'ready', cacheKey: computeNodeCacheKeys(graphNatural).mux_out ?? graphNatural.revisionSha256,
      startedAtUTC: '2030-01-01T02:00:03Z', completedAtUTC: '2030-01-01T02:00:05Z',
      elapsedMs: 1840, cpuMs: 620, gpuMs: 1120, peakMemoryBytes: 268435456, outputVariantId: 'variant_natural',
      metrics: { evaluationMethod: 'synthetic_test_values', hasPairedReference: false, shadowSnrGainDb: 5.4, estimatedNoiseReductionPct: 58, pointSourceRetentionRatio: 0.98, falsePointArtifactCount: 0, faceRegionStabilityScore: 0.99, temporalFlickerIndex: 0.04, audioSyncOffsetMs: 0.4, validityNotes: ['Synthetic test values: nothing was measured.'] },
    },
    {
      id: 'attempt_temporal', sourceAssetId: assetId, graphId: 'graph_temporal', parentAttemptId: 'attempt_natural',
      label: 'Motion-Compensated Temporal Stack', status: 'ready', cacheKey: computeNodeCacheKeys(graphTemporal).mux_out ?? graphTemporal.revisionSha256,
      startedAtUTC: '2030-01-01T02:00:06Z', completedAtUTC: '2030-01-01T02:00:09Z',
      elapsedMs: 3120, cpuMs: 940, gpuMs: 2050, peakMemoryBytes: 536870912, outputVariantId: 'variant_temporal',
      metrics: { evaluationMethod: 'synthetic_test_values', hasPairedReference: false, shadowSnrGainDb: 8.2, estimatedNoiseReductionPct: 76, pointSourceRetentionRatio: 0.99, falsePointArtifactCount: 0, faceRegionStabilityScore: 0.98, temporalFlickerIndex: 0.02, audioSyncOffsetMs: 0.6, validityNotes: ['Synthetic test values: nothing was measured.'] },
    },
  ];

  const mediaVariants: MediaVariant[] = [
    { id: 'variant_original', sourceAssetId: assetId, attemptId: 'attempt_original', graphId: 'graph_original', label: 'Original Recording', uri: 'synthetic://test/calibrated-field-1080p30.mp4', sha256: sourceSha256, width: 1920, height: 1080, durationMs: 12000, frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0, summaryNote: 'Synthetic test variant: the original recording.' },
    // SHA-256 of 'albedian synthetic test media: calibrated field natural variant'
    { id: 'variant_natural', sourceAssetId: assetId, attemptId: 'attempt_natural', graphId: 'graph_natural', label: 'Natural Tone + Star-Protected Denoise', uri: 'synthetic://test/calibrated-field-natural.mp4', sha256: '7d7448fba6d0c7fba5774555de28f515d8257c8c7da58c0b63abc7fd27cd57c5', width: 1920, height: 1080, durationMs: 12000, frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0.4, summaryNote: 'Synthetic test variant: tone transfer with star protection.' },
    // SHA-256 of 'albedian synthetic test media: calibrated field temporal variant'
    { id: 'variant_temporal', sourceAssetId: assetId, attemptId: 'attempt_temporal', graphId: 'graph_temporal', label: 'Motion-Compensated Temporal Stack', uri: 'synthetic://test/calibrated-field-temporal.mp4', sha256: '70af111522fc767777c68c8fd0abe22c036bd75a815dd4d4b5a2f979dd37ba0f', width: 1920, height: 1080, durationMs: 12000, frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0.6, summaryNote: 'Synthetic test variant: temporal stack.' },
  ];

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 37.8,
    frustumShiftNdc: [0, 0], simulationEpochISO: capturedAtUTC, observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: { observedMarkers: true, catalogMatchedLabels: true, predictedEphemerides: false, constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_synthetic_calibrated_anchor_0', label: 'Original Moment · Synthetic calibrated field (04.2s)', createdAtMs: 4200,
    sourceAssetId: assetId, sourceTimeSec: 4.2, playbackWasPlaying: true, audioGain: 0.85,
    mediaVariantId: 'variant_natural', cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(initialSpatialView), viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_synthetic_calibrated_field', title: 'Synthetic calibrated field (test data)', viewRevision: 1,
    machines: { media: 'ready', restoration: 'ready', calibration: 'calibrated', navigation: 'source', voice: 'listening' },
    sourceAsset, activeVariantId: 'variant_natural', compareWipePosition: 0.5,
    currentSourceTimeSec: 4.2, isPlaying: true, audioGain: 0.85, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [graphOriginal, graphNatural, graphTemporal], processingAttempts, mediaVariants,
    observationSolution: {
      id: solutionId, sourceAssetId: assetId, sourceSha256, frameTimeSec: 4.2, revision: 1, status: 'calibrated',
      observedAtUTC: capturedAtUTC, observerLocation: SYNTHETIC_PLACE,
      solver: { name: 'synthetic-test-solution', version: '0.0.0', catalogRelease: 'test rows' },
      wcs: { projection: 'TAN', crvalRaDeg: 83.8, crvalDecDeg: 4.0, crpixX: 960, crpixY: 540, scaleDegPerPx: 0.035, rotationDeg: 0, radialDistortionK1: -0.008, radialDistortionK2: 0.001, parity: -1, fovHorizontalDeg: 67.2, fovVerticalDeg: 37.8 },
      quality: { detectedCandidateCount: 28, matchedInlierCount: 20, heldOutInliers: 10, medianResidualPx: 0.42, p95ResidualPx: 0.89, medianResidualArcsec: 52.92, passedIndependentVerification: true },
      findings: buildCalibratedFindings(assetId, sourceSha256, solutionId),
      warnings: ['Synthetic test solution: no plate solve ran.'],
    },
    selectedFindingId: null, focusedFindingId: null, spatialView: initialSpatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
    voiceActions: [], exportArtifacts: [], reducedMotion: false,
    lastStatusBanner: 'Synthetic calibrated test session.',
  });
}

/**
 * A schema-valid session for a clip that could not be calibrated (`status: 'predicted_only'`):
 * two observed test detections at their pixels only, and two predicted test objects.
 */
export function createSyntheticUnresolvedSession(): ObservationSession {
  const assetId = 'asset_synthetic_overcast_field';
  // SHA-256 of 'albedian synthetic test media: unresolved field'
  const sourceSha256 = 'cb2d483ab58831a07c176869193c15ae6187967399b4cb58166e5500a6c73ec8';
  // Synthetic moment at the synthetic place
  const capturedAtUTC = '2030-06-15T23:05:00Z';
  const solutionId = 'sol_synthetic_overcast_field';
  const frameTimestampsMs = buildFrameTimestampsMs(6000, 30);

  const sourceAsset: SourceAsset = {
    id: assetId, title: 'Synthetic overcast field (test data)', mediaKind: 'video', sha256: sourceSha256,
    uri: 'synthetic://test/overcast-field-1080p30.mp4', width: 1920, height: 1080, durationMs: 6000,
    fpsNumerator: 30, fpsDenominator: 1, videoCodec: 'h264',
    // SHA-256 of 'albedian synthetic test media: unresolved field audio'
    audioStreams: [{ index: 0, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 6000, sha256: '19ac6254f7734374b3b0704c2409b10e5e6a3467a1a343cff654f1d2b54e076c' }],
    capturedAtUTC,
    observerLocation: { ...SYNTHETIC_PLACE, elevationM: 0, uncertaintyMeters: 25 },
    metadataSources: SYNTHETIC_METADATA_SOURCES,
    privacyClass: 'public_sample', immutable: true,
  };

  const graphOriginal: ProcessingGraph = {
    id: 'graph_overcast_original', sourceAssetId: assetId, sourceSha256,
    revisionSha256: deterministicHexDigest(`${sourceSha256}:original`),
    profileName: 'original', fidelityPolicy: DEFAULT_FIDELITY_POLICY, createdAtUTC: capturedAtUTC,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { passthrough: true }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['decode_root'], parameters: { copyAudio: true }, preservePointSources: true },
    ],
  };

  const attemptOriginal: ProcessingAttempt = {
    id: 'attempt_overcast_original', sourceAssetId: assetId, graphId: 'graph_overcast_original',
    label: 'Original Recording Passthrough', status: 'ready', cacheKey: computeNodeCacheKeys(graphOriginal).mux_out ?? graphOriginal.revisionSha256,
    startedAtUTC: '2030-06-15T23:05:01Z', completedAtUTC: '2030-06-15T23:05:02Z',
    elapsedMs: 95, cpuMs: 80, gpuMs: 0, peakMemoryBytes: 33554432, outputVariantId: 'variant_overcast_original',
  };

  const variantOriginal: MediaVariant = {
    id: 'variant_overcast_original', sourceAssetId: assetId, attemptId: 'attempt_overcast_original',
    graphId: 'graph_overcast_original', label: 'Original Recording', uri: 'synthetic://test/overcast-field-1080p30.mp4',
    sha256: sourceSha256, width: 1920, height: 1080, durationMs: 6000, frameTimestampsMs,
    audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0, summaryNote: 'Synthetic test variant: the original recording.',
  };

  const vegaRef: CatalogReference = {
    id: 'cat_pred_vega', provider: 'Hipparcos', releaseVersion: 'test row', catalogSourceId: 'HIP 91262',
    commonName: 'Vega', bayerDesignation: 'Alpha Lyrae', constellation: 'Lyra', objectType: 'star', coordinateEpoch: 'J2000',
    raDeg: 279.2347, decDeg: 38.7837, apparentMagnitude: 0.03,
    citation: 'Test row keyed by HIP 91262; the values are typed test data, not read from a catalog release',
  };

  const predictedFindings: CelestialFinding[] = [
    {
      id: 'finding_pred_vega', observationSolutionId: solutionId, evidenceClass: 'predicted',
      title: 'Vega (Predicted Overhead)', subtitle: 'Alpha Lyrae · Synthetic predicted position · test data',
      catalogRef: vegaRef,
      skyPosition: { raDeg: 279.2347, decDeg: 38.7837, uncertaintyArcsec: 1800 },
      evidence: { id: 'ev_pred_vega', evidenceClass: 'predicted', verificationMethod: 'Synthetic test position: no sky model was computed', sourceAssetSha256: sourceSha256, catalogReferenceId: vegaRef.id, limitations: [SYNTHETIC_EVIDENCE_NOTE], verifiedByIndependentHoldout: false },
    },
    {
      id: 'finding_pred_saturn', observationSolutionId: solutionId, evidenceClass: 'predicted',
      title: 'Saturn (Predicted Ephemeris)', subtitle: 'Synthetic predicted position · test data',
      skyPosition: { raDeg: 348.12, decDeg: -7.45, uncertaintyArcsec: 1800 },
      evidence: { id: 'ev_pred_saturn', evidenceClass: 'predicted', verificationMethod: 'Synthetic test position: no ephemeris was computed', sourceAssetSha256: sourceSha256, limitations: [SYNTHETIC_EVIDENCE_NOTE], verifiedByIndependentHoldout: false },
    },
  ];

  // The two detections stay listed at their pixels; without a verified WCS they have no sky position
  const observedFindings: CelestialFinding[] = [
    ['finding_obs_overcast_1', 'Field Source #1', 612.4, 214.9, 268.0, 4.1, 3.0, 0.61],
    ['finding_obs_overcast_2', 'Field Source #2', 1288.0, 341.6, 197.5, 3.7, 3.3, 0.74],
  ].map(([id, title, xPx, yPx, flux, snr, fwhmPx, centroidUncertaintyPx]) => ({
    id: id as string, observationSolutionId: solutionId, evidenceClass: 'observed' as const,
    title: title as string, subtitle: `Synthetic point detection · SNR ${snr}`,
    sourceDetection: {
      assetId, frameTimeSec: 1.5, xPx: xPx as number, yPx: yPx as number, flux: flux as number, snr: snr as number,
      fwhmPx: fwhmPx as number, centroidUncertaintyPx: centroidUncertaintyPx as number,
    },
    evidence: {
      id: `ev_${(id as string).replace('finding_', '')}`, evidenceClass: 'observed' as const,
      verificationMethod: 'Synthetic test detection: no pixels were measured', sourceAssetSha256: sourceSha256,
      limitations: [SYNTHETIC_EVIDENCE_NOTE, 'Pixel position only: 2 detections are too few for a verified WCS to place it on the sky.'],
      verifiedByIndependentHoldout: false,
    },
  }));

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 40,
    frustumShiftNdc: [0, 0], simulationEpochISO: capturedAtUTC, observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: { observedMarkers: false, catalogMatchedLabels: false, predictedEphemerides: true, constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_synthetic_overcast_anchor_0', label: 'Original Moment · Synthetic overcast field (01.5s)', createdAtMs: 1500,
    sourceAssetId: assetId, sourceTimeSec: 1.5, playbackWasPlaying: false, audioGain: 0.75,
    mediaVariantId: 'variant_overcast_original', cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(initialSpatialView), viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_synthetic_overcast_field', title: 'Synthetic overcast field (test data)', viewRevision: 1,
    machines: { media: 'ready', restoration: 'idle', calibration: 'predicted_only', navigation: 'source', voice: 'listening' },
    sourceAsset, activeVariantId: 'variant_overcast_original', compareWipePosition: 0.5,
    currentSourceTimeSec: 1.5, isPlaying: false, audioGain: 0.75, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [graphOriginal], processingAttempts: [attemptOriginal], mediaVariants: [variantOriginal],
    observationSolution: {
      id: solutionId, sourceAssetId: assetId, sourceSha256, frameTimeSec: 1.5, revision: 1, status: 'predicted_only',
      observedAtUTC: capturedAtUTC, observerLocation: SYNTHETIC_PLACE,
      solver: { name: 'synthetic-test-solution', version: '0.0.0', catalogRelease: 'test rows' },
      quality: { detectedCandidateCount: 2, matchedInlierCount: 2, heldOutInliers: 2, medianResidualPx: 8.4, p95ResidualPx: 14.2, medianResidualArcsec: 1058.4, passedIndependentVerification: false },
      findings: [...predictedFindings, ...observedFindings],
      warnings: ['Insufficient star detections (2 found, >= 6 held-out inliers required) for verified WCS registration. Showing predicted sky separately from source pixels.'],
    },
    selectedFindingId: null, focusedFindingId: null, spatialView: initialSpatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
    voiceActions: [], exportArtifacts: [], reducedMotion: false,
    lastStatusBanner: '2 point detections insufficient for blind plate solve. Sky predicted separately from recorded timestamp and location.',
  });
}
