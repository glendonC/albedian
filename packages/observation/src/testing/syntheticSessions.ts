import {
  CameraCheckpoint,
  CatalogReference,
  CelestialFinding,
  ColourDescription,
  DISCLOSURES,
  GraphParameter,
  GraphStep,
  MediaVariant,
  ObservationSession,
  ObservationSessionSchema,
  ProcessingAttempt,
  ProcessingGraph,
  RestoreRequest,
  SourceAsset,
  SpatialViewState,
  variantUri,
} from '@albedian/contracts';
import { computeCartesianParsecICRS } from '../checkpoints.js';
import { EMPTY_MEDIA_VARIANT_ID } from '../emptySession.js';

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

// A synthetic H.264 clip's colour: BT.709 throughout, limited range, chroma halved both ways
const SYNTHETIC_CLIP_COLOUR: ColourDescription = {
  model: 'ycbcr', bitDepth: 8, range: 'limited', matrix: 'bt709', transfer: 'bt709', primaries: 'bt709',
  chromaSubsampling: '420', chromaSiting: 'left', assumptions: [],
};
const RESTORATION_STEPS: GraphStep['kind'][] = [
  'decode', 'find_stars', 'measure_noise', 'register', 'temporal_merge', 'spatial_denoise', 'dissolve_unconfirmed',
];

/**
 * The records of two stored restorations of a synthetic clip: one at the default lift, one at the exposure a request
 * asked for, each with its graph, its ready attempt and its variant. Every number is typed test data.
 */
function buildSyntheticRestorations(sourceAsset: SourceAsset, startedAtSec: number) {
  const sourceSha256 = sourceAsset.sha256;
  // Ids are the first 16 hex digits (32 for a job, as the upload job runner writes it), and hashes all 64, of the SHA-256
  // of the label beside them
  const restorations: {
    request: RestoreRequest; lift: GraphParameter; gainAtBlack: number; decidedBy: 'noise_bound' | 'requested';
    graphId: string; attemptId: string; jobId: string; variantId: string; variantSha256: string;
  }[] = [
    {
      request: { kind: 'restoration' },
      lift: { name: 'gainAtBlack', value: 2.8, origin: 'measured', basis: 'Solved for this file from its median and its measured bounds.' },
      gainAtBlack: 2.8, decidedBy: 'noise_bound',
      // 'albedian synthetic test media: calibrated field restoration graph', '... attempt', '... job', '...' and '... bytes'
      graphId: 'graph_ea3dd03d56c44d38', attemptId: 'att_cf601f59643857cb', jobId: 'job_56954037ebf24abb566b0bc32cde5422',
      variantId: 'var_d629fe5dff4c53d7', variantSha256: '49ffc65df30a9140acf4f95b79b485824346d5326d4ab2b7c7144279466b6493',
    },
    {
      request: { kind: 'restoration', exposureEv: 1 },
      lift: { name: 'exposureEv', value: 1, origin: 'requested', basis: 'exposureEv' },
      gainAtBlack: 3.6, decidedBy: 'requested',
      // The same five labels, each followed by ', exposure 1 EV'
      graphId: 'graph_35088dcedd26985d', attemptId: 'att_56820a819b5bc129', jobId: 'job_80de33cf5554b0a435a59b4f6a0edd49',
      variantId: 'var_1354eb12349b6fbd', variantSha256: '7d04d887e22437cd2df50f3fc887d0008b64c79a2e56b6a5d00bd7f865fb82e1',
    },
  ];
  const at = (sec: number) => `2030-01-01T02:00:${String(sec).padStart(2, '0')}Z`;
  const processingGraphs: ProcessingGraph[] = restorations.map((r) => ({
    id: r.graphId, sourceSha256, kind: 'restoration', pipelineVersion: '1.0.0', request: r.request,
    steps: [
      ...RESTORATION_STEPS.map((kind) => ({ kind, version: '1.0.0', parameters: [] })),
      { kind: 'lift', version: '1.0.0', parameters: [r.lift] },
      { kind: 'encode', version: '1.0.0', parameters: [] },
      { kind: 'copy_audio', version: '1.0.0', parameters: [] },
    ],
  }));
  const processingAttempts: ProcessingAttempt[] = restorations.map((r, i) => ({
    id: r.attemptId, sourceSha256, graphId: r.graphId, jobId: r.jobId, status: 'ready',
    startedAtUTC: at(startedAtSec + 3 * i), finishedAtUTC: at(startedAtSec + 3 * i + 2), elapsedMs: 1840, cpuMs: 1620,
    variantId: r.variantId,
    measured: {
      colour: SYNTHETIC_CLIP_COLOUR,
      noise: { intensities: [0.002, 0.02, 0.2], sigma: { r: [0.001, 0.003, 0.009], g: [0.001, 0.002, 0.008], b: [0.002, 0.004, 0.01] }, blockCounts: [64, 48, 12] },
      stars: { found: 24, confirmed: 20 },
      lift: { gainAtBlack: r.gainAtBlack, decidedBy: r.decidedBy },
      clip: { frameCount: 360, framesWithNeighbours: 354, exactRepeatShare: 0, pointsConfirmedOnFramesTogether: 0, pointsFixedToSensor: 0, streamsNotCopied: 0 },
      notes: [],
    },
    gate: {
      statistic: 'detector', variantThresholdSnr: 6, confirmThresholdSnr: 4.75, tilePx: 256, matchRadiusPx: 1.5, framesChecked: 360,
      variantPointCount: 20, unconfirmedPointCount: 0, framesWithUnconfirmed: 0, clippedPointsLeftOut: 0, starsMeasured: 20,
      starsOutsideFluxTolerance: 0, fluxToleranceSigma: 3, worstFluxChangeSigma: 1.2, medianFluxRatio: 1, passed: true,
    },
  }));
  const mediaVariants: MediaVariant[] = restorations.map((r, i) => ({
    id: r.variantId, kind: 'restoration', sourceSha256, sourceAssetId: sourceAsset.id, graphId: r.graphId, attemptId: r.attemptId,
    sha256: r.variantSha256, uri: variantUri(sourceSha256, r.variantId), byteSize: 18874368, mimeType: 'video/mp4',
    width: sourceAsset.width, height: sourceAsset.height, durationMs: sourceAsset.durationMs, frameCount: 360,
    // SHA-256 of 'albedian synthetic test media: calibrated field frame times'
    frameTimesSha256: 'f56412beae280969517c1ed7d23b4bea609366e1738e54e17fb5f5fc80cc727d',
    audioStreams: sourceAsset.audioStreams, colour: SYNTHETIC_CLIP_COLOUR, disclosure: DISCLOSURES.restoration,
    createdAtUTC: at(startedAtSec + 3 * i + 2),
  }));
  return { processingGraphs, processingAttempts, mediaVariants };
}

/** Distance bounds as the inverse of a test row's parallax and its error, in parsecs. */
function parallaxDistancePc(parallaxMas: number, parallaxErrorMas: number) {
  return {
    lower: Number((1000 / (parallaxMas + parallaxErrorMas)).toFixed(3)),
    median: Number((1000 / parallaxMas).toFixed(3)),
    upper: Number((1000 / (parallaxMas - parallaxErrorMas)).toFixed(3)),
  };
}

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
 * 2 predicted test planets and 2 observed test detections, with two stored restorations of its clip.
 */
export function createSyntheticCalibratedSession(): ObservationSession {
  // As ingest names an asset: src_ and the first 16 hex digits of its SHA-256
  const assetId = 'src_0daa8e7f6a078e20';
  // SHA-256 of 'albedian synthetic test media: calibrated field'
  const sourceSha256 = '0daa8e7f6a078e20329379d50ed100633cd4d9369e3f0680868fb7d1ffce44e1';
  // Synthetic moment: Orion high on a moonless night at the synthetic place
  const capturedAtUTC = '2030-01-01T02:00:00Z';
  const solutionId = 'sol_synthetic_calibrated_field';

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

  const { processingGraphs, processingAttempts, mediaVariants } = buildSyntheticRestorations(sourceAsset, 1);
  const activeVariantId = mediaVariants[0]!.id;

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
    mediaVariantId: activeVariantId, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(initialSpatialView), viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_synthetic_calibrated_field', title: 'Synthetic calibrated field (test data)', viewRevision: 1,
    machines: { media: 'ready', restoration: 'ready', calibration: 'calibrated', navigation: 'source', voice: 'listening' },
    sourceAsset, activeVariantId, compareWipePosition: 0.5,
    currentSourceTimeSec: 4.2, isPlaying: true, audioGain: 0.85, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs, processingAttempts, mediaVariants,
    observationSolution: {
      id: solutionId, sourceAssetId: assetId, sourceSha256, frameTimeSec: 4.2, revision: 1, status: 'calibrated',
      observedAtUTC: capturedAtUTC, observerLocation: SYNTHETIC_PLACE,
      solver: { name: 'synthetic-test-solution', version: '0.0.0', catalogRelease: 'test rows' },
      wcs: { projection: 'TAN', crvalRaDeg: 83.8, crvalDecDeg: 4.0, crpixX: 960, crpixY: 540, scaleDegPerPx: 0.035, rotationDeg: 0, radialDistortionK1: -0.008, radialDistortionK2: 0.001, parity: -1, fovHorizontalDeg: 67.2, fovVerticalDeg: 37.8 },
      quality: { detectedCandidateCount: 28, matchedInlierCount: 20, heldOutPairCount: 10, heldOutInliers: 10, medianResidualPx: 0.42, p95ResidualPx: 0.89, medianResidualArcsec: 52.92, passedIndependentVerification: true },
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
    mediaVariantId: EMPTY_MEDIA_VARIANT_ID, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(initialSpatialView), viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_synthetic_overcast_field', title: 'Synthetic overcast field (test data)', viewRevision: 1,
    machines: { media: 'ready', restoration: 'idle', calibration: 'predicted_only', navigation: 'source', voice: 'listening' },
    // Nothing restored this clip: the original is shown and the session holds no variant
    sourceAsset, activeVariantId: EMPTY_MEDIA_VARIANT_ID, compareWipePosition: 0.5,
    currentSourceTimeSec: 1.5, isPlaying: false, audioGain: 0.75, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [], processingAttempts: [], mediaVariants: [],
    observationSolution: {
      id: solutionId, sourceAssetId: assetId, sourceSha256, frameTimeSec: 1.5, revision: 1, status: 'predicted_only',
      observedAtUTC: capturedAtUTC, observerLocation: SYNTHETIC_PLACE,
      solver: { name: 'synthetic-test-solution', version: '0.0.0', catalogRelease: 'test rows' },
      quality: { detectedCandidateCount: 2, matchedInlierCount: 2, heldOutPairCount: 2, heldOutInliers: 2, medianResidualPx: 8.4, p95ResidualPx: 14.2, medianResidualArcsec: 1058.4, passedIndependentVerification: false },
      findings: [...predictedFindings, ...observedFindings],
      warnings: ['Insufficient star detections (2 found, >= 6 held-out inliers required) for verified WCS registration. Showing predicted sky separately from source pixels.'],
    },
    selectedFindingId: null, focusedFindingId: null, spatialView: initialSpatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
    voiceActions: [], exportArtifacts: [], reducedMotion: false,
    lastStatusBanner: '2 point detections insufficient for blind plate solve. Sky predicted separately from recorded timestamp and location.',
  });
}
