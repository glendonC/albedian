import {
  ActionContext,
  ActionContextSchema,
  ActionOutcome,
  ActionOutcomeSchema,
  AlbedianAction,
  AlbedianActionSchema,
  CameraCheckpoint,
  CameraCheckpointSchema,
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
  VoiceAction,
  VoiceActionSchema,
  computeNodeCacheKeys,
  deterministicHexDigest,
  validateProcessingGraphDAG,
} from '@albedian/contracts';
import {
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
} from '@albedian/evidence';

/**
 * ============================================================================
 * @albedian/observation
 * ObservationSession State Machine Reducer, Reversible Checkpoint Stack,
 * Golden Sahara Session Factory & Context-Bound Voice Action Dispatcher
 * ============================================================================
 */

export const MAX_POINTER_RECENCY_MS = 10_000;
export const MAX_ALLOWED_MEDIA_DRIFT_MS = 42.0;

/**
 * Computes ICRS heliocentric/barycentric Cartesian coordinates in parsecs
 * from J2000 Right Ascension (deg), Declination (deg), and distance (pc).
 */
export function computeCartesianParsecICRS(
  raDeg: number,
  decDeg: number,
  distancePc: number
): [number, number, number] {
  const raRad = (raDeg * Math.PI) / 180;
  const decRad = (decDeg * Math.PI) / 180;
  const x = Number((distancePc * Math.cos(decRad) * Math.cos(raRad)).toFixed(6));
  const y = Number((distancePc * Math.cos(decRad) * Math.sin(raRad)).toFixed(6));
  const z = Number((distancePc * Math.sin(decRad)).toFixed(6));
  return [x, y, z];
}

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
  distance: {
    lower: number;
    median: number;
    upper: number;
  };
  xPx: number;
  yPx: number;
  snr: number;
  flux: number;
  pixelResidualPx: number;
}

const SAHARA_CATALOG_STARS: GoldenStarSpec[] = [
  {
    id: 'finding_betelgeuse',
    commonName: 'Betelgeuse',
    bayerDesignation: 'Alpha Orionis',
    hipId: 'HIP 27989',
    constellation: 'Orion',
    raDeg: 88.7929,
    decDeg: 7.4071,
    mag: 0.42,
    colorIndexBpRp: 2.05,
    spectralType: 'M1-M2 Ia-ab',
    properMotionRaMasYr: 27.54,
    properMotionDecMasYr: 11.3,
    radialVelocityKmS: 21.91,
    parallaxMas: 5.95,
    parallaxErrorMas: 0.45,
    distance: { lower: 152, median: 168, upper: 190 },
    xPx: 817.3,
    yPx: 442.6,
    snr: 84.2,
    flux: 18420,
    pixelResidualPx: 0.34,
  },
  {
    id: 'finding_rigel',
    commonName: 'Rigel',
    bayerDesignation: 'Beta Orionis',
    hipId: 'HIP 24436',
    constellation: 'Orion',
    raDeg: 78.6345,
    decDeg: -8.2016,
    mag: 0.13,
    colorIndexBpRp: -0.03,
    spectralType: 'B8 Ia',
    properMotionRaMasYr: 1.31,
    properMotionDecMasYr: 0.5,
    radialVelocityKmS: 17.8,
    parallaxMas: 3.78,
    parallaxErrorMas: 0.34,
    distance: { lower: 240, median: 264.5, upper: 290 },
    xPx: 1107.6,
    yPx: 888.6,
    snr: 92.5,
    flux: 22950,
    pixelResidualPx: 0.39,
  },
  {
    id: 'finding_bellatrix',
    commonName: 'Bellatrix',
    bayerDesignation: 'Gamma Orionis',
    hipId: 'HIP 25336',
    constellation: 'Orion',
    raDeg: 81.2828,
    decDeg: 6.3497,
    mag: 1.64,
    colorIndexBpRp: -0.22,
    spectralType: 'B2 III',
    properMotionRaMasYr: -8.11,
    properMotionDecMasYr: -12.88,
    radialVelocityKmS: 18.2,
    parallaxMas: 12.89,
    parallaxErrorMas: 0.42,
    distance: { lower: 75, median: 77.5, upper: 80 },
    xPx: 1031.9,
    yPx: 472.9,
    snr: 54.1,
    flux: 8120,
    pixelResidualPx: 0.41,
  },
  {
    id: 'finding_alnilam',
    commonName: 'Alnilam',
    bayerDesignation: 'Epsilon Orionis',
    hipId: 'HIP 26311',
    constellation: 'Orion',
    raDeg: 84.0534,
    decDeg: -1.2019,
    mag: 1.69,
    colorIndexBpRp: -0.18,
    spectralType: 'B0 Ia',
    properMotionRaMasYr: 1.44,
    properMotionDecMasYr: -0.78,
    radialVelocityKmS: 27.3,
    parallaxMas: 2.43,
    parallaxErrorMas: 0.25,
    distance: { lower: 380, median: 412, upper: 460 },
    xPx: 952.8,
    yPx: 688.6,
    snr: 51.8,
    flux: 7840,
    pixelResidualPx: 0.29,
  },
  {
    id: 'finding_alnitak',
    commonName: 'Alnitak',
    bayerDesignation: 'Zeta Orionis',
    hipId: 'HIP 26727',
    constellation: 'Orion',
    raDeg: 85.1897,
    decDeg: -1.9426,
    mag: 1.77,
    colorIndexBpRp: -0.2,
    spectralType: 'O9.5 Iab',
    properMotionRaMasYr: 3.19,
    properMotionDecMasYr: 2.03,
    radialVelocityKmS: 18.5,
    parallaxMas: 4.43,
    parallaxErrorMas: 0.3,
    distance: { lower: 215, median: 226, upper: 240 },
    xPx: 920.3,
    yPx: 709.8,
    snr: 49.4,
    flux: 7310,
    pixelResidualPx: 0.44,
  },
  {
    id: 'finding_mintaka',
    commonName: 'Mintaka',
    bayerDesignation: 'Delta Orionis',
    hipId: 'HIP 25930',
    constellation: 'Orion',
    raDeg: 83.0017,
    decDeg: -0.2991,
    mag: 2.23,
    colorIndexBpRp: -0.21,
    spectralType: 'O9.5 II',
    properMotionRaMasYr: 0.64,
    properMotionDecMasYr: -0.69,
    radialVelocityKmS: 16.0,
    parallaxMas: 2.63,
    parallaxErrorMas: 0.22,
    distance: { lower: 360, median: 380, upper: 405 },
    xPx: 982.8,
    yPx: 662.8,
    snr: 38.6,
    flux: 5120,
    pixelResidualPx: 0.46,
  },
  {
    id: 'finding_saiph',
    commonName: 'Saiph',
    bayerDesignation: 'Kappa Orionis',
    hipId: 'HIP 27366',
    constellation: 'Orion',
    raDeg: 86.9391,
    decDeg: -9.6696,
    mag: 2.06,
    colorIndexBpRp: -0.17,
    spectralType: 'B0.5 Ia',
    properMotionRaMasYr: 1.46,
    properMotionDecMasYr: -1.28,
    radialVelocityKmS: 20.7,
    parallaxMas: 5.04,
    parallaxErrorMas: 0.28,
    distance: { lower: 185, median: 198, upper: 215 },
    xPx: 870.3,
    yPx: 930.5,
    snr: 42.1,
    flux: 5980,
    pixelResidualPx: 0.51,
  },
  {
    id: 'finding_aldebaran',
    commonName: 'Aldebaran',
    bayerDesignation: 'Alpha Tauri',
    hipId: 'HIP 21421',
    constellation: 'Taurus',
    raDeg: 68.9802,
    decDeg: 16.5093,
    mag: 0.86,
    colorIndexBpRp: 1.54,
    spectralType: 'K5+ III',
    properMotionRaMasYr: 63.45,
    properMotionDecMasYr: -188.94,
    radialVelocityKmS: 54.26,
    parallaxMas: 48.94,
    parallaxErrorMas: 0.45,
    distance: { lower: 20.0, median: 20.4, upper: 20.9 },
    xPx: 1383.4,
    yPx: 182.6,
    snr: 71.3,
    flux: 14190,
    pixelResidualPx: 0.37,
  },
  {
    id: 'finding_sirius',
    commonName: 'Sirius',
    bayerDesignation: 'Alpha Canis Majoris',
    hipId: 'HIP 32349',
    constellation: 'Canis Major',
    raDeg: 101.2872,
    decDeg: -16.7161,
    mag: -1.46,
    colorIndexBpRp: 0.0,
    spectralType: 'A0mA1 Va',
    properMotionRaMasYr: -546.01,
    properMotionDecMasYr: -1223.07,
    radialVelocityKmS: -5.5,
    parallaxMas: 379.21,
    parallaxErrorMas: 1.58,
    distance: { lower: 2.63, median: 2.64, upper: 2.65 },
    xPx: 460.4,
    yPx: 1054.2,
    snr: 98.7,
    flux: 42800,
    pixelResidualPx: 0.48,
  },
  {
    id: 'finding_procyon',
    commonName: 'Procyon',
    bayerDesignation: 'Alpha Canis Minoris',
    hipId: 'HIP 37279',
    constellation: 'Canis Minor',
    raDeg: 114.8255,
    decDeg: 5.225,
    mag: 0.34,
    colorIndexBpRp: 0.42,
    spectralType: 'F5 IV-V',
    properMotionRaMasYr: -714.59,
    properMotionDecMasYr: -1036.8,
    radialVelocityKmS: -3.2,
    parallaxMas: 284.56,
    parallaxErrorMas: 1.26,
    distance: { lower: 3.5, median: 3.51, upper: 3.52 },
    xPx: 73.6,
    yPx: 505.0,
    snr: 86.4,
    flux: 19650,
    pixelResidualPx: 0.89,
  },
];

function buildSaharaFrameTimestampsMs(durationMs: number, fps: number): number[] {
  const frameCount = Math.round((durationMs / 1000) * fps);
  const timestamps: number[] = [];
  for (let i = 0; i < frameCount; i++) {
    timestamps.push(Number(((i * 1000) / fps).toFixed(3)));
  }
  return timestamps;
}

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
    const cartesianParsecICRS = computeCartesianParsecICRS(
      star.raDeg,
      star.decDeg,
      star.distance.median
    );

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
      cartesianParsecICRS,
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
      sourceDetection: undefined,
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
      skyPosition: {
        raDeg: 76.42,
        decDeg: 22.18,
        uncertaintyArcsec: 1.2,
        altDeg: 54.1,
        azDeg: 102.3,
      },
      inferredDistancePc: undefined,
      cartesianParsecICRS: undefined,
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
      sourceDetection: undefined,
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
      skyPosition: {
        raDeg: 54.85,
        decDeg: 19.12,
        uncertaintyArcsec: 1.5,
        altDeg: 61.4,
        azDeg: 95.8,
      },
      inferredDistancePc: undefined,
      cartesianParsecICRS: undefined,
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
        assetId,
        frameTimeSec: 4.2,
        xPx: 1142.4,
        yPx: 618.7,
        flux: 412.0,
        snr: 4.6,
        fwhmPx: 2.8,
        centroidUncertaintyPx: 0.52,
      },
      catalogRef: undefined,
      skyPosition: {
        raDeg: 84.52,
        decDeg: 1.84,
        uncertaintyArcsec: 68.0,
        altDeg: 47.2,
        azDeg: 118.4,
      },
      inferredDistancePc: undefined,
      cartesianParsecICRS: undefined,
      evidence: {
        id: 'ev_finding_obs_101',
        evidenceClass: 'observed',
        verificationMethod: 'Direct source pixel aperture photometry (SNR 4.6 >= 3.5 threshold)',
        sourceAssetSha256: sourceSha256,
        wcsSolutionId: solutionId,
        limitations: [
          'Measured directly in source recording pixels; lacks an unambiguous Gaia DR3 catalog match or parallax distance.',
        ],
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
        assetId,
        frameTimeSec: 4.2,
        xPx: 784.1,
        yPx: 492.3,
        flux: 318.5,
        snr: 3.9,
        fwhmPx: 3.1,
        centroidUncertaintyPx: 0.64,
      },
      catalogRef: undefined,
      skyPosition: {
        raDeg: 81.94,
        decDeg: 5.71,
        uncertaintyArcsec: 74.0,
        altDeg: 51.0,
        azDeg: 114.2,
      },
      inferredDistancePc: undefined,
      cartesianParsecICRS: undefined,
      evidence: {
        id: 'ev_finding_obs_102',
        evidenceClass: 'observed',
        verificationMethod: 'Direct source pixel aperture photometry (SNR 3.9 >= 3.5 threshold)',
        sourceAssetSha256: sourceSha256,
        wcsSolutionId: solutionId,
        limitations: [
          'Measured directly in source recording pixels; no catalog identification or distance constraint.',
        ],
        verifiedByIndependentHoldout: false,
      },
    },
  ];

  return [...matchedFindings, ...predictedFindings, ...observedFindings];
}

/**
 * Creates a snapshot `CameraCheckpoint` from the current `ObservationSession`.
 */
export function createCameraCheckpoint(
  session: ObservationSession,
  label = 'Saved Observation Moment'
): CameraCheckpoint {
  return CameraCheckpointSchema.parse({
    id: `chk_${session.id}_rev_${session.viewRevision}_${session.navigationHistory.checkpoints.length}`,
    label,
    createdAtMs: Math.round(session.currentSourceTimeSec * 1000),
    sourceAssetId: session.sourceAsset.id,
    sourceTimeSec: session.currentSourceTimeSec,
    playbackWasPlaying: session.isPlaying,
    audioGain: session.audioGain,
    mediaVariantId: session.activeVariantId,
    cropTransform: { ...session.cropTransform },
    selectedFindingId: session.selectedFindingId,
    navigationState: session.machines.navigation,
    spatialView: structuredClone(session.spatialView),
    viewRevision: session.viewRevision,
  });
}

/**
 * Transitions one of the 4+1 orthogonal state machines without mutating the other four.
 */
export function transitionOrthogonalMachine<K extends keyof ObservationSession['machines']>(
  session: ObservationSession,
  machine: K,
  nextState: ObservationSession['machines'][K]
): ObservationSession {
  return {
    ...session,
    machines: {
      ...session.machines,
      [machine]: nextState,
    },
  };
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
  const frameTimestampsMs = buildSaharaFrameTimestampsMs(12000, 30);

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
      {
        index: 0,
        codec: 'aac',
        sampleRateHz: 48000,
        channels: 2,
        startPtsMs: 0,
        durationMs: 12000,
        sha256: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
      },
    ],
    capturedAtUTC,
    observerLocation: {
      latDeg: 31.149,
      lonDeg: -3.972,
      elevationM: 710,
      placeName: 'Erg Chebbi, Morocco',
      uncertaintyMeters: 15,
    },
    cameraMetadata: {
      make: 'Sony',
      model: 'A7S III',
      focalLengthMm: 24,
      estimatedFovDeg: 67.2,
      exposureTimeSec: 0.0333,
      iso: 12800,
    },
    privacyClass: 'consented_demo',
    immutable: true,
  };

  const graphOriginal: ProcessingGraph = {
    id: 'graph_original',
    sourceAssetId: assetId,
    sourceSha256,
    revisionSha256: deterministicHexDigest(`${sourceSha256}:original`),
    profileName: 'original',
    fidelityPolicy: {
      noSyntheticSceneContent: true,
      allowSyntheticSky: false,
      preserveOriginalAudio: true,
      preserveFacesAndPeople: true,
      protectObservedStarPoints: true,
    },
    nodes: [
      {
        nodeId: 'decode_root',
        kind: 'source_decode',
        operationVersion: '1.0.0',
        inputNodeIds: [],
        parameters: { colorSpace: 'rec709', passthrough: true },
        preservePointSources: true,
      },
      {
        nodeId: 'mux_out',
        kind: 'encode_mux',
        operationVersion: '1.0.0',
        inputNodeIds: ['decode_root'],
        parameters: { copyAudio: true, crf: 14 },
        preservePointSources: true,
      },
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
    fidelityPolicy: {
      noSyntheticSceneContent: true,
      allowSyntheticSky: false,
      preserveOriginalAudio: true,
      preserveFacesAndPeople: true,
      protectObservedStarPoints: true,
    },
    nodes: [
      {
        nodeId: 'decode_root',
        kind: 'source_decode',
        operationVersion: '1.0.0',
        inputNodeIds: [],
        parameters: { colorSpace: 'linear_rec709' },
        preservePointSources: true,
      },
      {
        nodeId: 'mask_foreground',
        kind: 'region_mask',
        operationVersion: '1.1.0',
        inputNodeIds: ['decode_root'],
        parameters: { protectPeopleAndDunes: true, featherPx: 6 },
        maskId: 'mask_sahara_horizon_people',
        preservePointSources: true,
      },
      {
        nodeId: 'tone_curve',
        kind: 'tone_transfer',
        operationVersion: '1.2.0',
        inputNodeIds: ['decode_root', 'mask_foreground'],
        parameters: { shadowLiftEv: 1.35, highlightRollOff: 0.85 },
        preservePointSources: true,
      },
      {
        nodeId: 'chroma_denoise',
        kind: 'spatial_denoise',
        operationVersion: '1.2.0',
        inputNodeIds: ['tone_curve'],
        parameters: { lumaStrength: 0.32, chromaStrength: 0.68 },
        preservePointSources: true,
      },
      {
        nodeId: 'star_guard',
        kind: 'star_protect_merge',
        operationVersion: '1.1.0',
        inputNodeIds: ['decode_root', 'chroma_denoise'],
        parameters: { minPointSnr: 3.2, psfRadiusPx: 3.0 },
        preservePointSources: true,
      },
      {
        nodeId: 'mux_out',
        kind: 'encode_mux',
        operationVersion: '1.0.0',
        inputNodeIds: ['star_guard'],
        parameters: { copyAudio: true, crf: 15 },
        preservePointSources: true,
      },
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
    fidelityPolicy: {
      noSyntheticSceneContent: true,
      allowSyntheticSky: false,
      preserveOriginalAudio: true,
      preserveFacesAndPeople: true,
      protectObservedStarPoints: true,
    },
    nodes: [
      {
        nodeId: 'decode_root',
        kind: 'source_decode',
        operationVersion: '1.0.0',
        inputNodeIds: [],
        parameters: { colorSpace: 'linear_rec709' },
        preservePointSources: true,
      },
      {
        nodeId: 'temporal_align',
        kind: 'temporal_stack',
        operationVersion: '1.3.0',
        inputNodeIds: ['decode_root'],
        parameters: { windowFrames: 9, motionCompensated: true, rejectOutlierSigma: 2.8 },
        maskId: 'mask_sahara_horizon_people',
        preservePointSources: true,
      },
      {
        nodeId: 'tone_curve',
        kind: 'tone_transfer',
        operationVersion: '1.2.0',
        inputNodeIds: ['temporal_align'],
        parameters: { shadowLiftEv: 1.65, highlightRollOff: 0.88 },
        preservePointSources: true,
      },
      {
        nodeId: 'star_guard',
        kind: 'star_protect_merge',
        operationVersion: '1.1.0',
        inputNodeIds: ['decode_root', 'tone_curve'],
        parameters: { minPointSnr: 3.0, psfRadiusPx: 2.8 },
        preservePointSources: true,
      },
      {
        nodeId: 'mux_out',
        kind: 'encode_mux',
        operationVersion: '1.0.0',
        inputNodeIds: ['star_guard'],
        parameters: { copyAudio: true, crf: 15 },
        preservePointSources: true,
      },
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
      id: 'attempt_original',
      sourceAssetId: assetId,
      graphId: 'graph_original',
      label: 'Original Recording Passthrough',
      status: 'ready',
      cacheKey: originalCacheKeys.mux_out ?? graphOriginal.revisionSha256,
      startedAtUTC: '2026-10-19T22:14:01Z',
      completedAtUTC: '2026-10-19T22:14:02Z',
      elapsedMs: 120,
      cpuMs: 95,
      gpuMs: 0,
      peakMemoryBytes: 67108864,
      outputVariantId: 'variant_original',
      metrics: {
        evaluationMethod: 'identity_reference_check',
        hasPairedReference: true,
        shadowSnrGainDb: 0,
        estimatedNoiseReductionPct: 0,
        pointSourceRetentionRatio: 1.0,
        falsePointArtifactCount: 0,
        faceRegionStabilityScore: 1.0,
        temporalFlickerIndex: 0.08,
        audioSyncOffsetMs: 0,
        validityNotes: ['Immutable original source stream.'],
      },
    },
    {
      id: 'attempt_natural',
      sourceAssetId: assetId,
      graphId: 'graph_natural',
      parentAttemptId: 'attempt_original',
      label: 'Natural Tone + Star-Protected Denoise',
      status: 'ready',
      cacheKey: naturalCacheKeys.mux_out ?? graphNatural.revisionSha256,
      startedAtUTC: '2026-10-19T22:14:03Z',
      completedAtUTC: '2026-10-19T22:14:05Z',
      elapsedMs: 1840,
      cpuMs: 620,
      gpuMs: 1120,
      peakMemoryBytes: 268435456,
      outputVariantId: 'variant_natural',
      metrics: {
        evaluationMethod: 'no_reference_sky_patch_snr_and_psf_guard',
        hasPairedReference: false,
        shadowSnrGainDb: 5.4,
        estimatedNoiseReductionPct: 58,
        pointSourceRetentionRatio: 0.98,
        falsePointArtifactCount: 0,
        faceRegionStabilityScore: 0.99,
        temporalFlickerIndex: 0.04,
        audioSyncOffsetMs: 0.4,
        validityNotes: [
          'Foreground silhouette and people mask preserved.',
          'Zero synthetic stars introduced.',
        ],
      },
    },
    {
      id: 'attempt_temporal',
      sourceAssetId: assetId,
      graphId: 'graph_temporal',
      parentAttemptId: 'attempt_natural',
      label: 'Motion-Compensated Temporal Stack',
      status: 'ready',
      cacheKey: temporalCacheKeys.mux_out ?? graphTemporal.revisionSha256,
      startedAtUTC: '2026-10-19T22:14:06Z',
      completedAtUTC: '2026-10-19T22:14:09Z',
      elapsedMs: 3120,
      cpuMs: 940,
      gpuMs: 2050,
      peakMemoryBytes: 536870912,
      outputVariantId: 'variant_temporal',
      metrics: {
        evaluationMethod: 'temporal_window_sky_patch_snr_and_psf_guard',
        hasPairedReference: false,
        shadowSnrGainDb: 8.2,
        estimatedNoiseReductionPct: 76,
        pointSourceRetentionRatio: 0.99,
        falsePointArtifactCount: 0,
        faceRegionStabilityScore: 0.98,
        temporalFlickerIndex: 0.02,
        audioSyncOffsetMs: 0.6,
        validityNotes: [
          '9-frame motion-compensated sky stack with foreground rejection.',
          '99% point-source retention with 0 false star artifacts.',
        ],
      },
    },
  ];

  const mediaVariants: MediaVariant[] = [
    {
      id: 'variant_original',
      sourceAssetId: assetId,
      attemptId: 'attempt_original',
      graphId: 'graph_original',
      label: 'Original Recording',
      uri: 'albedian://samples/sahara-orion-2026-1080p30.mp4',
      sha256: sourceSha256,
      width: 1920,
      height: 1080,
      durationMs: 12000,
      frameTimestampsMs,
      audioStreamCopiedFromSource: true,
      audioSyncOffsetMs: 0,
      summaryNote: 'Unmodified camera recording with original desert wind & conversation audio.',
    },
    {
      id: 'variant_natural',
      sourceAssetId: assetId,
      attemptId: 'attempt_natural',
      graphId: 'graph_natural',
      label: 'Natural Tone + Star-Protected Denoise',
      uri: 'albedian://variants/sahara-orion-2026-natural.mp4',
      sha256: deterministicHexDigest(`${sourceSha256}:variant_natural`),
      width: 1920,
      height: 1080,
      durationMs: 12000,
      frameTimestampsMs,
      audioStreamCopiedFromSource: true,
      audioSyncOffsetMs: 0.4,
      summaryNote: '+5.4 dB shadow SNR, 98% star retention, foreground people protected.',
    },
    {
      id: 'variant_temporal',
      sourceAssetId: assetId,
      attemptId: 'attempt_temporal',
      graphId: 'graph_temporal',
      label: 'Motion-Compensated Temporal Stack',
      uri: 'albedian://variants/sahara-orion-2026-temporal.mp4',
      sha256: deterministicHexDigest(`${sourceSha256}:variant_temporal`),
      width: 1920,
      height: 1080,
      durationMs: 12000,
      frameTimestampsMs,
      audioStreamCopiedFromSource: true,
      audioSyncOffsetMs: 0.6,
      summaryNote: '+8.2 dB shadow SNR, 99% star retention, 9-frame temporal sky alignment.',
    },
  ];

  const findings = buildSaharaCatalogFindings(assetId, sourceSha256, solutionId);

  const observationSolution: ObservationSolution = {
    id: solutionId,
    sourceAssetId: assetId,
    sourceSha256,
    frameTimeSec: 4.2,
    revision: 1,
    status: 'calibrated',
    observedAtUTC: capturedAtUTC,
    observerLocation: {
      latDeg: 31.149,
      lonDeg: -3.972,
      placeName: 'Erg Chebbi, Morocco',
    },
    solver: {
      name: 'albedian-wcs-quad-geometric',
      version: '1.0.0',
      catalogRelease: 'Gaia DR3 / Hipparcos Bright Subset + JPL DE440',
    },
    wcs: {
      projection: 'TAN',
      crvalRaDeg: 83.8,
      crvalDecDeg: 4.0,
      crpixX: 960,
      crpixY: 540,
      scaleDegPerPx: 0.035,
      rotationDeg: 0,
      radialDistortionK1: -0.008,
      radialDistortionK2: 0.001,
      parity: -1,
      fovHorizontalDeg: 67.2,
      fovVerticalDeg: 37.8,
    },
    quality: {
      detectedCandidateCount: 28,
      matchedInlierCount: 20,
      heldOutInliers: 10,
      medianResidualPx: 0.42,
      p95ResidualPx: 0.89,
      medianResidualArcsec: 52.92,
      passedIndependentVerification: true,
    },
    findings,
    warnings: [],
  };

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d',
    transitionPhase: 'at_source',
    transitionProgress: 0,
    observerOriginParsec: [0, 0, 0],
    cameraQuaternion: [0, 0, 0, 1],
    fovVerticalDeg: 37.8,
    frustumShiftNdc: [0, 0],
    lockedAnchorNdc: undefined,
    simulationEpochISO: capturedAtUTC,
    observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: {
      observedMarkers: true,
      catalogMatchedLabels: true,
      predictedEphemerides: false,
      constellationLines: false,
      earthDirectionVector: false,
      residualVectors: false,
      sightlineCone: false,
    },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_sahara_anchor_0',
    label: 'Original Moment — Erg Chebbi (04.2s)',
    createdAtMs: 4200,
    sourceAssetId: assetId,
    sourceTimeSec: 4.2,
    playbackWasPlaying: true,
    audioGain: 0.85,
    mediaVariantId: 'variant_natural',
    cropTransform: {
      zoom: 1,
      panX: 0,
      panY: 0,
    },
    selectedFindingId: null,
    navigationState: 'source',
    spatialView: structuredClone(initialSpatialView),
    viewRevision: 1,
  };

  const session: ObservationSession = {
    id: 'session_sahara_orion_2026',
    title: 'Sahara Night — Orion & Taurus Over Erg Chebbi',
    viewRevision: 1,
    machines: {
      media: 'ready',
      restoration: 'ready',
      calibration: 'calibrated',
      navigation: 'source',
      voice: 'listening',
    },
    sourceAsset,
    activeVariantId: 'variant_natural',
    compareVariantId: undefined,
    compareWipePosition: 0.5,
    currentSourceTimeSec: 4.2,
    isPlaying: true,
    audioGain: 0.85,
    cropTransform: {
      zoom: 1,
      panX: 0,
      panY: 0,
    },
    processingGraphs: [graphOriginal, graphNatural, graphTemporal],
    processingAttempts,
    mediaVariants,
    observationSolution,
    selectedFindingId: null,
    focusedFindingId: null,
    spatialView: initialSpatialView,
    navigationHistory: {
      anchorMomentCheckpoint: anchorCheckpoint,
      checkpoints: [anchorCheckpoint],
      cursorIndex: 0,
    },
    voiceActions: [],
    exportArtifacts: [],
    reducedMotion: false,
    lastStatusBanner: 'Calibrated (10 held-out stars, 0.42 px median residual).',
  };

  return ObservationSessionSchema.parse(session);
}

/**
 * Returns a schema-validated `ObservationSession` representing an overcast/clipped dark clip
 * where blind astrometric plate solving failed held-out verification (`status: 'predicted_only'`,
 * `calibration: 'predicted_only'`, `heldOutInliers: 2`, with only `predicted` findings and zero
 * false `catalog_matched` claims).
 */
export function createUnresolvedObservationSession(): ObservationSession {
  const assetId = 'asset_overcast_dusk_2026';
  const sourceSha256 = '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08';
  const capturedAtUTC = '2026-10-21T21:05:00Z';
  const solutionId = 'sol_overcast_predicted_v1';
  const frameTimestampsMs = buildSaharaFrameTimestampsMs(6000, 30);

  const sourceAsset: SourceAsset = {
    id: assetId,
    title: 'Overcast Horizon — Sparse Cloud Gap Recording',
    mediaKind: 'video',
    sha256: sourceSha256,
    uri: 'albedian://samples/overcast-dusk-2026-1080p30.mp4',
    width: 1920,
    height: 1080,
    durationMs: 6000,
    fpsNumerator: 30,
    fpsDenominator: 1,
    videoCodec: 'h264',
    audioStreams: [
      {
        index: 0,
        codec: 'aac',
        sampleRateHz: 48000,
        channels: 2,
        startPtsMs: 0,
        durationMs: 6000,
        sha256: 'b4c5d6e7f8091a2b3c4d5e6f708192a3',
      },
    ],
    capturedAtUTC,
    observerLocation: {
      latDeg: 37.7749,
      lonDeg: -122.4194,
      elevationM: 52,
      placeName: 'San Francisco, CA',
      uncertaintyMeters: 25,
    },
    privacyClass: 'consented_demo',
    immutable: true,
  };

  const graphOriginal: ProcessingGraph = {
    id: 'graph_overcast_original',
    sourceAssetId: assetId,
    sourceSha256,
    revisionSha256: deterministicHexDigest(`${sourceSha256}:original`),
    profileName: 'original',
    fidelityPolicy: {
      noSyntheticSceneContent: true,
      allowSyntheticSky: false,
      preserveOriginalAudio: true,
      preserveFacesAndPeople: true,
      protectObservedStarPoints: true,
    },
    nodes: [
      {
        nodeId: 'decode_root',
        kind: 'source_decode',
        operationVersion: '1.0.0',
        inputNodeIds: [],
        parameters: { passthrough: true },
        preservePointSources: true,
      },
      {
        nodeId: 'mux_out',
        kind: 'encode_mux',
        operationVersion: '1.0.0',
        inputNodeIds: ['decode_root'],
        parameters: { copyAudio: true },
        preservePointSources: true,
      },
    ],
    createdAtUTC: capturedAtUTC,
  };

  const cacheKeys = computeNodeCacheKeys(graphOriginal);

  const attemptOriginal: ProcessingAttempt = {
    id: 'attempt_overcast_original',
    sourceAssetId: assetId,
    graphId: 'graph_overcast_original',
    label: 'Original Recording Passthrough',
    status: 'ready',
    cacheKey: cacheKeys.mux_out ?? graphOriginal.revisionSha256,
    startedAtUTC: '2026-10-21T21:05:01Z',
    completedAtUTC: '2026-10-21T21:05:02Z',
    elapsedMs: 95,
    cpuMs: 80,
    gpuMs: 0,
    peakMemoryBytes: 33554432,
    outputVariantId: 'variant_overcast_original',
  };

  const variantOriginal: MediaVariant = {
    id: 'variant_overcast_original',
    sourceAssetId: assetId,
    attemptId: 'attempt_overcast_original',
    graphId: 'graph_overcast_original',
    label: 'Original Recording',
    uri: 'albedian://samples/overcast-dusk-2026-1080p30.mp4',
    sha256: sourceSha256,
    width: 1920,
    height: 1080,
    durationMs: 6000,
    frameTimestampsMs,
    audioStreamCopiedFromSource: true,
    audioSyncOffsetMs: 0,
    summaryNote: 'Original cloud-obstructed recording.',
  };

  const predictedFindings: CelestialFinding[] = [
    {
      id: 'finding_pred_vega',
      observationSolutionId: solutionId,
      evidenceClass: 'predicted',
      title: 'Vega (Predicted Overhead)',
      subtitle: 'Alpha Lyrae · Predicted from Time & Location Only',
      sourceDetection: undefined,
      catalogRef: {
        id: 'cat_pred_vega',
        provider: 'Hipparcos',
        releaseVersion: 'HIP-2',
        catalogSourceId: 'HIP 91262',
        commonName: 'Vega',
        bayerDesignation: 'Alpha Lyrae',
        constellation: 'Lyra',
        objectType: 'star',
        coordinateEpoch: 'J2000',
        raDeg: 279.2347,
        decDeg: 38.7837,
        apparentMagnitude: 0.03,
        citation: 'Hipparcos Catalog HIP 91262 (Predicted via timestamp/location)',
      },
      skyPosition: {
        raDeg: 279.2347,
        decDeg: 38.7837,
        uncertaintyArcsec: 1800,
        altDeg: 62.0,
        azDeg: 280.0,
      },
      inferredDistancePc: undefined,
      cartesianParsecICRS: undefined,
      evidence: {
        id: 'ev_pred_vega',
        evidenceClass: 'predicted',
        verificationMethod: 'Approximate topocentric sky model from timestamp and coarse location',
        sourceAssetSha256: sourceSha256,
        limitations: [
          'Only 2 faint candidate points detected in frame; minimum 6 held-out inliers required for WCS calibration.',
          'Not matched to source pixels; direct pixel overlay disabled.',
        ],
        verifiedByIndependentHoldout: false,
      },
    },
    {
      id: 'finding_pred_saturn',
      observationSolutionId: solutionId,
      evidenceClass: 'predicted',
      title: 'Saturn (Predicted Ephemeris)',
      subtitle: 'JPL Horizons DE440 · Predicted from Time & Location Only',
      sourceDetection: undefined,
      catalogRef: {
        id: 'cat_pred_saturn',
        provider: 'JPL Horizons',
        releaseVersion: 'DE440',
        catalogSourceId: 'JPL 699',
        commonName: 'Saturn',
        constellation: 'Aquarius',
        objectType: 'planet',
        coordinateEpoch: capturedAtUTC,
        raDeg: 348.12,
        decDeg: -7.45,
        apparentMagnitude: 0.78,
        citation: 'NASA/JPL Horizons DE440 (Predicted via timestamp/location)',
      },
      skyPosition: {
        raDeg: 348.12,
        decDeg: -7.45,
        uncertaintyArcsec: 1800,
        altDeg: 41.2,
        azDeg: 162.5,
      },
      inferredDistancePc: undefined,
      cartesianParsecICRS: undefined,
      evidence: {
        id: 'ev_pred_saturn',
        evidenceClass: 'predicted',
        verificationMethod: 'JPL DE440 topocentric ephemeris from timestamp and coarse location',
        sourceAssetSha256: sourceSha256,
        limitations: [
          'Cloud cover prevented astrometric plate solve; position is shown in separate predicted-sky mode only.',
        ],
        verifiedByIndependentHoldout: false,
      },
    },
  ];

  const observationSolution: ObservationSolution = {
    id: solutionId,
    sourceAssetId: assetId,
    sourceSha256,
    frameTimeSec: 1.5,
    revision: 1,
    status: 'predicted_only',
    observedAtUTC: capturedAtUTC,
    observerLocation: {
      latDeg: 37.7749,
      lonDeg: -122.4194,
      placeName: 'San Francisco, CA',
    },
    solver: {
      name: 'albedian-wcs-quad-geometric',
      version: '1.0.0',
      catalogRelease: 'Gaia DR3 / Hipparcos Bright Subset + JPL DE440',
    },
    wcs: undefined,
    quality: {
      detectedCandidateCount: 2,
      matchedInlierCount: 2,
      heldOutInliers: 2,
      medianResidualPx: 8.4,
      p95ResidualPx: 14.2,
      medianResidualArcsec: 1058.4,
      passedIndependentVerification: false,
    },
    findings: predictedFindings,
    warnings: [
      'Insufficient star detections (2 found, >= 6 held-out inliers required) for verified WCS registration. Showing predicted sky separately from source pixels.',
    ],
  };

  const initialSpatialView: SpatialViewState = {
    mode: 'source_2d',
    transitionPhase: 'at_source',
    transitionProgress: 0,
    observerOriginParsec: [0, 0, 0],
    cameraQuaternion: [0, 0, 0, 1],
    fovVerticalDeg: 40,
    frustumShiftNdc: [0, 0],
    simulationEpochISO: capturedAtUTC,
    observationEpochISO: capturedAtUTC,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: {
      observedMarkers: false,
      catalogMatchedLabels: false,
      predictedEphemerides: true,
      constellationLines: false,
      earthDirectionVector: false,
      residualVectors: false,
      sightlineCone: false,
    },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_overcast_anchor_0',
    label: 'Original Moment — Overcast Horizon (01.5s)',
    createdAtMs: 1500,
    sourceAssetId: assetId,
    sourceTimeSec: 1.5,
    playbackWasPlaying: false,
    audioGain: 0.75,
    mediaVariantId: 'variant_overcast_original',
    cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null,
    navigationState: 'source',
    spatialView: structuredClone(initialSpatialView),
    viewRevision: 1,
  };

  return ObservationSessionSchema.parse({
    id: 'session_overcast_dusk_2026',
    title: 'Overcast Horizon — Sparse Cloud Gap Recording',
    viewRevision: 1,
    machines: {
      media: 'ready',
      restoration: 'idle',
      calibration: 'predicted_only',
      navigation: 'source',
      voice: 'listening',
    },
    sourceAsset,
    activeVariantId: 'variant_overcast_original',
    compareVariantId: undefined,
    compareWipePosition: 0.5,
    currentSourceTimeSec: 1.5,
    isPlaying: false,
    audioGain: 0.75,
    cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [graphOriginal],
    processingAttempts: [attemptOriginal],
    mediaVariants: [variantOriginal],
    observationSolution,
    selectedFindingId: null,
    focusedFindingId: null,
    spatialView: initialSpatialView,
    navigationHistory: {
      anchorMomentCheckpoint: anchorCheckpoint,
      checkpoints: [anchorCheckpoint],
      cursorIndex: 0,
    },
    voiceActions: [],
    exportArtifacts: [],
    reducedMotion: false,
    lastStatusBanner:
      '2 point detections insufficient for blind plate solve. Sky predicted separately from recorded timestamp and location.',
  });
}

/**
 * Builds a schema-validated `ActionContext` from the current `ObservationSession`
 * and optional contemporaneous pointer telemetry.
 */
export function buildActionContext(
  session: ObservationSession,
  recentPointer?: ActionContext['recentPointer']
): ActionContext {
  return ActionContextSchema.parse({
    sessionId: session.id,
    sourceAssetId: session.sourceAsset.id,
    sourceTimeSec: session.currentSourceTimeSec,
    navigationState: session.machines.navigation,
    calibrationState: session.machines.calibration,
    selectedFindingId: session.selectedFindingId,
    focusedFindingId: session.focusedFindingId,
    recentPointer,
    simulationEpochISO: session.spatialView.simulationEpochISO,
    viewRevision: session.viewRevision,
    reducedMotion: session.reducedMotion,
  });
}

function isPointerFreshAndMatchingRevision(
  pointer: NonNullable<ActionContext['recentPointer']> | undefined,
  context: ActionContext
): pointer is NonNullable<ActionContext['recentPointer']> {
  if (!pointer) {
    return false;
  }
  if (pointer.viewRevision !== context.viewRevision) {
    return false;
  }
  if (!Number.isFinite(pointer.atTimestampMs) || pointer.atTimestampMs < 0) {
    return false;
  }
  // Support both Unix epoch timestamps (Date.now()) and session-relative media timestamps (ms)
  if (pointer.atTimestampMs >= 1_000_000_000_000) {
    const ageMs = Date.now() - pointer.atTimestampMs;
    return ageMs <= MAX_POINTER_RECENCY_MS && ageMs >= -60_000;
  }
  const mediaNowMs = context.sourceTimeSec * 1000;
  const relativeAgeMs = mediaNowMs - pointer.atTimestampMs;
  return relativeAgeMs <= MAX_POINTER_RECENCY_MS;
}

function findNamedFindingInUtterance(
  normalizedUtterance: string,
  session: ObservationSession
): CelestialFinding | undefined {
  for (const finding of session.observationSolution.findings) {
    const nameLower = finding.title.toLowerCase();
    if (normalizedUtterance.includes(nameLower)) {
      return finding;
    }
    const idShort = finding.id.replace(/^finding_/, '').toLowerCase();
    if (idShort.length >= 3 && normalizedUtterance.includes(idShort)) {
      return finding;
    }
    if (
      finding.catalogRef?.bayerDesignation &&
      normalizedUtterance.includes(finding.catalogRef.bayerDesignation.toLowerCase())
    ) {
      return finding;
    }
    if (
      finding.catalogRef?.catalogSourceId &&
      normalizedUtterance.includes(finding.catalogRef.catalogSourceId.toLowerCase())
    ) {
      return finding;
    }
  }
  return undefined;
}

interface TargetResolutionResult {
  findingId?: string;
  disambiguation?: {
    question: string;
    choices: Array<{ id: string; label: string }>;
  };
  error?: string;
}

/**
 * Resolves object references in strict priority:
 * 1. Explicit named object in the utterance
 * 2. Active `selectedFindingId` (or `focusedFindingId`) unless overridden by an ambiguous multi-target pointer
 * 3. Fresh `recentPointer` (`<= 10s` and matching `viewRevision`):
 *    - If `overlappingCandidateIds.length > 1`, returns `disambiguation` (`needs_input`)
 *    - Otherwise resolves to `nearestFindingId` (or sole candidate in `overlappingCandidateIds`)
 */
function resolveTargetFinding(
  normalizedUtterance: string,
  context: ActionContext,
  session: ObservationSession
): TargetResolutionResult {
  const named = findNamedFindingInUtterance(normalizedUtterance, session);
  if (named) {
    return { findingId: named.id };
  }

  const pointerFresh = isPointerFreshAndMatchingRevision(context.recentPointer, context);

  // If the user just pointed at overlapping candidates, surface disambiguation
  if (
    pointerFresh &&
    context.recentPointer?.overlappingCandidateIds &&
    context.recentPointer.overlappingCandidateIds.length > 1
  ) {
    const findingMap = new Map(session.observationSolution.findings.map((f) => [f.id, f]));
    const choices = context.recentPointer.overlappingCandidateIds.map((id) => ({
      id,
      label: findingMap.get(id)?.title ?? id,
    }));
    const names = choices.map((c) => c.label).join(' or ');
    return {
      disambiguation: {
        question: `Multiple celestial objects overlap near your pointer. Did you mean ${names}?`,
        choices,
      },
    };
  }

  if (context.selectedFindingId) {
    return { findingId: context.selectedFindingId };
  }

  if (context.focusedFindingId) {
    return { findingId: context.focusedFindingId };
  }

  if (pointerFresh && context.recentPointer) {
    if (context.recentPointer.nearestFindingId) {
      return { findingId: context.recentPointer.nearestFindingId };
    }
    if (context.recentPointer.overlappingCandidateIds?.length === 1) {
      return { findingId: context.recentPointer.overlappingCandidateIds[0] };
    }
  }

  // If only a single finding exists in the entire solution, use it as unique visible candidate
  const soleFinding = session.observationSolution.findings[0];
  if (session.observationSolution.findings.length === 1 && soleFinding) {
    return { findingId: soleFinding.id };
  }

  return {
    error:
      'Ambiguous target reference: no star is currently selected and no recent pointer location matches the active view revision.',
  };
}

/**
 * Translates natural-language voice or text utterances into schema-validated `AlbedianAction`
 * payloads bound to `context.viewRevision`, or returns a structured `disambiguation` / `error`.
 */
export function parseUtteranceToAction(
  utterance: string,
  context: ActionContext,
  session: ObservationSession
): {
  action?: AlbedianAction;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
} {
  const trimmed = utterance.trim();
  if (!trimmed) {
    return { error: 'Empty voice or text command.' };
  }

  const norm = trimmed.toLowerCase();
  const requestId = `req_${context.viewRevision}_${deterministicHexDigest(`${trimmed}:${context.viewRevision}`).slice(0, 8)}`;

  // 0. Guard against requests for synthetic sky replacement or fabricated objects
  if (
    /\b(fake|synthetic|hallucinate|invent|replace the sky|add a moon|add a nebula|generate a galaxy)\b/i.test(
      norm
    )
  ) {
    return {
      error:
        'Unsafe synthetic edit rejected: Albedian preserves authentic observations and never fabricates sky content.',
    };
  }

  // 1. Interruption / Barge-in cancellation: "Stop", "Cancel", "Wait", "Halt", "Abort"
  if (/^(stop|cancel|wait|halt|abort|freeze)\b/i.test(norm) || /\b(stop|cancel)\s+(navigation|transition|flight)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({
        type: 'navigation.cancel',
        requestId,
        expectedViewRevision: context.viewRevision,
        transitionId: session.spatialView.activeTransitionId,
      }),
    };
  }

  // 2. Partial step back: "Go back a little", "Step back slightly", "Back a bit"
  if (/\b(back a little|back a bit|step back slightly|slightly back|partial back)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({
        type: 'history.back',
        requestId,
        expectedViewRevision: context.viewRevision,
        mode: 'partial',
        fraction: 0.35,
      }),
    };
  }

  // 3. Return to source moment or previous checkpoint:
  // "Go back", "Take me back", "Back to moment", "Return to source", "Back to photo"
  if (
    /\b(take me back|back to moment|back to the moment|return to source|back to source|back to recording|back to photo|back to video|go back|return home)\b/i.test(
      norm
    ) ||
    norm === 'back'
  ) {
    if (/\b(previous checkpoint|last checkpoint|checkpoint back)\b/i.test(norm)) {
      return {
        action: AlbedianActionSchema.parse({
          type: 'history.back',
          requestId,
          expectedViewRevision: context.viewRevision,
          mode: 'checkpoint',
        }),
      };
    }
    return {
      action: AlbedianActionSchema.parse({
        type: 'source.return',
        requestId,
        expectedViewRevision: context.viewRevision,
        checkpointId: session.navigationHistory.anchorMomentCheckpoint.id,
        restoreAudio: true,
      }),
    };
  }

  // 4. Historical / Future Epoch Simulation:
  // "Show me this sky a thousand years ago", "1026 CE", "2000 BCE", "year 1026"
  if (
    /\b(thousand years ago|1000 years ago|1026\s*ce|year\s+-?\d+|\d+\s*(?:ce|bce|bc|ad)|years from now|years ago|epoch)\b/i.test(
      norm
    )
  ) {
    let yearCE = 1026;
    if (/\b(thousand years ago|1000 years ago|1026)\b/i.test(norm)) {
      yearCE = 1026;
    } else {
      const bceMatch = norm.match(/(\d+)\s*(?:bce|bc)\b/i);
      const ceMatch = norm.match(/(-?\d+)\s*(?:ce|ad)\b/i);
      const agoMatch = norm.match(/(\d+)\s*years\s*ago\b/i);
      const futureMatch = norm.match(/(\d+)\s*years\s*from\s*now\b/i);
      const yearMatch = norm.match(/year\s+(-?\d+)/i);
      if (bceMatch?.[1]) {
        yearCE = -Number.parseInt(bceMatch[1], 10);
      } else if (ceMatch?.[1]) {
        yearCE = Number.parseInt(ceMatch[1], 10);
      } else if (agoMatch?.[1]) {
        yearCE = 2026 - Number.parseInt(agoMatch[1], 10);
      } else if (futureMatch?.[1]) {
        yearCE = 2026 + Number.parseInt(futureMatch[1], 10);
      } else if (yearMatch?.[1]) {
        yearCE = Number.parseInt(yearMatch[1], 10);
      }
    }

    if (yearCE < -10000 || yearCE > 12000) {
      return {
        error: `Requested epoch year ${yearCE} CE is outside the validated proper-motion propagation window (-10000 to 12000 CE).`,
      };
    }

    const paddedYear =
      yearCE < 0
        ? `-${Math.abs(yearCE).toString().padStart(4, '0')}`
        : yearCE.toString().padStart(4, '0');
    const epochISO = `${paddedYear}-10-19T22:14:00Z`;

    return {
      action: AlbedianActionSchema.parse({
        type: 'space.epoch',
        requestId,
        expectedViewRevision: context.viewRevision,
        epochISO,
        yearCE,
      }),
    };
  }

  // 5. UI Overlays: "Where is Earth?", "Show constellation lines", "Show residuals", "Sightline cone"
  if (/\b(where is earth|earth direction|point to earth|direction to earth|home vector)\b/i.test(norm)) {
    const enabled = !/\b(hide|off|disable)\b/i.test(norm);
    return {
      action: AlbedianActionSchema.parse({
        type: 'ui.overlay',
        requestId,
        expectedViewRevision: context.viewRevision,
        overlay: 'earthDirectionVector',
        enabled,
      }),
    };
  }

  if (/\b(constellation lines|constellations overlay|star lines)\b/i.test(norm)) {
    const enabled = !/\b(hide|off|disable)\b/i.test(norm);
    return {
      action: AlbedianActionSchema.parse({
        type: 'ui.overlay',
        requestId,
        expectedViewRevision: context.viewRevision,
        overlay: 'constellationLines',
        enabled,
      }),
    };
  }

  if (/\b(residual vectors|residuals)\b/i.test(norm)) {
    const enabled = !/\b(hide|off|disable)\b/i.test(norm);
    return {
      action: AlbedianActionSchema.parse({
        type: 'ui.overlay',
        requestId,
        expectedViewRevision: context.viewRevision,
        overlay: 'residualVectors',
        enabled,
      }),
    };
  }

  // 6. Media Comparison: "Compare", "Show original", "Before and after", "Wipe"
  if (
    /\b(compare|show original|original recording|before and after|a\/b wipe|split view)\b/i.test(
      norm
    )
  ) {
    const originalVariant = session.mediaVariants[0]?.id ?? 'variant_original';
    const restoredVariant =
      session.activeVariantId !== originalVariant
        ? session.activeVariantId
        : (session.mediaVariants[1]?.id ?? originalVariant);
    return {
      action: AlbedianActionSchema.parse({
        type: 'media.compare',
        requestId,
        expectedViewRevision: context.viewRevision,
        variantAId: originalVariant,
        variantBId: restoredVariant,
        preserveTime: true,
      }),
    };
  }

  // 7. Media Restoration:
  // "Make the sky clearer", "Don't change the people", "Temporal stack", "Denoise", "Bring out the stars"
  if (
    /\b(make the sky clearer|sky clearer|don't change the people|do not change the people|preserve the people|temporal stack|motion-compensated|denoise|clean up noise|restore|bring out faint stars)\b/i.test(
      norm
    )
  ) {
    const profile: 'natural' | 'temporal' | 'star_protected' = /\b(temporal|stack|faint)\b/i.test(
      norm
    )
      ? 'temporal'
      : 'star_protected';
    return {
      action: AlbedianActionSchema.parse({
        type: 'media.restore',
        requestId,
        expectedViewRevision: context.viewRevision,
        sourceAssetId: session.sourceAsset.id,
        profile,
        constraints: {
          preservePeople: true,
          allowSyntheticSky: false,
          protectStars: true,
        },
      }),
    };
  }

  // 8. Media Seek: "Seek to 8 seconds", "Jump to 3.5s"
  const seekMatch = norm.match(/\b(?:seek|jump|scrub)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b/i);
  if (seekMatch?.[1]) {
    const sourceTimeSec = Number.parseFloat(seekMatch[1]);
    return {
      action: AlbedianActionSchema.parse({
        type: 'media.seek',
        requestId,
        expectedViewRevision: context.viewRevision,
        sourceTimeSec,
        preserveAudioSync: true,
      }),
    };
  }

  // 9. Calibrated / Predicted Sky Registration:
  // "What were we looking at?", "Show me what was overhead", "Identify the stars", "Show the sky"
  if (
    /\b(what were we looking at|what was overhead|show me what was overhead|identify the stars|calibrate|register the sky|show sky|what is in the sky|match the sky)\b/i.test(
      norm
    )
  ) {
    const mode: 'measured' | 'predicted' =
      context.calibrationState === 'calibrated' ? 'measured' : 'predicted';
    return {
      action: AlbedianActionSchema.parse({
        type: 'sky.show',
        requestId,
        expectedViewRevision: context.viewRevision,
        mode,
        observationSolutionId: session.observationSolution.id,
      }),
    };
  }

  // 10. Alternative 3D Viewpoint / Orbit:
  // "Show from another viewpoint", "Orbit", "Another angle", "Translate viewpoint"
  if (
    /\b(another viewpoint|different viewpoint|orbit|another angle|from the side|translate viewpoint|shift viewpoint)\b/i.test(
      norm
    )
  ) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) {
      return { disambiguation: resolved.disambiguation };
    }
    const targetFindingId = resolved.findingId ?? session.selectedFindingId ?? 'finding_betelgeuse';
    const trajectory: 'orbit' | 'translate' = /\b(translate|shift)\b/i.test(norm)
      ? 'translate'
      : 'orbit';
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.viewpoint',
        requestId,
        expectedViewRevision: context.viewRevision,
        targetFindingId,
        units: 'pc',
        offsetParsec: [12, 8, -5],
        trajectory,
      }),
    };
  }

  // 11. Describe Distance / Evidence: "How far away is it?", "What is its distance?"
  if (/\b(how far|distance|light years|parsecs|how distant)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) {
      return { disambiguation: resolved.disambiguation };
    }
    if (!resolved.findingId) {
      return { error: resolved.error };
    }
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.describe',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        fields: ['distance', 'evidence'],
      }),
    };
  }

  // 12. Describe Behind Sightline: "What's behind it?", "What lies beyond it?", "Behind sightline"
  if (
    /\b(what's behind|what is behind|what lies beyond|behind it|beyond it|behind|beyond|behind sightline|along this sightline)\b/i.test(
      norm
    )
  ) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) {
      return { disambiguation: resolved.disambiguation };
    }
    if (!resolved.findingId) {
      return { error: resolved.error };
    }
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.describe',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        fields: ['behind_sightline'],
      }),
    };
  }

  // 13. Enter 3D Space: "Take me there", "Explore", "Fly to it", "Go to Betelgeuse", "Enter 3D space"
  if (
    /\b(take me there|fly there|go there|explore|fly to|travel to|enter space|enter 3d|zoom into space|take me to)\b/i.test(
      norm
    )
  ) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) {
      return { disambiguation: resolved.disambiguation };
    }
    if (!resolved.findingId) {
      return { error: resolved.error };
    }
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.enter',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        distancePolicy: 'measured_or_estimated',
      }),
    };
  }

  // 14. Select / Identify Finding: "What's that one?", "What is that?", "Select Betelgeuse", "Select Rigel"
  if (
    /\b(what's that|what is that|what star is that|which star|that one|this one|select|focus on|inspect|point at|look at|tell me about)\b/i.test(
      norm
    ) ||
    Boolean(findNamedFindingInUtterance(norm, session))
  ) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) {
      return { disambiguation: resolved.disambiguation };
    }
    if (!resolved.findingId) {
      return { error: resolved.error };
    }
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.select',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
      }),
    };
  }

  return {
    error: `Unrecognized or ambiguous observation command: "${trimmed}".`,
  };
}

/**
 * Alias for `parseUtteranceToAction` per architectural design specification.
 */
export const resolveVoiceUtterance = parseUtteranceToAction;

function syncAnchorCheckpointIfInitial(session: ObservationSession): ObservationSession {
  const anchor = session.navigationHistory.anchorMomentCheckpoint;
  if (
    session.navigationHistory.checkpoints.length === 1 &&
    session.viewRevision === anchor.viewRevision &&
    session.machines.navigation === 'source'
  ) {
    const syncedAnchor: CameraCheckpoint = {
      ...anchor,
      sourceAssetId: session.sourceAsset.id,
      sourceTimeSec: session.currentSourceTimeSec,
      playbackWasPlaying: session.isPlaying,
      audioGain: session.audioGain,
      mediaVariantId: session.activeVariantId,
      cropTransform: { ...session.cropTransform },
      selectedFindingId: session.selectedFindingId,
      navigationState: session.machines.navigation,
      spatialView: structuredClone(session.spatialView),
      viewRevision: session.viewRevision,
    };
    return {
      ...session,
      navigationHistory: {
        anchorMomentCheckpoint: syncedAnchor,
        checkpoints: [syncedAnchor],
        cursorIndex: 0,
      },
    };
  }
  return session;
}

function pushCheckpointIfDepartingSource(
  session: ObservationSession,
  label: string
): ObservationSession['navigationHistory'] {
  const checkpoint = createCameraCheckpoint(session, label);
  const existingCheckpoints = session.navigationHistory.checkpoints;
  const nextCheckpoints = [...existingCheckpoints, checkpoint];
  return {
    anchorMomentCheckpoint: session.navigationHistory.anchorMomentCheckpoint,
    checkpoints: nextCheckpoints,
    cursorIndex: nextCheckpoints.length - 1,
  };
}

function computeStarAnchorNdc(
  finding: CelestialFinding,
  sourceAsset: SourceAsset
): [number, number] {
  if (!finding.sourceDetection) {
    return [0, 0];
  }
  const ndcX = Number(((finding.sourceDetection.xPx / sourceAsset.width) * 2 - 1).toFixed(4));
  const ndcY = Number((1 - (finding.sourceDetection.yPx / sourceAsset.height) * 2).toFixed(4));
  return [Math.max(-1, Math.min(1, ndcX)), Math.max(-1, Math.min(1, ndcY))];
}

/**
 * Authoritative revision-checked `AlbedianAction` dispatcher and reducer.
 * - Rejects any action with `expectedViewRevision !== session.viewRevision` (`reason: 'stale_revision'`)
 *   and leaves `ObservationSession` untouched.
 * - Enforces scientific calibration and distance prerequisites (`space.enter` requires a
 *   `catalog_matched` finding with a valid `inferredDistancePc`).
 * - Pushes immutable `CameraCheckpoint`s when departing `source`/`comparison` for `sky_registered` or `space`.
 * - Restores `anchorMomentCheckpoint` identically on `source.return`.
 */
export function dispatchAlbedianAction(
  session: ObservationSession,
  action: AlbedianAction
): { session: ObservationSession; outcome: ActionOutcome } {
  // 1. Strict revision gate (PBT-VOICE-1)
  if (action.expectedViewRevision !== session.viewRevision) {
    return {
      session,
      outcome: ActionOutcomeSchema.parse({
        status: 'rejected',
        actionId: action.requestId,
        reason: 'stale_revision',
        explanation: `Stale action revision: expected ${action.expectedViewRevision}, but session is at revision ${session.viewRevision}.`,
        nextActions: ['Rebuild ActionContext from latest ObservationSession and retry'],
      }),
    };
  }

  const baseSession = syncAnchorCheckpointIfInitial(session);
  const nextRevision = baseSession.viewRevision + 1;

  switch (action.type) {
    case 'media.restore': {
      if (action.sourceAssetId !== baseSession.sourceAsset.id) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'missing_source',
            explanation: `SourceAsset "${action.sourceAssetId}" does not match active session asset "${baseSession.sourceAsset.id}".`,
            nextActions: ['Dispatch media.restore with the active SourceAsset ID'],
          }),
        };
      }

      if ((action.constraints as { allowSyntheticSky?: boolean }).allowSyntheticSky !== false) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'unsafe_synthetic_edit',
            explanation: 'Synthetic sky generation is forbidden by Albedian fidelity policy.',
            nextActions: ['Use star_protected or temporal restoration profiles'],
          }),
        };
      }

      const matchingGraph = baseSession.processingGraphs.find(
        (g) =>
          g.profileName === action.profile ||
          (action.profile === 'natural' && g.profileName === 'star_protected')
      );
      const matchingVariant =
        baseSession.mediaVariants.find((v) => v.graphId === matchingGraph?.id) ??
        baseSession.mediaVariants[baseSession.mediaVariants.length - 1];

      if (!matchingVariant) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'missing_source',
            explanation: `No MediaVariant available for profile "${action.profile}".`,
            nextActions: ['Run restoration pipeline first'],
          }),
        };
      }

      const statusText = `Applied ${matchingVariant.label} (${matchingVariant.summaryNote})`;
      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        activeVariantId: matchingVariant.id,
        machines: {
          ...baseSession.machines,
          restoration: 'ready',
          voice: 'listening',
        },
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: `Restored sky clarity using ${matchingVariant.label} while preserving people and original audio.`,
          visualStatus: statusText,
        }),
      };
    }

    case 'media.compare': {
      const variantA = baseSession.mediaVariants.find((v) => v.id === action.variantAId);
      const variantB = baseSession.mediaVariants.find((v) => v.id === action.variantBId);
      if (!variantA || !variantB) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'missing_source',
            explanation: `Cannot compare missing media variants (${action.variantAId} vs ${action.variantBId}).`,
            nextActions: ['Select valid media variants from session.mediaVariants'],
          }),
        };
      }

      const statusText = `Comparing ${variantA.label} with ${variantB.label} at ${baseSession.currentSourceTimeSec.toFixed(1)}s.`;
      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        activeVariantId: variantB.id,
        compareVariantId: variantA.id,
        compareWipePosition: 0.5,
        machines: {
          ...baseSession.machines,
          navigation: 'comparison',
          voice: 'listening',
        },
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: `Comparing ${variantA.label} and ${variantB.label}.`,
          visualStatus: statusText,
        }),
      };
    }

    case 'media.seek': {
      const maxTimeSec = baseSession.sourceAsset.durationMs / 1000;
      const clampedSec = Math.min(Math.max(0, action.sourceTimeSec), maxTimeSec);
      const statusText = `Seeked to ${clampedSec.toFixed(2)}s (audio stream synchronized).`;
      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        currentSourceTimeSec: clampedSec,
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: `Seeked to ${clampedSec.toFixed(1)} seconds.`,
          visualStatus: statusText,
        }),
      };
    }

    case 'sky.show': {
      if (
        baseSession.machines.calibration === 'unresolved' ||
        baseSession.observationSolution.status === 'unresolved'
      ) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'unresolved_sky',
            explanation:
              'Sky registration is unresolved: insufficient star detections and no timestamp/location fallback.',
            nextActions: ['Provide capture timestamp and location for predicted sky model'],
          }),
        };
      }

      if (
        action.mode === 'measured' &&
        (baseSession.machines.calibration !== 'calibrated' ||
          baseSession.observationSolution.status !== 'calibrated')
      ) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'unresolved_sky',
            explanation: `Direct measured pixel registration requires a calibrated WCS solution (current status: ${baseSession.observationSolution.status}).`,
            nextActions: ['Show predicted sky model instead (`mode: "predicted"`)'],
          }),
        };
      }

      const shouldPushCheckpoint =
        baseSession.machines.navigation === 'source' ||
        baseSession.machines.navigation === 'comparison';
      const nextHistory = shouldPushCheckpoint
        ? pushCheckpointIfDepartingSource(baseSession, 'Departure from Source to Registered Sky')
        : baseSession.navigationHistory;

      const isCalibrated = baseSession.observationSolution.status === 'calibrated';
      const statusText = isCalibrated
        ? `Calibrated sky registered (${baseSession.observationSolution.quality.heldOutInliers} held-out stars, ${baseSession.observationSolution.quality.medianResidualPx.toFixed(2)} px median residual).`
        : 'Showing predicted sky model from recorded timestamp and location (separate from source pixels).';

      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        machines: {
          ...baseSession.machines,
          navigation: 'sky_registered',
          voice: 'listening',
        },
        spatialView: {
          ...baseSession.spatialView,
          mode: 'registered_sky',
          transitionPhase: 'at_registered_sky',
          transitionProgress: 1,
          activeTransitionId: action.requestId,
          activeOverlays: {
            ...baseSession.spatialView.activeOverlays,
            observedMarkers: isCalibrated,
            catalogMatchedLabels: isCalibrated,
            predictedEphemerides: true,
            constellationLines: true,
          },
        },
        navigationHistory: nextHistory,
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: statusText,
          visualStatus: statusText,
        }),
      };
    }

    case 'finding.select': {
      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.findingId
      );
      if (!finding) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'ambiguous_target',
            explanation: `CelestialFinding "${action.findingId}" does not exist in active ObservationSolution.`,
            nextActions: ['Select a visible star or planet marker'],
          }),
        };
      }

      const badge = getEpistemicBadgeDescriptor(finding.evidenceClass);
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      const anchorNdc = computeStarAnchorNdc(finding, baseSession.sourceAsset);
      const statusText = `${finding.title} (${finding.subtitle}) · ${badge.label} · ${distInfo.lightYearText}`;

      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        selectedFindingId: finding.id,
        focusedFindingId: finding.id,
        spatialView: {
          ...baseSession.spatialView,
          lockedAnchorNdc: anchorNdc,
        },
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: `${finding.title}, ${finding.subtitle}. ${badge.label}. ${distInfo.lightYearText}.`,
          visualStatus: statusText,
        }),
      };
    }

    case 'finding.describe': {
      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.findingId
      );
      if (!finding) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'ambiguous_target',
            explanation: `Cannot describe unknown finding "${action.findingId}".`,
            nextActions: ['Select a finding first'],
          }),
        };
      }

      const badge = getEpistemicBadgeDescriptor(finding.evidenceClass);
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      const segments: string[] = [`${finding.title} (${finding.subtitle})`];

      if (action.fields.includes('distance')) {
        segments.push(`Distance: ${distInfo.lightYearText} [${distInfo.parsecText}]`);
      }
      if (action.fields.includes('type') && finding.catalogRef?.spectralType) {
        segments.push(`Spectral type: ${finding.catalogRef.spectralType}`);
      }
      if (
        action.fields.includes('motion') &&
        finding.catalogRef?.properMotionRaMasYr !== undefined
      ) {
        segments.push(
          `Proper motion: (${finding.catalogRef.properMotionRaMasYr}, ${finding.catalogRef.properMotionDecMasYr ?? 0}) mas/yr`
        );
      }
      if (action.fields.includes('evidence')) {
        segments.push(`Evidence: ${badge.label} — ${badge.disclosure}`);
      }
      if (action.fields.includes('behind_sightline')) {
        segments.push(
          `Sightline cone active along RA ${finding.skyPosition.raDeg.toFixed(2)}°, Dec ${finding.skyPosition.decDeg.toFixed(2)}° showing deeper catalog background.`
        );
      }

      const descriptionText = segments.join(' · ');
      const enableSightline =
        action.fields.includes('behind_sightline') ||
        baseSession.spatialView.activeOverlays.sightlineCone;

      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        selectedFindingId: finding.id,
        focusedFindingId: finding.id,
        spatialView: {
          ...baseSession.spatialView,
          activeOverlays: {
            ...baseSession.spatialView.activeOverlays,
            sightlineCone: enableSightline,
          },
        },
        lastStatusBanner: descriptionText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: descriptionText,
          visualStatus: descriptionText,
        }),
      };
    }

    case 'space.enter': {
      if (
        baseSession.machines.calibration === 'unresolved' ||
        baseSession.observationSolution.status === 'unresolved'
      ) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'unresolved_sky',
            explanation: 'Cannot enter 3D catalog space from an unresolved sky.',
            nextActions: ['Calibrate sky first'],
          }),
        };
      }

      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.findingId
      );
      if (!finding) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'ambiguous_target',
            explanation: `Target finding "${action.findingId}" not found in active ObservationSolution.`,
            nextActions: ['Select a catalog-matched star first'],
          }),
        };
      }

      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      if (
        finding.evidenceClass !== 'catalog_matched' ||
        !finding.inferredDistancePc ||
        !distInfo.canEnter3DSpace
      ) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'no_distance_estimate',
            explanation: `${finding.title} (${finding.evidenceClass}) lacks a verified catalog parallax or Bailer-Jones distance prior required for 3D space navigation.`,
            nextActions: [
              'Select a catalog-matched star with a verified distance interval (e.g. Betelgeuse, Rigel, Aldebaran)',
            ],
          }),
        };
      }

      const nextHistory = pushCheckpointIfDepartingSource(
        baseSession,
        `Checkpoint before entering 3D space toward ${finding.title}`
      );
      const targetCoords =
        finding.cartesianParsecICRS ??
        computeCartesianParsecICRS(
          finding.skyPosition.raDeg,
          finding.skyPosition.decDeg,
          finding.inferredDistancePc.median
        );
      const anchorNdc = computeStarAnchorNdc(finding, baseSession.sourceAsset);
      const statusText = `Entered 3D Space · ${finding.title} (${distInfo.lightYearText}) · Catalog Geometry · Distance Estimates`;

      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        isPlaying: false,
        selectedFindingId: finding.id,
        focusedFindingId: finding.id,
        machines: {
          ...baseSession.machines,
          navigation: 'space',
          voice: 'listening',
        },
        spatialView: {
          ...baseSession.spatialView,
          mode: 'catalog_3d',
          transitionPhase: 'at_space',
          transitionProgress: 1,
          activeTransitionId: action.requestId,
          observerOriginParsec: targetCoords,
          fovVerticalDeg: 45,
          lockedAnchorNdc: anchorNdc,
        },
        navigationHistory: nextHistory,
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: `Arrived near ${finding.title}, approximately ${distInfo.lightYearText} from Earth. Catalog geometry and distance estimates active.`,
          visualStatus: statusText,
        }),
      };
    }

    case 'space.viewpoint': {
      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.targetFindingId
      );
      if (!finding) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'ambiguous_target',
            explanation: `Cannot shift viewpoint around unknown finding "${action.targetFindingId}".`,
            nextActions: ['Select a catalog-matched star first'],
          }),
        };
      }

      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      if (
        finding.evidenceClass !== 'catalog_matched' ||
        !finding.inferredDistancePc ||
        !distInfo.canEnter3DSpace
      ) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'no_distance_estimate',
            explanation: `${finding.title} lacks a catalog distance estimate for 3D viewpoint displacement.`,
            nextActions: ['Select a catalog-matched star with a verified distance estimate'],
          }),
        };
      }

      const nextHistory = pushCheckpointIfDepartingSource(
        baseSession,
        `Checkpoint before ${action.trajectory} viewpoint around ${finding.title}`
      );
      const baseCoords =
        finding.cartesianParsecICRS ??
        computeCartesianParsecICRS(
          finding.skyPosition.raDeg,
          finding.skyPosition.decDeg,
          finding.inferredDistancePc.median
        );
      const displacedOrigin: [number, number, number] = [
        Number((baseCoords[0] + action.offsetParsec[0]).toFixed(6)),
        Number((baseCoords[1] + action.offsetParsec[1]).toFixed(6)),
        Number((baseCoords[2] + action.offsetParsec[2]).toFixed(6)),
      ];

      const statusText = `Simulated 3D Viewpoint (${action.trajectory}) near ${finding.title} · Offset [${action.offsetParsec.join(', ')}] pc · Catalog Geometry · Distance Estimates`;

      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        isPlaying: false,
        selectedFindingId: finding.id,
        focusedFindingId: finding.id,
        machines: {
          ...baseSession.machines,
          navigation: 'space',
          voice: 'listening',
        },
        spatialView: {
          ...baseSession.spatialView,
          mode: 'catalog_3d',
          transitionPhase: 'at_space',
          transitionProgress: 1,
          activeTransitionId: action.requestId,
          observerOriginParsec: displacedOrigin,
          cameraQuaternion: [0, 0.258819, 0, 0.965926],
          activeOverlays: {
            ...baseSession.spatialView.activeOverlays,
            earthDirectionVector: true,
          },
        },
        navigationHistory: nextHistory,
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: `Shifted to alternative 3D viewpoint around ${finding.title}. Earth direction vector enabled.`,
          visualStatus: statusText,
        }),
      };
    }

    case 'space.epoch': {
      if (
        !Number.isInteger(action.yearCE) ||
        action.yearCE < -10000 ||
        action.yearCE > 12000 ||
        !action.epochISO ||
        action.epochISO.trim().length < 4
      ) {
        return {
          session,
          outcome: ActionOutcomeSchema.parse({
            status: 'rejected',
            actionId: action.requestId,
            reason: 'invalid_epoch',
            explanation: `Epoch year ${action.yearCE} CE is outside the valid proper-motion integration bounds (-10000 to 12000 CE).`,
            nextActions: ['Specify an epoch between 10,000 BCE and 12,000 CE'],
          }),
        };
      }

      const shouldPushCheckpoint =
        baseSession.machines.navigation === 'source' ||
        baseSession.machines.navigation === 'comparison';
      const nextHistory = shouldPushCheckpoint
        ? pushCheckpointIfDepartingSource(
            baseSession,
            `Checkpoint before epoch change to ${action.yearCE} CE`
          )
        : baseSession.navigationHistory;

      const nextNavState =
        baseSession.machines.navigation === 'source' ||
        baseSession.machines.navigation === 'comparison'
          ? 'sky_registered'
          : baseSession.machines.navigation;

      const statusText = `Modeled Sky · Epoch ${action.yearCE} CE (Gaia DR3 proper motion propagation)`;

      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        isPlaying: false,
        machines: {
          ...baseSession.machines,
          navigation: nextNavState,
          voice: 'listening',
        },
        spatialView: {
          ...baseSession.spatialView,
          mode: 'modeled_epoch',
          transitionPhase: nextNavState === 'space' ? 'at_space' : 'at_registered_sky',
          transitionProgress: 1,
          simulationEpochISO: action.epochISO,
        },
        navigationHistory: nextHistory,
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: `Showing modeled sky at ${action.yearCE} CE using Gaia proper motions.`,
          visualStatus: statusText,
        }),
      };
    }

    case 'history.back': {
      if (action.mode === 'partial') {
        const fraction = Math.min(Math.max(action.fraction ?? 0.35, 0.05), 1);
        const currentOrigin = baseSession.spatialView.observerOriginParsec;
        const anchorOrigin =
          baseSession.navigationHistory.anchorMomentCheckpoint.spatialView.observerOriginParsec;
        const interpolatedOrigin: [number, number, number] = [
          Number((currentOrigin[0] * (1 - fraction) + anchorOrigin[0] * fraction).toFixed(6)),
          Number((currentOrigin[1] * (1 - fraction) + anchorOrigin[1] * fraction).toFixed(6)),
          Number((currentOrigin[2] * (1 - fraction) + anchorOrigin[2] * fraction).toFixed(6)),
        ];

        const currentFov = baseSession.spatialView.fovVerticalDeg;
        const anchorFov =
          baseSession.navigationHistory.anchorMomentCheckpoint.spatialView.fovVerticalDeg;
        const interpolatedFov = Number(
          (currentFov * (1 - fraction) + anchorFov * fraction).toFixed(4)
        );
        const statusText = `Stepped back ${Math.round(fraction * 100)}% toward observation checkpoint.`;

        const updatedSession: ObservationSession = {
          ...baseSession,
          viewRevision: nextRevision,
          spatialView: {
            ...baseSession.spatialView,
            observerOriginParsec: interpolatedOrigin,
            fovVerticalDeg: interpolatedFov,
            transitionProgress: Number((1 - fraction).toFixed(4)),
          },
          lastStatusBanner: statusText,
        };

        return {
          session: updatedSession,
          outcome: ActionOutcomeSchema.parse({
            status: 'completed',
            actionId: action.requestId,
            newStateVersion: nextRevision,
            spokenFeedback: statusText,
            visualStatus: statusText,
          }),
        };
      }

      // Checkpoint mode: pop back one checkpoint or restore anchorMomentCheckpoint
      const checkpoints = baseSession.navigationHistory.checkpoints;
      const lastCheckpoint = checkpoints[checkpoints.length - 1];
      const targetCheckpoint =
        checkpoints.length > 1 && lastCheckpoint
          ? lastCheckpoint
          : baseSession.navigationHistory.anchorMomentCheckpoint;
      const remainingCheckpoints =
        checkpoints.length > 1
          ? checkpoints.slice(0, checkpoints.length - 1)
          : [baseSession.navigationHistory.anchorMomentCheckpoint];

      const statusText = `Restored checkpoint: ${targetCheckpoint.label}`;
      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        currentSourceTimeSec: targetCheckpoint.sourceTimeSec,
        isPlaying: targetCheckpoint.playbackWasPlaying,
        audioGain: targetCheckpoint.audioGain,
        activeVariantId: targetCheckpoint.mediaVariantId,
        cropTransform: { ...targetCheckpoint.cropTransform },
        selectedFindingId: targetCheckpoint.selectedFindingId,
        focusedFindingId: targetCheckpoint.selectedFindingId,
        machines: {
          ...baseSession.machines,
          navigation: targetCheckpoint.navigationState,
          voice: 'listening',
        },
        spatialView: structuredClone(targetCheckpoint.spatialView),
        navigationHistory: {
          anchorMomentCheckpoint: baseSession.navigationHistory.anchorMomentCheckpoint,
          checkpoints: remainingCheckpoints,
          cursorIndex: remainingCheckpoints.length - 1,
        },
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: statusText,
          visualStatus: statusText,
        }),
      };
    }

    case 'navigation.cancel': {
      const settledNavState =
        baseSession.machines.navigation === 'transitioning' ||
        baseSession.machines.navigation === 'returning'
          ? baseSession.spatialView.mode === 'source_2d'
            ? 'source'
            : 'sky_registered'
          : baseSession.machines.navigation;

      const statusText = 'Navigation transition canceled; camera settled at current pose.';
      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        machines: {
          ...baseSession.machines,
          navigation: settledNavState,
          voice: 'interrupted',
        },
        spatialView: {
          ...baseSession.spatialView,
          activeTransitionId: undefined,
        },
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'canceled',
          actionId: action.requestId,
          settledStateVersion: nextRevision,
          visualStatus: statusText,
        }),
      };
    }

    case 'ui.overlay': {
      const statusText = `${action.enabled ? 'Enabled' : 'Disabled'} overlay: ${action.overlay}.`;
      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        spatialView: {
          ...baseSession.spatialView,
          activeOverlays: {
            ...baseSession.spatialView.activeOverlays,
            [action.overlay]: action.enabled,
          },
        },
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: statusText,
          visualStatus: statusText,
        }),
      };
    }

    case 'source.return': {
      const allCheckpoints = [
        baseSession.navigationHistory.anchorMomentCheckpoint,
        ...baseSession.navigationHistory.checkpoints,
      ];
      const targetCheckpoint = action.checkpointId
        ? (allCheckpoints.find((c) => c.id === action.checkpointId) ??
          baseSession.navigationHistory.anchorMomentCheckpoint)
        : baseSession.navigationHistory.anchorMomentCheckpoint;

      const restoredSpatialView: SpatialViewState = {
        ...structuredClone(targetCheckpoint.spatialView),
        mode: 'source_2d',
        transitionPhase: 'at_source',
        transitionProgress: 0,
        activeTransitionId: undefined,
      };

      const statusText = `Returned to Moment (${targetCheckpoint.sourceTimeSec.toFixed(1)}s) · Audio & frame restored.`;
      const updatedSession: ObservationSession = {
        ...baseSession,
        viewRevision: nextRevision,
        currentSourceTimeSec: targetCheckpoint.sourceTimeSec,
        isPlaying: targetCheckpoint.playbackWasPlaying,
        audioGain: targetCheckpoint.audioGain,
        activeVariantId: targetCheckpoint.mediaVariantId,
        compareVariantId: undefined,
        cropTransform: { ...targetCheckpoint.cropTransform },
        selectedFindingId: targetCheckpoint.selectedFindingId,
        focusedFindingId: targetCheckpoint.selectedFindingId,
        machines: {
          ...baseSession.machines,
          navigation: 'source',
          voice: 'listening',
        },
        spatialView: restoredSpatialView,
        navigationHistory: {
          anchorMomentCheckpoint: targetCheckpoint,
          checkpoints: [targetCheckpoint],
          cursorIndex: 0,
        },
        lastStatusBanner: statusText,
      };

      return {
        session: updatedSession,
        outcome: ActionOutcomeSchema.parse({
          status: 'completed',
          actionId: action.requestId,
          newStateVersion: nextRevision,
          spokenFeedback: 'Returned to your original recorded moment.',
          visualStatus: statusText,
        }),
      };
    }
  }
}

/**
 * Alias for `dispatchAlbedianAction` per architectural design specification.
 */
export const validateAndDispatchAction = dispatchAlbedianAction;

/**
 * End-to-end voice/text command executor:
 * 1. Builds `ActionContext` from `session` and `recentPointer`
 * 2. Parses the natural-language `utterance` into a typed `AlbedianAction` or `disambiguation`
 * 3. Validates and dispatches the action through `dispatchAlbedianAction`
 * 4. Records the audited `VoiceAction` on `session.voiceActions`
 */
export function executeVoiceOrTextCommand(
  session: ObservationSession,
  utterance: string,
  modality: 'voice' | 'text' | 'pointer' = 'voice',
  recentPointer?: ActionContext['recentPointer']
): {
  session: ObservationSession;
  outcome: ActionOutcome;
  voiceActionRecord: VoiceAction;
} {
  const context = buildActionContext(session, recentPointer);
  const parsed = parseUtteranceToAction(utterance, context, session);
  const actionRecordId = `voice_act_${session.viewRevision}_${session.voiceActions.length + 1}`;

  const voiceActionRecord: VoiceAction = VoiceActionSchema.parse({
    id: actionRecordId,
    utterance: utterance.trim() || '(empty)',
    modality,
    context,
    proposedAction: parsed.action,
    createdAtUTC: new Date().toISOString(),
  });

  if (parsed.disambiguation) {
    const outcome: ActionOutcome = ActionOutcomeSchema.parse({
      status: 'needs_input',
      actionId: actionRecordId,
      question: parsed.disambiguation.question,
      choices: parsed.disambiguation.choices,
    });
    const nextSession: ObservationSession = {
      ...session,
      machines: {
        ...session.machines,
        voice: 'awaiting_tool',
      },
      voiceActions: [...session.voiceActions, voiceActionRecord],
      lastStatusBanner: parsed.disambiguation.question,
    };
    return { session: nextSession, outcome, voiceActionRecord };
  }

  if (!parsed.action) {
    const errText = parsed.error ?? 'Could not resolve voice or text command.';
    const reason:
      | 'unsafe_synthetic_edit'
      | 'invalid_epoch'
      | 'unresolved_sky'
      | 'ambiguous_target' = /synthetic/i.test(errText)
      ? 'unsafe_synthetic_edit'
      : /epoch/i.test(errText)
        ? 'invalid_epoch'
        : session.machines.calibration === 'unresolved'
          ? 'unresolved_sky'
          : 'ambiguous_target';

    const outcome: ActionOutcome = ActionOutcomeSchema.parse({
      status: 'rejected',
      actionId: actionRecordId,
      reason,
      explanation: errText,
      nextActions: [
        'Select a star on screen or name it explicitly (e.g. "Select Betelgeuse")',
        'Ask "What were we looking at?"',
        'Say "Back to moment"',
      ],
    });
    const nextSession: ObservationSession = {
      ...session,
      voiceActions: [...session.voiceActions, voiceActionRecord],
      lastStatusBanner: errText,
    };
    return { session: nextSession, outcome, voiceActionRecord };
  }

  const dispatched = dispatchAlbedianAction(session, parsed.action);
  const sessionWithAudit: ObservationSession = {
    ...dispatched.session,
    voiceActions: [...dispatched.session.voiceActions, voiceActionRecord],
  };

  return {
    session: sessionWithAudit,
    outcome: dispatched.outcome,
    voiceActionRecord,
  };
}

/**
 * Validates that a derived `MediaVariant` remains synchronized with the immutable
 * `SourceAsset` timeline (`PBT-SYNC-1`):
 * - Frame timestamps are strictly monotonic and finite
 * - Audio stream preservation matches source audio presence
 * - Audio/duration sync drift is bounded (`<= 42.0 ms`)
 */
export function validateMediaTimelineSync(
  source: SourceAsset,
  variant: MediaVariant
): {
  valid: boolean;
  monotonic: boolean;
  maxDriftMs: number;
  audioPreserved: boolean;
} {
  const timestamps = variant.frameTimestampsMs;
  let monotonic = timestamps.length > 0;
  for (let i = 0; i < timestamps.length; i++) {
    const t = timestamps[i];
    if (t === undefined || !Number.isFinite(t) || t < 0) {
      monotonic = false;
      break;
    }
    const prev = i > 0 ? timestamps[i - 1] : undefined;
    if (prev !== undefined && t <= prev) {
      monotonic = false;
      break;
    }
  }

  const audioPreserved =
    source.audioStreams.length === 0 ? true : variant.audioStreamCopiedFromSource === true;

  const durationDriftMs = Math.abs(variant.durationMs - source.durationMs);
  const audioOffsetDriftMs = Math.abs(variant.audioSyncOffsetMs);
  const lastFrameTs = timestamps.length > 0 ? timestamps[timestamps.length - 1] : undefined;
  const lastFrameDriftMs =
    lastFrameTs !== undefined && lastFrameTs > source.durationMs
      ? lastFrameTs - source.durationMs
      : 0;

  const maxDriftMs = Number(
    Math.max(durationDriftMs, audioOffsetDriftMs, lastFrameDriftMs).toFixed(4)
  );

  const sameSourceAsset = variant.sourceAssetId === source.id;
  const valid =
    sameSourceAsset &&
    monotonic &&
    audioPreserved &&
    maxDriftMs <= MAX_ALLOWED_MEDIA_DRIFT_MS;

  return {
    valid,
    monotonic,
    maxDriftMs,
    audioPreserved,
  };
}
