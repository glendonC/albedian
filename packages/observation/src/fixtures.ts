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

interface GoldenStarSpec {
  id: string;
  commonName: string;
  bayerDesignation: string;
  hipId: string;
  constellation: string;
  raDeg: number;
  decDeg: number;
  mag: number;
  colorIndexBpRp: number;
  spectralType: string;
  properMotionRaMasYr: number;
  properMotionDecMasYr: number;
  radialVelocityKmS: number;
  parallaxMas: number;
  parallaxErrorMas: number;
  distance: { lower: number; median: number; upper: number };
  xPx: number;
  yPx: number;
  snr: number;
  flux: number;
  pixelResidualPx: number;
}

const SAHARA_CATALOG_STARS: GoldenStarSpec[] = [
  {
    id: 'finding_betelgeuse', commonName: 'Betelgeuse', bayerDesignation: 'Alpha Orionis', hipId: 'HIP 27989',
    constellation: 'Orion', raDeg: 88.7929, decDeg: 7.4071, mag: 0.42, colorIndexBpRp: 2.05, spectralType: 'M1-M2 Ia-ab',
    properMotionRaMasYr: 27.54, properMotionDecMasYr: 11.3, radialVelocityKmS: 21.91, parallaxMas: 5.95, parallaxErrorMas: 0.45,
    distance: { lower: 152, median: 168, upper: 190 }, xPx: 817.3, yPx: 442.6, snr: 84.2, flux: 18420, pixelResidualPx: 0.34,
  },
  {
    id: 'finding_rigel', commonName: 'Rigel', bayerDesignation: 'Beta Orionis', hipId: 'HIP 24436',
    constellation: 'Orion', raDeg: 78.6345, decDeg: -8.2016, mag: 0.13, colorIndexBpRp: -0.03, spectralType: 'B8 Ia',
    properMotionRaMasYr: 1.31, properMotionDecMasYr: 0.5, radialVelocityKmS: 17.8, parallaxMas: 3.78, parallaxErrorMas: 0.34,
    distance: { lower: 240, median: 264.5, upper: 290 }, xPx: 1107.6, yPx: 888.6, snr: 92.5, flux: 22950, pixelResidualPx: 0.39,
  },
  {
    id: 'finding_bellatrix', commonName: 'Bellatrix', bayerDesignation: 'Gamma Orionis', hipId: 'HIP 25336',
    constellation: 'Orion', raDeg: 81.2828, decDeg: 6.3497, mag: 1.64, colorIndexBpRp: -0.22, spectralType: 'B2 III',
    properMotionRaMasYr: -8.11, properMotionDecMasYr: -12.88, radialVelocityKmS: 18.2, parallaxMas: 12.89, parallaxErrorMas: 0.42,
    distance: { lower: 75, median: 77.5, upper: 80 }, xPx: 1031.9, yPx: 472.9, snr: 54.1, flux: 8120, pixelResidualPx: 0.41,
  },
  {
    id: 'finding_alnilam', commonName: 'Alnilam', bayerDesignation: 'Epsilon Orionis', hipId: 'HIP 26311',
    constellation: 'Orion', raDeg: 84.0534, decDeg: -1.2019, mag: 1.69, colorIndexBpRp: -0.18, spectralType: 'B0 Ia',
    properMotionRaMasYr: 1.44, properMotionDecMasYr: -0.78, radialVelocityKmS: 27.3, parallaxMas: 2.43, parallaxErrorMas: 0.25,
    distance: { lower: 380, median: 412, upper: 460 }, xPx: 952.8, yPx: 688.6, snr: 51.8, flux: 7840, pixelResidualPx: 0.29,
  },
  {
    id: 'finding_alnitak', commonName: 'Alnitak', bayerDesignation: 'Zeta Orionis', hipId: 'HIP 26727',
    constellation: 'Orion', raDeg: 85.1897, decDeg: -1.9426, mag: 1.77, colorIndexBpRp: -0.2, spectralType: 'O9.5 Iab',
    properMotionRaMasYr: 3.19, properMotionDecMasYr: 2.03, radialVelocityKmS: 18.5, parallaxMas: 4.43, parallaxErrorMas: 0.3,
    distance: { lower: 215, median: 226, upper: 240 }, xPx: 920.3, yPx: 709.8, snr: 49.4, flux: 7310, pixelResidualPx: 0.44,
  },
  {
    id: 'finding_mintaka', commonName: 'Mintaka', bayerDesignation: 'Delta Orionis', hipId: 'HIP 25930',
    constellation: 'Orion', raDeg: 83.0017, decDeg: -0.2991, mag: 2.23, colorIndexBpRp: -0.21, spectralType: 'O9.5 II',
    properMotionRaMasYr: 0.64, properMotionDecMasYr: -0.69, radialVelocityKmS: 16.0, parallaxMas: 2.63, parallaxErrorMas: 0.22,
    distance: { lower: 360, median: 380, upper: 405 }, xPx: 982.8, yPx: 662.8, snr: 38.6, flux: 5120, pixelResidualPx: 0.46,
  },
  {
    id: 'finding_saiph', commonName: 'Saiph', bayerDesignation: 'Kappa Orionis', hipId: 'HIP 27366',
    constellation: 'Orion', raDeg: 86.9391, decDeg: -9.6696, mag: 2.06, colorIndexBpRp: -0.17, spectralType: 'B0.5 Ia',
    properMotionRaMasYr: 1.46, properMotionDecMasYr: -1.28, radialVelocityKmS: 20.7, parallaxMas: 5.04, parallaxErrorMas: 0.28,
    distance: { lower: 185, median: 198, upper: 215 }, xPx: 870.3, yPx: 930.5, snr: 42.1, flux: 5980, pixelResidualPx: 0.51,
  },
  {
    id: 'finding_aldebaran', commonName: 'Aldebaran', bayerDesignation: 'Alpha Tauri', hipId: 'HIP 21421',
    constellation: 'Taurus', raDeg: 68.9802, decDeg: 16.5093, mag: 0.86, colorIndexBpRp: 1.54, spectralType: 'K5+ III',
    properMotionRaMasYr: 63.45, properMotionDecMasYr: -188.94, radialVelocityKmS: 54.26, parallaxMas: 48.94, parallaxErrorMas: 0.45,
    distance: { lower: 20.0, median: 20.4, upper: 20.9 }, xPx: 1383.4, yPx: 182.6, snr: 71.3, flux: 14190, pixelResidualPx: 0.37,
  },
  {
    id: 'finding_sirius', commonName: 'Sirius', bayerDesignation: 'Alpha Canis Majoris', hipId: 'HIP 32349',
    constellation: 'Canis Major', raDeg: 101.2872, decDeg: -16.7161, mag: -1.46, colorIndexBpRp: 0.0, spectralType: 'A0mA1 Va',
    properMotionRaMasYr: -546.01, properMotionDecMasYr: -1223.07, radialVelocityKmS: -5.5, parallaxMas: 379.21, parallaxErrorMas: 1.58,
    distance: { lower: 2.63, median: 2.64, upper: 2.65 }, xPx: 460.4, yPx: 1054.2, snr: 98.7, flux: 42800, pixelResidualPx: 0.48,
  },
  {
    id: 'finding_procyon', commonName: 'Procyon', bayerDesignation: 'Alpha Canis Minoris', hipId: 'HIP 37279',
    constellation: 'Canis Minor', raDeg: 114.8255, decDeg: 5.225, mag: 0.34, colorIndexBpRp: 0.42, spectralType: 'F5 IV-V',
    properMotionRaMasYr: -714.59, properMotionDecMasYr: -1036.8, radialVelocityKmS: -3.2, parallaxMas: 284.56, parallaxErrorMas: 1.26,
    distance: { lower: 3.5, median: 3.51, upper: 3.52 }, xPx: 73.6, yPx: 505.0, snr: 86.4, flux: 19650, pixelResidualPx: 0.89,
  },
];

function buildFrameTimestampsMs(durationMs: number, fps: number): number[] {
  const frameCount = Math.round((durationMs / 1000) * fps);
  return Array.from({ length: frameCount }, (_, i) => Number(((i * 1000) / fps).toFixed(3)));
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
  const matchedFindings: CelestialFinding[] = SAHARA_CATALOG_STARS.map((star) => {
    const catalogRef: CatalogReference = {
      id: `cat_${star.id.replace('finding_', '')}`,
      provider: 'Gaia DR3',
      releaseVersion: 'Gaia DR3 / Hipparcos Cross-Match 2023.1',
      catalogSourceId: star.hipId,
      commonName: star.commonName,
      bayerDesignation: star.bayerDesignation,
      constellation: star.constellation,
      objectType: 'star',
      coordinateEpoch: 'J2000',
      raDeg: star.raDeg,
      decDeg: star.decDeg,
      apparentMagnitude: star.mag,
      colorIndexBpRp: star.colorIndexBpRp,
      spectralType: star.spectralType,
      properMotionRaMasYr: star.properMotionRaMasYr,
      properMotionDecMasYr: star.properMotionDecMasYr,
      radialVelocityKmS: star.radialVelocityKmS,
      parallaxMas: star.parallaxMas,
      parallaxErrorMas: star.parallaxErrorMas,
      bailerJonesDistancePc: {
        lower: star.distance.lower,
        median: star.distance.median,
        upper: star.distance.upper,
        method: 'photogeometric_bailer_jones_edr3',
      },
      citation: `Gaia Collaboration (2023) DR3 & Bailer-Jones et al. (2021) [${star.hipId}]`,
    };

    const angularResidualArcsec = Number((star.pixelResidualPx * 0.035 * 3600).toFixed(2));
    return {
      id: star.id,
      observationSolutionId: solutionId,
      evidenceClass: 'catalog_matched',
      title: star.commonName,
      subtitle: `${star.bayerDesignation} · ${star.constellation}`,
      sourceDetection: {
        assetId,
        frameTimeSec: 4.2,
        xPx: star.xPx,
        yPx: star.yPx,
        flux: star.flux,
        snr: star.snr,
        fwhmPx: 2.4,
        centroidUncertaintyPx: 0.18,
      },
      catalogRef,
      skyPosition: {
        raDeg: star.raDeg,
        decDeg: star.decDeg,
        uncertaintyArcsec: angularResidualArcsec,
        altDeg: 48.5,
        azDeg: 116.0,
      },
      inferredDistancePc: {
        lower: star.distance.lower,
        median: star.distance.median,
        upper: star.distance.upper,
        method: 'photogeometric_bailer_jones_edr3',
      },
      cartesianParsecICRS: computeCartesianParsecICRS(star.raDeg, star.decDeg, star.distance.median),
      evidence: {
        id: `ev_${star.id}`,
        evidenceClass: 'catalog_matched',
        verificationMethod: 'DAOStarFinder centroid + TAN WCS plate solve + 10-star held-out residual gate',
        sourceAssetSha256: sourceSha256,
        wcsSolutionId: solutionId,
        catalogReferenceId: catalogRef.id,
        angularResidualArcsec,
        pixelResidualPx: star.pixelResidualPx,
        distanceUncertaintyNote: `Bailer-Jones 68% confidence interval: ${star.distance.lower}–${star.distance.upper} pc (median ${star.distance.median} pc).`,
        limitations: [
          'Line-of-sight radial distance derived from Gaia EDR3/DR3 photogeometric prior, not single-frame camera depth.',
        ],
        verifiedByIndependentHoldout: true,
      },
    };
  });

  const predictedFindings: CelestialFinding[] = [
    {
      id: 'finding_jupiter',
      observationSolutionId: solutionId,
      evidenceClass: 'predicted',
      title: 'Jupiter',
      subtitle: 'JPL Horizons DE440 · Taurus Ephemeris',
      catalogRef: {
        id: 'cat_jupiter_de440',
        provider: 'JPL Horizons',
        releaseVersion: 'DE440',
        catalogSourceId: 'JPL 599',
        commonName: 'Jupiter',
        constellation: 'Taurus',
        objectType: 'planet',
        coordinateEpoch: '2026-10-19T22:14:00Z',
        raDeg: 76.42,
        decDeg: 22.18,
        apparentMagnitude: -2.41,
        citation: 'NASA/JPL Horizons DE440 Planetary Ephemeris (2026-10-19T22:14:00Z)',
      },
      skyPosition: { raDeg: 76.42, decDeg: 22.18, uncertaintyArcsec: 1.2, altDeg: 54.1, azDeg: 102.3 },
      evidence: {
        id: 'ev_finding_jupiter',
        evidenceClass: 'predicted',
        verificationMethod: 'JPL DE440 topocentric ephemeris from recorded UTC timestamp and Erg Chebbi coordinates',
        sourceAssetSha256: sourceSha256,
        wcsSolutionId: solutionId,
        catalogReferenceId: 'cat_jupiter_de440',
        limitations: [
          'Predicted from JPL Horizons ephemeris; not claimed as an unclipped point-spread centroid in camera pixels.',
          'Solar System bodies lack parsec-scale stellar parallax priors and cannot be entered as 3D stellar catalog geometry.',
        ],
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
        id: 'cat_uranus_de440',
        provider: 'JPL Horizons',
        releaseVersion: 'DE440',
        catalogSourceId: 'JPL 799',
        commonName: 'Uranus',
        constellation: 'Taurus',
        objectType: 'planet',
        coordinateEpoch: '2026-10-19T22:14:00Z',
        raDeg: 54.85,
        decDeg: 19.12,
        apparentMagnitude: 5.68,
        citation: 'NASA/JPL Horizons DE440 Planetary Ephemeris (2026-10-19T22:14:00Z)',
      },
      skyPosition: { raDeg: 54.85, decDeg: 19.12, uncertaintyArcsec: 1.5, altDeg: 61.4, azDeg: 95.8 },
      evidence: {
        id: 'ev_finding_uranus',
        evidenceClass: 'predicted',
        verificationMethod: 'JPL DE440 topocentric ephemeris from recorded UTC timestamp and Erg Chebbi coordinates',
        sourceAssetSha256: sourceSha256,
        wcsSolutionId: solutionId,
        catalogReferenceId: 'cat_uranus_de440',
        limitations: [
          'Apparent magnitude 5.68 is below single-frame handheld video SNR threshold; position is predicted from ephemeris only.',
        ],
        verifiedByIndependentHoldout: false,
      },
    },
  ];

  const observedFindings: CelestialFinding[] = [
    {
      id: 'finding_obs_101',
      observationSolutionId: solutionId,
      evidenceClass: 'observed',
      title: 'Field Source #101',
      subtitle: 'Observed Point Detection · SNR 4.6',
      sourceDetection: {
        assetId, frameTimeSec: 4.2, xPx: 1142.4, yPx: 618.7, flux: 412.0, snr: 4.6, fwhmPx: 2.8, centroidUncertaintyPx: 0.52,
      },
      skyPosition: { raDeg: 84.52, decDeg: 1.84, uncertaintyArcsec: 68.0, altDeg: 47.2, azDeg: 118.4 },
      evidence: {
        id: 'ev_finding_obs_101',
        evidenceClass: 'observed',
        verificationMethod: 'Direct source pixel aperture photometry (SNR 4.6 >= 3.5 threshold)',
        sourceAssetSha256: sourceSha256,
        wcsSolutionId: solutionId,
        limitations: ['Measured directly in source recording pixels; lacks an unambiguous Gaia DR3 catalog match or parallax distance.'],
        verifiedByIndependentHoldout: false,
      },
    },
    {
      id: 'finding_obs_102',
      observationSolutionId: solutionId,
      evidenceClass: 'observed',
      title: 'Field Source #102',
      subtitle: 'Observed Point Detection · SNR 3.9',
      sourceDetection: {
        assetId, frameTimeSec: 4.2, xPx: 784.1, yPx: 492.3, flux: 318.5, snr: 3.9, fwhmPx: 3.1, centroidUncertaintyPx: 0.64,
      },
      skyPosition: { raDeg: 81.94, decDeg: 5.71, uncertaintyArcsec: 74.0, altDeg: 51.0, azDeg: 114.2 },
      evidence: {
        id: 'ev_finding_obs_102',
        evidenceClass: 'observed',
        verificationMethod: 'Direct source pixel aperture photometry (SNR 3.9 >= 3.5 threshold)',
        sourceAssetSha256: sourceSha256,
        wcsSolutionId: solutionId,
        limitations: ['Measured directly in source recording pixels; no catalog identification or distance constraint.'],
        verifiedByIndependentHoldout: false,
      },
    },
  ];

  return [...matchedFindings, ...predictedFindings, ...observedFindings];
}

/**
 * Returns the complete, schema-validated golden `ObservationSession` for
 * "Sahara Night — Orion & Taurus Over Erg Chebbi" (`2026-10-19T22:14:00Z`).
 */
export function createDefaultSaharaObservationSession(): ObservationSession {
  const assetId = 'asset_sahara_orion_2026';
  const sourceSha256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
  const capturedAtUTC = '2026-10-19T22:14:00Z';
  const solutionId = 'sol_sahara_wcs_v1';
  const frameTimestampsMs = buildFrameTimestampsMs(12000, 30);

  const sourceAsset: SourceAsset = {
    id: assetId,
    title: 'Sahara Night — Orion & Taurus Over Erg Chebbi',
    mediaKind: 'video',
    sha256: sourceSha256,
    uri: 'albedian://samples/sahara-orion-2026-1080p30.mp4',
    width: 1920,
    height: 1080,
    durationMs: 12000,
    fpsNumerator: 30,
    fpsDenominator: 1,
    videoCodec: 'h264',
    audioStreams: [
      { index: 0, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 12000, sha256: 'a1b2c3d4e5f60718293a4b5c6d7e8f90' },
    ],
    capturedAtUTC,
    observerLocation: { latDeg: 31.149, lonDeg: -3.972, elevationM: 710, placeName: 'Erg Chebbi, Morocco', uncertaintyMeters: 15 },
    cameraMetadata: { make: 'Sony', model: 'A7S III', focalLengthMm: 24, estimatedFovDeg: 67.2, exposureTimeSec: 0.0333, iso: 12800 },
    privacyClass: 'consented_demo',
    immutable: true,
  };

  const graphOriginal: ProcessingGraph = {
    id: 'graph_original',
    sourceAssetId: assetId,
    sourceSha256,
    revisionSha256: deterministicHexDigest(`${sourceSha256}:original`),
    profileName: 'original',
    fidelityPolicy: DEFAULT_FIDELITY_POLICY,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { colorSpace: 'rec709', passthrough: true }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['decode_root'], parameters: { copyAudio: true, crf: 14 }, preservePointSources: true },
    ],
    createdAtUTC: capturedAtUTC,
  };

  const graphNatural: ProcessingGraph = {
    id: 'graph_natural',
    sourceAssetId: assetId,
    sourceSha256,
    parentGraphId: 'graph_original',
    revisionSha256: deterministicHexDigest(`${sourceSha256}:star_protected`),
    profileName: 'star_protected',
    fidelityPolicy: DEFAULT_FIDELITY_POLICY,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { colorSpace: 'linear_rec709' }, preservePointSources: true },
      { nodeId: 'mask_foreground', kind: 'region_mask', operationVersion: '1.1.0', inputNodeIds: ['decode_root'], parameters: { protectPeopleAndDunes: true, featherPx: 6 }, maskId: 'mask_sahara_horizon_people', preservePointSources: true },
      { nodeId: 'tone_curve', kind: 'tone_transfer', operationVersion: '1.2.0', inputNodeIds: ['decode_root', 'mask_foreground'], parameters: { shadowLiftEv: 1.35, highlightRollOff: 0.85 }, preservePointSources: true },
      { nodeId: 'chroma_denoise', kind: 'spatial_denoise', operationVersion: '1.2.0', inputNodeIds: ['tone_curve'], parameters: { lumaStrength: 0.32, chromaStrength: 0.68 }, preservePointSources: true },
      { nodeId: 'star_guard', kind: 'star_protect_merge', operationVersion: '1.1.0', inputNodeIds: ['decode_root', 'chroma_denoise'], parameters: { minPointSnr: 3.2, psfRadiusPx: 3.0 }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['star_guard'], parameters: { copyAudio: true, crf: 15 }, preservePointSources: true },
    ],
    createdAtUTC: capturedAtUTC,
  };

  const graphTemporal: ProcessingGraph = {
    id: 'graph_temporal',
    sourceAssetId: assetId,
    sourceSha256,
    parentGraphId: 'graph_natural',
    revisionSha256: deterministicHexDigest(`${sourceSha256}:temporal`),
    profileName: 'temporal',
    fidelityPolicy: DEFAULT_FIDELITY_POLICY,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { colorSpace: 'linear_rec709' }, preservePointSources: true },
      { nodeId: 'temporal_align', kind: 'temporal_stack', operationVersion: '1.3.0', inputNodeIds: ['decode_root'], parameters: { windowFrames: 9, motionCompensated: true, rejectOutlierSigma: 2.8 }, maskId: 'mask_sahara_horizon_people', preservePointSources: true },
      { nodeId: 'tone_curve', kind: 'tone_transfer', operationVersion: '1.2.0', inputNodeIds: ['temporal_align'], parameters: { shadowLiftEv: 1.65, highlightRollOff: 0.88 }, preservePointSources: true },
      { nodeId: 'star_guard', kind: 'star_protect_merge', operationVersion: '1.1.0', inputNodeIds: ['decode_root', 'tone_curve'], parameters: { minPointSnr: 3.0, psfRadiusPx: 2.8 }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['star_guard'], parameters: { copyAudio: true, crf: 15 }, preservePointSources: true },
    ],
    createdAtUTC: capturedAtUTC,
  };

  for (const graph of [graphOriginal, graphNatural, graphTemporal]) {
    const dagCheck = validateProcessingGraphDAG(graph);
    if (!dagCheck.valid) {
      throw new Error(`Invalid golden ProcessingGraph ${graph.id}: ${dagCheck.errors.join('; ')}`);
    }
  }

  const originalCacheKeys = computeNodeCacheKeys(graphOriginal);
  const naturalCacheKeys = computeNodeCacheKeys(graphNatural);
  const temporalCacheKeys = computeNodeCacheKeys(graphTemporal);

  const processingAttempts: ProcessingAttempt[] = [
    {
      id: 'attempt_original', sourceAssetId: assetId, graphId: 'graph_original', label: 'Original Recording Passthrough',
      status: 'ready', cacheKey: originalCacheKeys.mux_out ?? graphOriginal.revisionSha256,
      startedAtUTC: '2026-10-19T22:14:01Z', completedAtUTC: '2026-10-19T22:14:02Z',
      elapsedMs: 120, cpuMs: 95, gpuMs: 0, peakMemoryBytes: 67108864, outputVariantId: 'variant_original',
      metrics: {
        evaluationMethod: 'identity_reference_check', hasPairedReference: true, shadowSnrGainDb: 0,
        estimatedNoiseReductionPct: 0, pointSourceRetentionRatio: 1.0, falsePointArtifactCount: 0,
        faceRegionStabilityScore: 1.0, temporalFlickerIndex: 0.08, audioSyncOffsetMs: 0,
        validityNotes: ['Immutable original source stream.'],
      },
    },
    {
      id: 'attempt_natural', sourceAssetId: assetId, graphId: 'graph_natural', parentAttemptId: 'attempt_original',
      label: 'Natural Tone + Star-Protected Denoise', status: 'ready', cacheKey: naturalCacheKeys.mux_out ?? graphNatural.revisionSha256,
      startedAtUTC: '2026-10-19T22:14:03Z', completedAtUTC: '2026-10-19T22:14:05Z',
      elapsedMs: 1840, cpuMs: 620, gpuMs: 1120, peakMemoryBytes: 268435456, outputVariantId: 'variant_natural',
      metrics: {
        evaluationMethod: 'no_reference_sky_patch_snr_and_psf_guard', hasPairedReference: false, shadowSnrGainDb: 5.4,
        estimatedNoiseReductionPct: 58, pointSourceRetentionRatio: 0.98, falsePointArtifactCount: 0,
        faceRegionStabilityScore: 0.99, temporalFlickerIndex: 0.04, audioSyncOffsetMs: 0.4,
        validityNotes: ['Foreground silhouette and people mask preserved.', 'Zero synthetic stars introduced.'],
      },
    },
    {
      id: 'attempt_temporal', sourceAssetId: assetId, graphId: 'graph_temporal', parentAttemptId: 'attempt_natural',
      label: 'Motion-Compensated Temporal Stack', status: 'ready', cacheKey: temporalCacheKeys.mux_out ?? graphTemporal.revisionSha256,
      startedAtUTC: '2026-10-19T22:14:06Z', completedAtUTC: '2026-10-19T22:14:09Z',
      elapsedMs: 3120, cpuMs: 940, gpuMs: 2050, peakMemoryBytes: 536870912, outputVariantId: 'variant_temporal',
      metrics: {
        evaluationMethod: 'temporal_window_sky_patch_snr_and_psf_guard', hasPairedReference: false, shadowSnrGainDb: 8.2,
        estimatedNoiseReductionPct: 76, pointSourceRetentionRatio: 0.99, falsePointArtifactCount: 0,
        faceRegionStabilityScore: 0.98, temporalFlickerIndex: 0.02, audioSyncOffsetMs: 0.6,
        validityNotes: ['9-frame motion-compensated sky stack with foreground rejection.', '99% point-source retention with 0 false star artifacts.'],
      },
    },
  ];

  const mediaVariants: MediaVariant[] = [
    {
      id: 'variant_original', sourceAssetId: assetId, attemptId: 'attempt_original', graphId: 'graph_original',
      label: 'Original Recording', uri: 'albedian://samples/sahara-orion-2026-1080p30.mp4', sha256: sourceSha256,
      width: 1920, height: 1080, durationMs: 12000, frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0,
      summaryNote: 'Unmodified camera recording with original desert wind & conversation audio.',
    },
    {
      id: 'variant_natural', sourceAssetId: assetId, attemptId: 'attempt_natural', graphId: 'graph_natural',
      label: 'Natural Tone + Star-Protected Denoise', uri: 'albedian://variants/sahara-orion-2026-natural.mp4',
      sha256: deterministicHexDigest(`${sourceSha256}:variant_natural`), width: 1920, height: 1080, durationMs: 12000,
      frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0.4,
      summaryNote: '+5.4 dB shadow SNR, 98% star retention, foreground people protected.',
    },
    {
      id: 'variant_temporal', sourceAssetId: assetId, attemptId: 'attempt_temporal', graphId: 'graph_temporal',
      label: 'Motion-Compensated Temporal Stack', uri: 'albedian://variants/sahara-orion-2026-temporal.mp4',
      sha256: deterministicHexDigest(`${sourceSha256}:variant_temporal`), width: 1920, height: 1080, durationMs: 12000,
      frameTimestampsMs, audioStreamCopiedFromSource: true, audioSyncOffsetMs: 0.6,
      summaryNote: '+8.2 dB shadow SNR, 99% star retention, 9-frame temporal sky alignment.',
    },
  ];

  const observationSolution: ObservationSolution = {
    id: solutionId, sourceAssetId: assetId, sourceSha256, frameTimeSec: 4.2, revision: 1, status: 'calibrated',
    observedAtUTC: capturedAtUTC,
    observerLocation: { latDeg: 31.149, lonDeg: -3.972, placeName: 'Erg Chebbi, Morocco' },
    solver: { name: 'albedian-wcs-quad-geometric', version: '1.0.0', catalogRelease: 'Gaia DR3 / Hipparcos Bright Subset + JPL DE440' },
    wcs: {
      projection: 'TAN', crvalRaDeg: 83.8, crvalDecDeg: 4.0, crpixX: 960, crpixY: 540,
      scaleDegPerPx: 0.035, rotationDeg: 0, radialDistortionK1: -0.008, radialDistortionK2: 0.001,
      parity: -1, fovHorizontalDeg: 67.2, fovVerticalDeg: 37.8,
    },
    quality: {
      detectedCandidateCount: 28, matchedInlierCount: 20, heldOutInliers: 10,
      medianResidualPx: 0.42, p95ResidualPx: 0.89, medianResidualArcsec: 52.92, passedIndependentVerification: true,
    },
    findings: buildSaharaCatalogFindings(assetId, sourceSha256, solutionId),
    warnings: [],
  };

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 37.8,
    frustumShiftNdc: [0, 0], simulationEpochISO: capturedAtUTC, observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: {
      observedMarkers: true, catalogMatchedLabels: true, predictedEphemerides: false,
      constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false,
    },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_sahara_anchor_0', label: 'Original Moment — Erg Chebbi (04.2s)', createdAtMs: 4200,
    sourceAssetId: assetId, sourceTimeSec: 4.2, playbackWasPlaying: true, audioGain: 0.85,
    mediaVariantId: 'variant_natural', cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(initialSpatialView), viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_sahara_orion_2026',
    title: 'Sahara Night — Orion & Taurus Over Erg Chebbi',
    viewRevision: 1,
    machines: { media: 'ready', restoration: 'ready', calibration: 'calibrated', navigation: 'source', voice: 'listening' },
    sourceAsset,
    activeVariantId: 'variant_natural',
    compareWipePosition: 0.5,
    currentSourceTimeSec: 4.2,
    isPlaying: true,
    audioGain: 0.85,
    cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [graphOriginal, graphNatural, graphTemporal],
    processingAttempts,
    mediaVariants,
    observationSolution,
    selectedFindingId: null,
    focusedFindingId: null,
    spatialView: initialSpatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
    voiceActions: [],
    exportArtifacts: [],
    reducedMotion: false,
    lastStatusBanner: 'Calibrated (10 held-out stars, 0.42 px median residual).',
  });
}

/**
 * Returns a schema-validated `ObservationSession` representing an overcast/clipped dark clip
 * (`status: 'predicted_only'`, `calibration: 'predicted_only'`, `heldOutInliers: 2`, with only
 * `predicted` findings and zero false `catalog_matched` claims).
 */
export function createUnresolvedObservationSession(): ObservationSession {
  const assetId = 'asset_overcast_dusk_2026';
  const sourceSha256 = '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08';
  const capturedAtUTC = '2026-10-21T21:05:00Z';
  const solutionId = 'sol_overcast_predicted_v1';
  const frameTimestampsMs = buildFrameTimestampsMs(6000, 30);

  const sourceAsset: SourceAsset = {
    id: assetId, title: 'Overcast Horizon — Sparse Cloud Gap Recording', mediaKind: 'video', sha256: sourceSha256,
    uri: 'albedian://samples/overcast-dusk-2026-1080p30.mp4', width: 1920, height: 1080, durationMs: 6000,
    fpsNumerator: 30, fpsDenominator: 1, videoCodec: 'h264',
    audioStreams: [{ index: 0, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 6000, sha256: 'b4c5d6e7f8091a2b3c4d5e6f708192a3' }],
    capturedAtUTC,
    observerLocation: { latDeg: 37.7749, lonDeg: -122.4194, elevationM: 52, placeName: 'San Francisco, CA', uncertaintyMeters: 25 },
    privacyClass: 'consented_demo',
    immutable: true,
  };

  const graphOriginal: ProcessingGraph = {
    id: 'graph_overcast_original', sourceAssetId: assetId, sourceSha256,
    revisionSha256: deterministicHexDigest(`${sourceSha256}:original`),
    profileName: 'original', fidelityPolicy: DEFAULT_FIDELITY_POLICY,
    nodes: [
      { nodeId: 'decode_root', kind: 'source_decode', operationVersion: '1.0.0', inputNodeIds: [], parameters: { passthrough: true }, preservePointSources: true },
      { nodeId: 'mux_out', kind: 'encode_mux', operationVersion: '1.0.0', inputNodeIds: ['decode_root'], parameters: { copyAudio: true }, preservePointSources: true },
    ],
    createdAtUTC: capturedAtUTC,
  };

  const cacheKeys = computeNodeCacheKeys(graphOriginal);
  const attemptOriginal: ProcessingAttempt = {
    id: 'attempt_overcast_original', sourceAssetId: assetId, graphId: 'graph_overcast_original',
    label: 'Original Recording Passthrough', status: 'ready', cacheKey: cacheKeys.mux_out ?? graphOriginal.revisionSha256,
    startedAtUTC: '2026-10-21T21:05:01Z', completedAtUTC: '2026-10-21T21:05:02Z',
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
      catalogRef: {
        id: 'cat_pred_vega', provider: 'Hipparcos', releaseVersion: 'HIP-2', catalogSourceId: 'HIP 91262',
        commonName: 'Vega', bayerDesignation: 'Alpha Lyrae', constellation: 'Lyra', objectType: 'star',
        coordinateEpoch: 'J2000', raDeg: 279.2347, decDeg: 38.7837, apparentMagnitude: 0.03,
        citation: 'Hipparcos Catalog HIP 91262 (Predicted via timestamp/location)',
      },
      skyPosition: { raDeg: 279.2347, decDeg: 38.7837, uncertaintyArcsec: 1800, altDeg: 62.0, azDeg: 280.0 },
      evidence: {
        id: 'ev_pred_vega', evidenceClass: 'predicted',
        verificationMethod: 'Approximate topocentric sky model from timestamp and coarse location',
        sourceAssetSha256: sourceSha256,
        limitations: ['Only 2 faint candidate points detected in frame; minimum 6 held-out inliers required for WCS calibration.', 'Not matched to source pixels; direct pixel overlay disabled.'],
        verifiedByIndependentHoldout: false,
      },
    },
    {
      id: 'finding_pred_saturn', observationSolutionId: solutionId, evidenceClass: 'predicted',
      title: 'Saturn (Predicted Ephemeris)', subtitle: 'JPL Horizons DE440 · Predicted from Time & Location Only',
      catalogRef: {
        id: 'cat_pred_saturn', provider: 'JPL Horizons', releaseVersion: 'DE440', catalogSourceId: 'JPL 699',
        commonName: 'Saturn', constellation: 'Aquarius', objectType: 'planet', coordinateEpoch: capturedAtUTC,
        raDeg: 348.12, decDeg: -7.45, apparentMagnitude: 0.78, citation: 'NASA/JPL Horizons DE440 (Predicted via timestamp/location)',
      },
      skyPosition: { raDeg: 348.12, decDeg: -7.45, uncertaintyArcsec: 1800, altDeg: 41.2, azDeg: 162.5 },
      evidence: {
        id: 'ev_pred_saturn', evidenceClass: 'predicted',
        verificationMethod: 'JPL DE440 topocentric ephemeris from timestamp and coarse location',
        sourceAssetSha256: sourceSha256,
        limitations: ['Cloud cover prevented astrometric plate solve; position is shown in separate predicted-sky mode only.'],
        verifiedByIndependentHoldout: false,
      },
    },
  ];

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 40,
    frustumShiftNdc: [0, 0], simulationEpochISO: capturedAtUTC, observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: {
      observedMarkers: false, catalogMatchedLabels: false, predictedEphemerides: true,
      constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false,
    },
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
      quality: {
        detectedCandidateCount: 2, matchedInlierCount: 2, heldOutInliers: 2,
        medianResidualPx: 8.4, p95ResidualPx: 14.2, medianResidualArcsec: 1058.4, passedIndependentVerification: false,
      },
      findings: predictedFindings,
      warnings: ['Insufficient star detections (2 found, >= 6 held-out inliers required) for verified WCS registration. Showing predicted sky separately from source pixels.'],
    },
    selectedFindingId: null, focusedFindingId: null, spatialView: initialSpatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
    voiceActions: [], exportArtifacts: [], reducedMotion: false,
    lastStatusBanner: '2 point detections insufficient for blind plate solve. Sky predicted separately from recorded timestamp and location.',
  });
}
