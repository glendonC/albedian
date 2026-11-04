import {
  CameraCheckpoint,
  CatalogReference,
  CelestialFinding,
  MediaVariant,
  ObservationSession,
  ObservationSessionSchema,
  ObservationSolution,
  ProcessingAttempt,
  ProcessingGraph,
  SourceAsset,
  SpatialViewState,
  computeNodeCacheKeys,
  deterministicHexDigest,
  validateProcessingGraphDAG,
} from '@albedian/contracts';
import { computeCartesianParsecICRS } from './checkpoints.js';

/**
 * ============================================================================
 * @albedian/observation — Golden Sahara & Unresolved Session Fixtures
 * ============================================================================
 */

type StarTuple = [
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
  dLow: number,
  dMed: number,
  dHigh: number,
  xPx: number,
  yPx: number,
  snr: number,
  flux: number,
  resPx: number,
];

const SAHARA_STARS: StarTuple[] = [
  ['finding_betelgeuse', 'Betelgeuse', 'Alpha Orionis', 'HIP 27989', 'Orion', 88.7929, 7.4071, 0.42, 2.05, 'M1-M2 Ia-ab', 27.54, 11.3, 21.91, 5.95, 0.45, 152, 168, 190, 817.3, 442.6, 84.2, 18420, 0.34],
  ['finding_rigel', 'Rigel', 'Beta Orionis', 'HIP 24436', 'Orion', 78.6345, -8.2016, 0.13, -0.03, 'B8 Ia', 1.31, 0.5, 17.8, 3.78, 0.34, 240, 264.5, 290, 1107.6, 888.6, 92.5, 22950, 0.39],
  ['finding_bellatrix', 'Bellatrix', 'Gamma Orionis', 'HIP 25336', 'Orion', 81.2828, 6.3497, 1.64, -0.22, 'B2 III', -8.11, -12.88, 18.2, 12.89, 0.42, 75, 77.5, 80, 1031.9, 472.9, 54.1, 8120, 0.41],
  ['finding_alnilam', 'Alnilam', 'Epsilon Orionis', 'HIP 26311', 'Orion', 84.0534, -1.2019, 1.69, -0.18, 'B0 Ia', 1.44, -0.78, 27.3, 2.43, 0.25, 380, 412, 460, 952.8, 688.6, 51.8, 7840, 0.29],
  ['finding_alnitak', 'Alnitak', 'Zeta Orionis', 'HIP 26727', 'Orion', 85.1897, -1.9426, 1.77, -0.2, 'O9.5 Iab', 3.19, 2.03, 18.5, 4.43, 0.3, 215, 226, 240, 920.3, 709.8, 49.4, 7310, 0.44],
  ['finding_mintaka', 'Mintaka', 'Delta Orionis', 'HIP 25930', 'Orion', 83.0017, -0.2991, 2.23, -0.21, 'O9.5 II', 0.64, -0.69, 16.0, 2.63, 0.22, 360, 380, 405, 982.8, 662.8, 38.6, 5120, 0.46],
  ['finding_saiph', 'Saiph', 'Kappa Orionis', 'HIP 27366', 'Orion', 86.9391, -9.6696, 2.06, -0.17, 'B0.5 Ia', 1.46, -1.28, 20.7, 5.04, 0.28, 185, 198, 215, 870.3, 930.5, 42.1, 5980, 0.51],
  ['finding_aldebaran', 'Aldebaran', 'Alpha Tauri', 'HIP 21421', 'Taurus', 68.9802, 16.5093, 0.86, 1.54, 'K5+ III', 63.45, -188.94, 54.26, 48.94, 0.45, 20.0, 20.4, 20.9, 1383.4, 182.6, 71.3, 14190, 0.37],
  ['finding_sirius', 'Sirius', 'Alpha Canis Majoris', 'HIP 32349', 'Canis Major', 101.2872, -16.7161, -1.46, 0.0, 'A0mA1 Va', -546.01, -1223.07, -5.5, 379.21, 1.58, 2.63, 2.64, 2.65, 460.4, 1054.2, 98.7, 42800, 0.48],
  ['finding_procyon', 'Procyon', 'Alpha Canis Minoris', 'HIP 37279', 'Canis Minor', 114.8255, 5.225, 0.34, 0.42, 'F5 IV-V', -714.59, -1036.8, -3.2, 284.56, 1.26, 3.5, 3.51, 3.52, 73.6, 505.0, 86.4, 19650, 0.89],
];

function buildFrameTimestampsMs(durationMs: number, fps: number): number[] {
  const count = Math.round((durationMs / 1000) * fps);
  return Array.from({ length: count }, (_, i) => Number(((i * 1000) / fps).toFixed(3)));
}

const DEFAULT_FIDELITY_POLICY: ProcessingGraph['fidelityPolicy'] = {
  noSyntheticSceneContent: true,
  allowSyntheticSky: false,
  preserveOriginalAudio: true,
  preserveFacesAndPeople: true,
  protectObservedStarPoints: true,
};

function buildSaharaCatalogFindings(
  assetId: string,
  sourceSha256: string,
  solutionId: string
): CelestialFinding[] {
  const matched: CelestialFinding[] = SAHARA_STARS.map(
    ([id, commonName, bayerDesignation, hipId, constellation, raDeg, decDeg, mag, colorIndexBpRp, spectralType, properMotionRaMasYr, properMotionDecMasYr, radialVelocityKmS, parallaxMas, parallaxErrorMas, lower, median, upper, xPx, yPx, snr, flux, pixelResidualPx]) => {
      const catalogRef: CatalogReference = {
        id: `cat_${id.replace('finding_', '')}`,
        provider: 'Gaia DR3',
        releaseVersion: 'Gaia DR3 / Hipparcos Cross-Match 2023.1',
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
        bailerJonesDistancePc: { lower, median, upper, method: 'photogeometric_bailer_jones_edr3' },
        citation: `Gaia Collaboration (2023) DR3 & Bailer-Jones et al. (2021) [${hipId}]`,
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
        skyPosition: { raDeg, decDeg, uncertaintyArcsec: angularResidualArcsec, altDeg: 48.5, azDeg: 116.0 },
        inferredDistancePc: { lower, median, upper, method: 'photogeometric_bailer_jones_edr3' },
        cartesianParsecICRS: computeCartesianParsecICRS(raDeg, decDeg, median),
        evidence: {
          id: `ev_${id}`,
          evidenceClass: 'catalog_matched',
          verificationMethod: 'DAOStarFinder centroid + TAN WCS plate solve + 10-star held-out residual gate',
          sourceAssetSha256: sourceSha256,
          wcsSolutionId: solutionId,
          catalogReferenceId: catalogRef.id,
          angularResidualArcsec,
          pixelResidualPx,
          distanceUncertaintyNote: `Bailer-Jones 68% confidence interval: ${lower}–${upper} pc (median ${median} pc).`,
          limitations: ['Line-of-sight radial distance derived from Gaia EDR3/DR3 photogeometric prior, not single-frame camera depth.'],
          verifiedByIndependentHoldout: true,
        },
      };
    }
  );

  const predicted: CelestialFinding[] = [
    {
      id: 'finding_jupiter',
      observationSolutionId: solutionId,
      evidenceClass: 'predicted',
      title: 'Jupiter',
      subtitle: 'JPL Horizons DE440 · Taurus Ephemeris',
      catalogRef: {
        id: 'cat_jupiter_de440', provider: 'JPL Horizons', releaseVersion: 'DE440', catalogSourceId: 'JPL 599',
        commonName: 'Jupiter', constellation: 'Taurus', objectType: 'planet', coordinateEpoch: '2030-01-01T02:00:00Z',
        raDeg: 76.42, decDeg: 22.18, apparentMagnitude: -2.41, citation: 'NASA/JPL Horizons DE440 Planetary Ephemeris (2030-01-01T02:00:00Z)',
      },
      skyPosition: { raDeg: 76.42, decDeg: 22.18, uncertaintyArcsec: 1.2, altDeg: 54.1, azDeg: 102.3 },
      evidence: {
        id: 'ev_finding_jupiter', evidenceClass: 'predicted',
        verificationMethod: 'JPL DE440 topocentric ephemeris from recorded UTC timestamp and synthetic site coordinates',
        sourceAssetSha256: sourceSha256, wcsSolutionId: solutionId, catalogReferenceId: 'cat_jupiter_de440',
        limitations: ['Predicted from JPL Horizons ephemeris; not claimed as an unclipped point-spread centroid in camera pixels.'],
        verifiedByIndependentHoldout: false,
      },
    },
    {
      id: 'finding_uranus',
      observationSolutionId: solutionId,
      evidenceClass: 'predicted',
      title: 'Uranus',
      subtitle: 'JPL Horizons DE440 · Taurus Ephemeris',
      catalogRef: {
        id: 'cat_uranus_de440', provider: 'JPL Horizons', releaseVersion: 'DE440', catalogSourceId: 'JPL 799',
        commonName: 'Uranus', constellation: 'Taurus', objectType: 'planet', coordinateEpoch: '2030-01-01T02:00:00Z',
        raDeg: 54.85, decDeg: 19.12, apparentMagnitude: 5.68, citation: 'NASA/JPL Horizons DE440 Planetary Ephemeris (2030-01-01T02:00:00Z)',
      },
      skyPosition: { raDeg: 54.85, decDeg: 19.12, uncertaintyArcsec: 1.5, altDeg: 61.4, azDeg: 95.8 },
      evidence: {
        id: 'ev_finding_uranus', evidenceClass: 'predicted',
        verificationMethod: 'JPL DE440 topocentric ephemeris from recorded UTC timestamp and synthetic site coordinates',
        sourceAssetSha256: sourceSha256, wcsSolutionId: solutionId, catalogReferenceId: 'cat_uranus_de440',
        limitations: ['Apparent magnitude 5.68 is below single-frame handheld video SNR threshold; position is predicted from ephemeris only.'],
        verifiedByIndependentHoldout: false,
      },
    },
  ];

  const observed: CelestialFinding[] = [
    {
      id: 'finding_obs_101', observationSolutionId: solutionId, evidenceClass: 'observed',
      title: 'Field Source #101', subtitle: 'Observed Point Detection · SNR 4.6',
      sourceDetection: { assetId, frameTimeSec: 4.2, xPx: 1142.4, yPx: 618.7, flux: 412.0, snr: 4.6, fwhmPx: 2.8, centroidUncertaintyPx: 0.52 },
      skyPosition: { raDeg: 84.52, decDeg: 1.84, uncertaintyArcsec: 68.0, altDeg: 47.2, azDeg: 118.4 },
      evidence: {
        id: 'ev_finding_obs_101', evidenceClass: 'observed',
        verificationMethod: 'Direct source pixel aperture photometry (SNR 4.6 >= 3.5 threshold)',
        sourceAssetSha256: sourceSha256, wcsSolutionId: solutionId,
        limitations: ['Measured directly in source recording pixels; lacks an unambiguous Gaia DR3 catalog match or parallax distance.'],
        verifiedByIndependentHoldout: false,
      },
    },
    {
      id: 'finding_obs_102', observationSolutionId: solutionId, evidenceClass: 'observed',
      title: 'Field Source #102', subtitle: 'Observed Point Detection · SNR 3.9',
      sourceDetection: { assetId, frameTimeSec: 4.2, xPx: 784.1, yPx: 492.3, flux: 318.5, snr: 3.9, fwhmPx: 3.1, centroidUncertaintyPx: 0.64 },
      skyPosition: { raDeg: 81.94, decDeg: 5.71, uncertaintyArcsec: 74.0, altDeg: 51.0, azDeg: 114.2 },
      evidence: {
        id: 'ev_finding_obs_102', evidenceClass: 'observed',
        verificationMethod: 'Direct source pixel aperture photometry (SNR 3.9 >= 3.5 threshold)',
        sourceAssetSha256: sourceSha256, wcsSolutionId: solutionId,
        limitations: ['Measured directly in source recording pixels; no catalog identification or distance constraint.'],
        verifiedByIndependentHoldout: false,
      },
    },
  ];

  return [...matched, ...predicted, ...observed];
}

/**
 * Returns the complete, schema-validated golden `ObservationSession` for
 * "Sahara Night — Orion & Taurus Over synthetic site" (`2030-01-01T02:00:00Z`).
 */
export function createDefaultSaharaObservationSession(): ObservationSession {
  const assetId = 'asset_sahara_orion_2026';
  const sourceSha256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
  const capturedAtUTC = '2030-01-01T02:00:00Z';
  const solutionId = 'sol_sahara_wcs_v1';
  const frameTimestampsMs = buildFrameTimestampsMs(12000, 30);

  const sourceAsset: SourceAsset = {
    id: assetId, title: 'Sahara Night — Orion & Taurus Over synthetic site', mediaKind: 'video', sha256: sourceSha256,
    uri: 'albedian://samples/sahara-orion-2026-1080p30.mp4', width: 1920, height: 1080, durationMs: 12000,
    fpsNumerator: 30, fpsDenominator: 1, videoCodec: 'h264',
    audioStreams: [{ index: 0, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 12000, sha256: 'a1b2c3d4e5f60718293a4b5c6d7e8f90' }],
    capturedAtUTC,
    observerLocation: { latDeg: 30.0, lonDeg: -40.0, elevationM: 710, placeName: 'synthetic site, Morocco', uncertaintyMeters: 15 },
    cameraMetadata: { make: 'Sony', model: 'A7S III', focalLengthMm: 24, estimatedFovDeg: 67.2, exposureTimeSec: 0.0333, iso: 12800 },
    privacyClass: 'consented_demo', immutable: true,
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
      { nodeId: 'mask_foreground', kind: 'region_mask', operationVersion: '1.1.0', inputNodeIds: ['decode_root'], parameters: { protectPeopleAndDunes: true, featherPx: 6 }, maskId: 'mask_sahara_horizon_people', preservePointSources: true },
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
      { nodeId: 'temporal_align', kind: 'temporal_stack', operationVersion: '1.3.0', inputNodeIds: ['decode_root'], parameters: { windowFrames: 9, motionCompensated: true, rejectOutlierSigma: 2.8 }, maskId: 'mask_sahara_horizon_people', preservePointSources: true },
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
      metrics: { evaluationMethod: 'identity_reference_check', hasPairedReference: true, shadowSnrGainDb: 0, estimatedNoiseReductionPct: 0, pointSourceRetentionRatio: 1.0, falsePointArtifactCount: 0, faceRegionStabilityScore: 1.0, temporalFlickerIndex: 0.08, audioSyncOffsetMs: 0, validityNotes: ['Immutable original source stream.'] },
    },
    {
      id: 'attempt_natural', sourceAssetId: assetId, graphId: 'graph_natural', parentAttemptId: 'attempt_original',
      label: 'Natural Tone + Star-Protected Denoise', status: 'ready', cacheKey: computeNodeCacheKeys(graphNatural).mux_out ?? graphNatural.revisionSha256,
      startedAtUTC: '2030-01-01T02:00:03Z', completedAtUTC: '2030-01-01T02:00:05Z',
      elapsedMs: 1840, cpuMs: 620, gpuMs: 1120, peakMemoryBytes: 268435456, outputVariantId: 'variant_natural',
      metrics: { evaluationMethod: 'no_reference_sky_patch_snr_and_psf_guard', hasPairedReference: false, shadowSnrGainDb: 5.4, estimatedNoiseReductionPct: 58, pointSourceRetentionRatio: 0.98, falsePointArtifactCount: 0, faceRegionStabilityScore: 0.99, temporalFlickerIndex: 0.04, audioSyncOffsetMs: 0.4, validityNotes: ['Foreground silhouette and people mask preserved.', 'Zero synthetic stars introduced.'] },
    },
    {
      id: 'attempt_temporal', sourceAssetId: assetId, graphId: 'graph_temporal', parentAttemptId: 'attempt_natural',
      label: 'Motion-Compensated Temporal Stack', status: 'ready', cacheKey: computeNodeCacheKeys(graphTemporal).mux_out ?? graphTemporal.revisionSha256,
      startedAtUTC: '2030-01-01T02:00:06Z', completedAtUTC: '2030-01-01T02:00:09Z',
      elapsedMs: 3120, cpuMs: 940, gpuMs: 2050, peakMemoryBytes: 536870912, outputVariantId: 'variant_temporal',
      metrics: { evaluationMethod: 'temporal_window_sky_patch_snr_and_psf_guard', hasPairedReference: false, shadowSnrGainDb: 8.2, estimatedNoiseReductionPct: 76, pointSourceRetentionRatio: 0.99, falsePointArtifactCount: 0, faceRegionStabilityScore: 0.98, temporalFlickerIndex: 0.02, audioSyncOffsetMs: 0.6, validityNotes: ['9-frame motion-compensated sky stack with foreground rejection.', '99% point-source retention with 0 false star artifacts.'] },
    },
  ];

  const mediaVariants: MediaVariant[] = [
    { id: 'variant_original', sourceAssetId: assetId, attemptId: 'attempt_original', graphId: 'graph_original', label: 'Original Recording', uri: 'albedian://samples/sahara-orion-2026-1080p30.mp4', sha256: sourceSha256, width: 1920, height: 1080, durationMs: 12000, frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0, summaryNote: 'Unmodified camera recording with original desert wind & conversation audio.' },
    { id: 'variant_natural', sourceAssetId: assetId, attemptId: 'attempt_natural', graphId: 'graph_natural', label: 'Natural Tone + Star-Protected Denoise', uri: 'albedian://variants/sahara-orion-2026-natural.mp4', sha256: deterministicHexDigest(`${sourceSha256}:variant_natural`), width: 1920, height: 1080, durationMs: 12000, frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0.4, summaryNote: '+5.4 dB shadow SNR, 98% star retention, foreground people protected.' },
    { id: 'variant_temporal', sourceAssetId: assetId, attemptId: 'attempt_temporal', graphId: 'graph_temporal', label: 'Motion-Compensated Temporal Stack', uri: 'albedian://variants/sahara-orion-2026-temporal.mp4', sha256: deterministicHexDigest(`${sourceSha256}:variant_temporal`), width: 1920, height: 1080, durationMs: 12000, frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0.6, summaryNote: '+8.2 dB shadow SNR, 99% star retention, 9-frame temporal sky alignment.' },
  ];

  const observationSolution: ObservationSolution = {
    id: solutionId, sourceAssetId: assetId, sourceSha256, frameTimeSec: 4.2, revision: 1, status: 'calibrated',
    observedAtUTC: capturedAtUTC, observerLocation: { latDeg: 30.0, lonDeg: -40.0, placeName: 'synthetic site, Morocco' },
    solver: { name: 'albedian-wcs-quad-geometric', version: '1.0.0', catalogRelease: 'Gaia DR3 / Hipparcos Bright Subset + JPL DE440' },
    wcs: { projection: 'TAN', crvalRaDeg: 83.8, crvalDecDeg: 4.0, crpixX: 960, crpixY: 540, scaleDegPerPx: 0.035, rotationDeg: 0, radialDistortionK1: -0.008, radialDistortionK2: 0.001, parity: -1, fovHorizontalDeg: 67.2, fovVerticalDeg: 37.8 },
    quality: { detectedCandidateCount: 28, matchedInlierCount: 20, heldOutInliers: 10, medianResidualPx: 0.42, p95ResidualPx: 0.89, medianResidualArcsec: 52.92, passedIndependentVerification: true },
    findings: buildSaharaCatalogFindings(assetId, sourceSha256, solutionId),
    warnings: [],
  };

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 37.8,
    frustumShiftNdc: [0, 0], simulationEpochISO: capturedAtUTC, observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: { observedMarkers: true, catalogMatchedLabels: true, predictedEphemerides: false, constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_sahara_anchor_0', label: 'Original Moment — synthetic site (04.2s)', createdAtMs: 4200,
    sourceAssetId: assetId, sourceTimeSec: 4.2, playbackWasPlaying: true, audioGain: 0.85,
    mediaVariantId: 'variant_natural', cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(initialSpatialView), viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_sahara_orion_2026', title: 'Sahara Night — Orion & Taurus Over synthetic site', viewRevision: 1,
    machines: { media: 'ready', restoration: 'ready', calibration: 'calibrated', navigation: 'source', voice: 'listening' },
    sourceAsset, activeVariantId: 'variant_natural', compareWipePosition: 0.5,
    currentSourceTimeSec: 4.2, isPlaying: true, audioGain: 0.85, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [graphOriginal, graphNatural, graphTemporal], processingAttempts, mediaVariants, observationSolution,
    selectedFindingId: null, focusedFindingId: null, spatialView: initialSpatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
    voiceActions: [], exportArtifacts: [], reducedMotion: false,
    lastStatusBanner: 'Calibrated (10 held-out stars, 0.42 px median residual).',
  });
}

/**
 * Returns a schema-validated `ObservationSession` representing an overcast/clipped dark clip
 * (`status: 'predicted_only'`, `calibration: 'predicted_only'`, `heldOutInliers: 2`).
 */
export function createUnresolvedObservationSession(): ObservationSession {
  const assetId = 'asset_overcast_dusk_2026';
  const sourceSha256 = '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08';
  const capturedAtUTC = '2030-01-02T21:05:00Z';
  const solutionId = 'sol_overcast_predicted_v1';
  const frameTimestampsMs = buildFrameTimestampsMs(6000, 30);

  const sourceAsset: SourceAsset = {
    id: assetId, title: 'Overcast Horizon — Sparse Cloud Gap Recording', mediaKind: 'video', sha256: sourceSha256,
    uri: 'albedian://samples/overcast-dusk-2026-1080p30.mp4', width: 1920, height: 1080, durationMs: 6000,
    fpsNumerator: 30, fpsDenominator: 1, videoCodec: 'h264',
    audioStreams: [{ index: 0, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 6000, sha256: 'b4c5d6e7f8091a2b3c4d5e6f708192a3' }],
    capturedAtUTC,
    observerLocation: { latDeg: 37.7749, lonDeg: -122.4194, elevationM: 52, placeName: 'San Francisco, CA', uncertaintyMeters: 25 },
    privacyClass: 'consented_demo', immutable: true,
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
    startedAtUTC: '2030-01-02T21:05:01Z', completedAtUTC: '2030-01-02T21:05:02Z',
    elapsedMs: 95, cpuMs: 80, gpuMs: 0, peakMemoryBytes: 33554432, outputVariantId: 'variant_overcast_original',
  };

  const variantOriginal: MediaVariant = {
    id: 'variant_overcast_original', sourceAssetId: assetId, attemptId: 'attempt_overcast_original',
    graphId: 'graph_overcast_original', label: 'Original Recording', uri: 'albedian://samples/overcast-dusk-2026-1080p30.mp4',
    sha256: sourceSha256, width: 1920, height: 1080, durationMs: 6000, frameTimestampsMs,
    audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0, summaryNote: 'Original cloud-obstructed recording.',
  };

  const predictedFindings: CelestialFinding[] = [
    {
      id: 'finding_pred_vega', observationSolutionId: solutionId, evidenceClass: 'predicted',
      title: 'Vega (Predicted Overhead)', subtitle: 'Alpha Lyrae · Predicted from Time & Location Only',
      catalogRef: { id: 'cat_pred_vega', provider: 'Hipparcos', releaseVersion: 'HIP-2', catalogSourceId: 'HIP 91262', commonName: 'Vega', bayerDesignation: 'Alpha Lyrae', constellation: 'Lyra', objectType: 'star', coordinateEpoch: 'J2000', raDeg: 279.2347, decDeg: 38.7837, apparentMagnitude: 0.03, citation: 'Hipparcos Catalog HIP 91262 (Predicted via timestamp/location)' },
      skyPosition: { raDeg: 279.2347, decDeg: 38.7837, uncertaintyArcsec: 1800, altDeg: 62.0, azDeg: 280.0 },
      evidence: { id: 'ev_pred_vega', evidenceClass: 'predicted', verificationMethod: 'Approximate topocentric sky model from timestamp and coarse location', sourceAssetSha256: sourceSha256, limitations: ['Only 2 faint candidate points detected in frame; minimum 6 held-out inliers required for WCS calibration.'], verifiedByIndependentHoldout: false },
    },
    {
      id: 'finding_pred_saturn', observationSolutionId: solutionId, evidenceClass: 'predicted',
      title: 'Saturn (Predicted Ephemeris)', subtitle: 'JPL Horizons DE440 · Predicted from Time & Location Only',
      catalogRef: { id: 'cat_pred_saturn', provider: 'JPL Horizons', releaseVersion: 'DE440', catalogSourceId: 'JPL 699', commonName: 'Saturn', constellation: 'Aquarius', objectType: 'planet', coordinateEpoch: capturedAtUTC, raDeg: 348.12, decDeg: -7.45, apparentMagnitude: 0.78, citation: 'NASA/JPL Horizons DE440 (Predicted via timestamp/location)' },
      skyPosition: { raDeg: 348.12, decDeg: -7.45, uncertaintyArcsec: 1800, altDeg: 41.2, azDeg: 162.5 },
      evidence: { id: 'ev_pred_saturn', evidenceClass: 'predicted', verificationMethod: 'JPL DE440 topocentric ephemeris from timestamp and coarse location', sourceAssetSha256: sourceSha256, limitations: ['Cloud cover prevented astrometric plate solve; position is shown in separate predicted-sky mode only.'], verifiedByIndependentHoldout: false },
    },
  ];

  // The two detections stay listed at their pixels; without a verified WCS they have no sky position
  const observedFindings: CelestialFinding[] = [
    {
      id: 'finding_obs_overcast_1', observationSolutionId: solutionId, evidenceClass: 'observed',
      title: 'Field Source #1', subtitle: 'Observed Point Detection · SNR 4.1',
      sourceDetection: { assetId, frameTimeSec: 1.5, xPx: 612.4, yPx: 214.9, flux: 268.0, snr: 4.1, fwhmPx: 3.0, centroidUncertaintyPx: 0.61 },
      evidence: { id: 'ev_obs_overcast_1', evidenceClass: 'observed', verificationMethod: 'Direct source pixel aperture photometry (SNR 4.1 >= 3.5 threshold)', sourceAssetSha256: sourceSha256, limitations: ['Pixel position only: 2 detections are too few for a verified WCS to place it on the sky.'], verifiedByIndependentHoldout: false },
    },
    {
      id: 'finding_obs_overcast_2', observationSolutionId: solutionId, evidenceClass: 'observed',
      title: 'Field Source #2', subtitle: 'Observed Point Detection · SNR 3.7',
      sourceDetection: { assetId, frameTimeSec: 1.5, xPx: 1288.0, yPx: 341.6, flux: 197.5, snr: 3.7, fwhmPx: 3.3, centroidUncertaintyPx: 0.74 },
      evidence: { id: 'ev_obs_overcast_2', evidenceClass: 'observed', verificationMethod: 'Direct source pixel aperture photometry (SNR 3.7 >= 3.5 threshold)', sourceAssetSha256: sourceSha256, limitations: ['Pixel position only: 2 detections are too few for a verified WCS to place it on the sky.'], verifiedByIndependentHoldout: false },
    },
  ];

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 40,
    frustumShiftNdc: [0, 0], simulationEpochISO: capturedAtUTC, observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: { observedMarkers: false, catalogMatchedLabels: false, predictedEphemerides: true, constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_overcast_anchor_0', label: 'Original Moment — Overcast Horizon (01.5s)', createdAtMs: 1500,
    sourceAssetId: assetId, sourceTimeSec: 1.5, playbackWasPlaying: false, audioGain: 0.75,
    mediaVariantId: 'variant_overcast_original', cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(initialSpatialView), viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_overcast_dusk_2026', title: 'Overcast Horizon — Sparse Cloud Gap Recording', viewRevision: 1,
    machines: { media: 'ready', restoration: 'idle', calibration: 'predicted_only', navigation: 'source', voice: 'listening' },
    sourceAsset, activeVariantId: 'variant_overcast_original', compareWipePosition: 0.5,
    currentSourceTimeSec: 1.5, isPlaying: false, audioGain: 0.75, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [graphOriginal], processingAttempts: [attemptOriginal], mediaVariants: [variantOriginal],
    observationSolution: {
      id: solutionId, sourceAssetId: assetId, sourceSha256, frameTimeSec: 1.5, revision: 1, status: 'predicted_only',
      observedAtUTC: capturedAtUTC, observerLocation: { latDeg: 37.7749, lonDeg: -122.4194, placeName: 'San Francisco, CA' },
      solver: { name: 'albedian-wcs-quad-geometric', version: '1.0.0', catalogRelease: 'Gaia DR3 / Hipparcos Bright Subset + JPL DE440' },
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
