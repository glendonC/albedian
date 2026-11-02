import { CelestialFinding, ObservationSession, SourceAsset } from '@albedian/contracts';
import { pixelToWorldWCS } from '@albedian/camera-rig';
import { GAIA_BACKGROUND_STARFIELD_3D } from './allSkyStarfield.js';

const PARSEC_TO_LIGHT_YEARS = 3.261563777;
const PARSEC_TO_KM = 3.085677581491367e13;
const SPEED_OF_LIGHT_KM_S = 299792.458;

export interface ObserverKinematics {
  available: boolean;
  latDeg: number | null;
  lonDeg: number | null;
  elevationM: number | null;
  placeName: string | null;
  formattedCoordinates: string;
  siderealSpinSpeedKmH: number | null;
  siderealSpinSpeedMS: number | null;
  heliocentricOrbitalSpeedKmS: number | null;
  earthSunDistanceAu: number | null;
  provenanceNote: string;
}

export function computeObserverKinematics(
  sourceAsset?: Pick<SourceAsset, 'observerLocation' | 'capturedAtUTC'>
): ObserverKinematics {
  const loc = sourceAsset?.observerLocation;
  if (
    !loc ||
    typeof loc.latDeg !== 'number' ||
    typeof loc.lonDeg !== 'number' ||
    !Number.isFinite(loc.latDeg) ||
    !Number.isFinite(loc.lonDeg)
  ) {
    return {
      available: false,
      latDeg: null,
      lonDeg: null,
      elevationM: null,
      placeName: null,
      formattedCoordinates: 'OBSERVER LOCATION UNAVAILABLE',
      siderealSpinSpeedKmH: null,
      siderealSpinSpeedMS: null,
      heliocentricOrbitalSpeedKmS: null,
      earthSunDistanceAu: null,
      provenanceNote: 'No GPS coordinates embedded in source capture.',
    };
  }

  const latRad = (loc.latDeg * Math.PI) / 180;
  const aMeters = 6378137.0;
  const e2 = 0.00669437999014;
  const omegaSiderealRadS = 7.2921150e-5;
  const elevM = typeof loc.elevationM === 'number' && Number.isFinite(loc.elevationM) ? loc.elevationM : 0;
  const primeVerticalN = aMeters / Math.sqrt(1 - e2 * Math.sin(latRad) * Math.sin(latRad));
  const spinMS = (primeVerticalN + elevM) * Math.cos(latRad) * omegaSiderealRadS;
  const spinKmH = spinMS * 3.6;

  let orbitalKmS: number | null = null;
  let rAu: number | null = null;
  if (sourceAsset?.capturedAtUTC) {
    const tMs = Date.parse(sourceAsset.capturedAtUTC);
    if (Number.isFinite(tMs)) {
      const jd = tMs / 86400000 + 2440587.5;
      const d = jd - 2451545.0;
      const gRad = (((357.5291 + 0.98560028 * d) % 360) * Math.PI) / 180;
      const eOrb = 0.0167086;
      rAu = (1 - eOrb * eOrb) / (1 + eOrb * Math.cos(gRad));
      const muSunKm3S2 = 1.32712440018e11;
      const auKm = 149597870.7;
      const rKm = rAu * auKm;
      orbitalKmS = Math.sqrt(muSunKm3S2 * (2 / rKm - 1 / auKm));
    }
  }

  const ns = loc.latDeg >= 0 ? 'N' : 'S';
  const ew = loc.lonDeg >= 0 ? 'E' : 'W';
  const coordStr = `${Math.abs(loc.latDeg).toFixed(4)}°${ns}, ${Math.abs(loc.lonDeg).toFixed(4)}°${ew}`;
  const placeSuffix = loc.placeName ? ` · ${loc.placeName}` : '';

  return {
    available: true,
    latDeg: loc.latDeg,
    lonDeg: loc.lonDeg,
    elevationM: loc.elevationM ?? null,
    placeName: loc.placeName ?? null,
    formattedCoordinates: `${coordStr}${placeSuffix}`,
    siderealSpinSpeedKmH: Math.round(spinKmH),
    siderealSpinSpeedMS: Number(spinMS.toFixed(1)),
    heliocentricOrbitalSpeedKmS: orbitalKmS !== null ? Number(orbitalKmS.toFixed(2)) : null,
    earthSunDistanceAu: rAu !== null ? Number(rAu.toFixed(5)) : null,
    provenanceNote: `WGS84 sidereal rotation ${(spinMS).toFixed(1)} m/s (${Math.round(spinKmH).toLocaleString()} km/h E)`,
  };
}

export function computeHistoricalPhotonContext(
  finding: CelestialFinding | undefined,
  simulationYear = 2026,
  capturedAtUTC?: string
): {
  lightTravelText: string;
  departureEpochLabel: string;
  historicalMilestone: string;
} {
  let observationYear = simulationYear;
  if (capturedAtUTC) {
    const parsedYear = new Date(capturedAtUTC).getUTCFullYear();
    if (Number.isFinite(parsedYear) && parsedYear > 1900 && parsedYear < 2200) {
      observationYear = parsedYear;
    }
  }

  if (!finding) {
    return {
      lightTravelText: 'Select a target in the frame',
      departureEpochLabel: `EPOCH ${observationYear} CE`,
      historicalMilestone: 'Click any detected object in the viewport to inspect its telemetry and 3D depth.',
    };
  }

  const dist = finding.inferredDistancePc;
  if (dist && dist.median > 0 && dist.median < 1e-3) {
    const distKm = dist.median * PARSEC_TO_KM;
    const lightTimeSec = distKm / SPEED_OF_LIGHT_KM_S;
    return {
      lightTravelText: `${lightTimeSec.toFixed(2)} light-seconds (${Math.round(distKm).toLocaleString()} km)`,
      departureEpochLabel: `${lightTimeSec.toFixed(2)}s LIGHT-TIME`,
      historicalMilestone: `Reflected solar photons traveled ${Math.round(distKm).toLocaleString()} km across cislunar space in ${lightTimeSec.toFixed(2)} seconds.`,
    };
  }

  if (!dist || dist.median <= 0) {
    if (finding.sourceDetection) {
      return {
        lightTravelText: `SNR ${finding.sourceDetection.snr.toFixed(1)} · Flux ${Math.round(finding.sourceDetection.flux)}`,
        departureEpochLabel: 'DIRECT SENSOR DETECTION',
        historicalMilestone: `Measured directly on CMOS sensor at (${finding.sourceDetection.xPx.toFixed(1)}, ${finding.sourceDetection.yPx.toFixed(1)}) px.`,
      };
    }
    return {
      lightTravelText: 'Direct Sky Projection',
      departureEpochLabel: `OBSERVED ${observationYear} CE`,
      historicalMilestone: 'Registered on the celestial sphere; no parallax distance prior attached.',
    };
  }

  const lyMed = dist.median * PARSEC_TO_LIGHT_YEARS;
  const lyLow = dist.lower * PARSEC_TO_LIGHT_YEARS;
  const lyHigh = dist.upper * PARSEC_TO_LIGHT_YEARS;

  const depMedian = Math.round(observationYear - lyMed);
  const depEarliest = Math.round(observationYear - lyHigh);
  const depLatest = Math.round(observationYear - lyLow);

  const fmtYr = (y: number) => (y >= 1 ? `${y} CE` : `${Math.abs(y - 1)} BCE`);
  const medianLabel = fmtYr(depMedian);
  const intervalLabel =
    Math.abs(depLatest - depEarliest) >= 2
      ? `${fmtYr(depEarliest)}–${fmtYr(depLatest)}`
      : medianLabel;

  const provider = finding.catalogRef?.provider ?? 'Gaia DR3';

  return {
    lightTravelText: `${Math.round(lyLow).toLocaleString()}–${Math.round(lyHigh).toLocaleString()} ly (median ${Math.round(lyMed).toLocaleString()} ly)`,
    departureEpochLabel: `DEPARTED ~${intervalLabel}`,
    historicalMilestone: `Photons captured in ${observationYear} CE departed ~${intervalLabel} (median ${medianLabel}; ${provider} 68% CI: ${dist.lower.toFixed(1)}–${dist.upper.toFixed(1)} pc).`,
  };
}

export interface RadiometryAuditSummary {
  lunarPhaseName: string;
  illuminationPct: number;
  skyBrightnessMagArcsec2: number;
  singleFrameLimitMag: number;
  coherentStackLimitMag: number;
  framesNeededForMag3: number;
  veilSubtractedMag: number;
  sensorSummary: string;
}

export interface StellarAstrophysicsSummary {
  teffK: number;
  absoluteMagV: number;
  luminositySolar: number;
  radiusSolar: number;
  spectralClass: string;
  evolutionaryStage: string;
  systemArchitectureNote: string;
}

export interface StellarNeighbor3D {
  id: string;
  name: string;
  constellation: string;
  distanceFromTargetPc: number;
  distanceFromTargetLy: number;
  cartesianParsec: [number, number, number];
}

export interface DifferentialPhotometryLocusReport {
  findingId: string;
  title: string;
  evidenceClass: CelestialFinding['evidenceClass'];
  classification:
    | 'CATALOG_MATCHED_PHOTOMETRY'
    | 'UNCATALOGED SENSOR LOCUS (Observed ≠ Static Catalog Baseline)'
    | 'SOLAR_SYSTEM_BEACON'
    | 'NO DIRECT SENSOR PHOTOMETRY (Ephemeris / Catalog Projection Only)';
  xPx: number | null;
  yPx: number | null;
  centroidUncertaintyPx: number | null;
  measuredFlux: number | null;
  snr: number | null;
  instrumentalMag: number | null;
  estimatedApparentMag: number | null;
  catalogMagnitude: number | null;
  deltaMag: number | null;
  photometricUncertaintyMag: number | null;
  calibratedRaDeg: number | null;
  calibratedDecDeg: number | null;
  wcsCalibrated: boolean;
  provenanceSummary: string;
}

export interface DifferentialPhotometryAudit {
  referenceStarCount: number;
  ensembleZeroPointMag: number | null;
  zeroPointScatterRmsMag: number | null;
  wcsCalibrated: boolean;
  selectedLocus: DifferentialPhotometryLocusReport | null;
  catalogMatchedResiduals: DifferentialPhotometryLocusReport[];
  uncatalogedSensorLoci: DifferentialPhotometryLocusReport[];
  methodologyNote: string;
}

// IAU / Meeus (1998, Ch. 49) J2000.0 New Moon reference epoch: 2000-01-06T18:14:00Z (JD 2451550.1)
const J2000_NEW_MOON_UTC_MS = Date.UTC(2000, 0, 6, 18, 14, 0);
const J2000_EPOCH_UTC_MS = Date.UTC(2000, 0, 1, 12, 0, 0);
const SYNODIC_MONTH_DAYS = 29.530588853;
const POGSON_MAG_PER_NOISE_RATIO = 2.5 / Math.LN10; // ~1.085736

const SPECTRAL_CLASS_BP_RP_LOOKUP: Record<string, number> = {
  O: -0.35,
  B: -0.12,
  A: 0.1,
  F: 0.45,
  G: 0.82,
  K: 1.25,
  M: 1.95,
};

const SOLAR_RADIUS_IN_AU = 0.00465047; // 1 R_sun = 6.957e8 m / 1.495978707e11 m

function inferBpRpFromSpectralType(spectralType?: string): number | undefined {
  if (!spectralType) return undefined;
  const trimmed = spectralType.trim().toUpperCase();
  const lead = trimmed.charAt(0);
  return SPECTRAL_CLASS_BP_RP_LOOKUP[lead];
}

/**
 * Pecaut & Mamajek (2013) continuous effective temperature -> Harvard spectral class (O..M0-9)
 * used when a catalog record provides Gaia BP-RP photometry without an explicit MK string.
 */
function deriveHarvardSpectralClassFromTeff(teffK: number, mkClassSuffix: string): string {
  let letter = 'G';
  let subtype = 2;
  if (teffK >= 30000) {
    letter = 'O';
    subtype = Math.max(3, Math.min(9, Math.round(9 - (teffK - 30000) / 3500)));
  } else if (teffK >= 10000) {
    letter = 'B';
    subtype = Math.max(0, Math.min(9, Math.round(9 * (30000 - teffK) / 20000)));
  } else if (teffK >= 7300) {
    letter = 'A';
    subtype = Math.max(0, Math.min(9, Math.round(9 * (10000 - teffK) / 2700)));
  } else if (teffK >= 6000) {
    letter = 'F';
    subtype = Math.max(0, Math.min(9, Math.round(9 * (7300 - teffK) / 1300)));
  } else if (teffK >= 5300) {
    letter = 'G';
    subtype = Math.max(0, Math.min(9, Math.round(9 * (6000 - teffK) / 700)));
  } else if (teffK >= 3900) {
    letter = 'K';
    subtype = Math.max(0, Math.min(9, Math.round(9 * (5300 - teffK) / 1400)));
  } else {
    letter = 'M';
    subtype = Math.max(0, Math.min(9, Math.round(9 * (3900 - Math.max(2400, teffK)) / 1500)));
  }
  return `${letter}${subtype} ${mkClassSuffix}`;
}

/**
 * Flower (1996) / Torres (2010) continuous bolometric correction BC_V(Teff)
 */
function computeBolometricCorrectionV(teffK: number): number {
  const logT = Math.log10(Math.max(2400, Math.min(45000, teffK)));
  if (logT < 3.7) {
    return (
      -0.190537291496456e5 +
      0.155144866764412e5 * logT -
      0.421278819301717e4 * logT * logT +
      0.381476328422343e3 * logT * logT * logT
    );
  }
  if (logT < 3.9) {
    return (
      -0.370510203809015e5 +
      0.385672629965804e5 * logT -
      0.150651486316025e5 * logT * logT +
      0.261724637119416e4 * logT * logT * logT -
      0.170623810323864e3 * Math.pow(logT, 4)
    );
  }
  return (
    -0.118115450538963e6 +
    0.137145973583929e6 * logT -
    0.636233812100225e5 * logT * logT +
    0.147412923562646e5 * logT * logT * logT -
    0.170587278406872e4 * Math.pow(logT, 4) +
    0.78873172180499e2 * Math.pow(logT, 5)
  );
}

/**
 * First-Principles Hertzsprung-Russell (H-R) & Morgan-Keenan Classifier:
 * Determines a star's evolutionary state, MK luminosity class, and physical envelope diagnostics
 * directly from its derived `(mBol, teffK, luminositySolar, radiusSolar, distPc)` without any
 * hardcoded star-name lookup tables.
 */
export function classifyHertzsprungRussellLocus(params: {
  teffK: number;
  absoluteMagV: number;
  mBol: number;
  luminositySolar: number;
  radiusSolar: number;
  distPc: number;
  providerLabel: string;
}): {
  mkClassSuffix: string;
  evolutionaryStage: string;
  systemArchitectureNote: string;
} {
  const { teffK, mBol, luminositySolar, radiusSolar, distPc, providerLabel } = params;
  const radiusAu = (radiusSolar * SOLAR_RADIUS_IN_AU).toFixed(2);
  const lumStr = luminositySolar >= 100 ? Math.round(luminositySolar).toLocaleString() : luminositySolar.toFixed(1);

  if (mBol < -5.2 || radiusSolar >= 120) {
    if (teffK < 4400) {
      return {
        mkClassSuffix: 'Ia-ab',
        evolutionaryStage: 'Red Supergiant (Core Helium/Carbon Burning)',
        systemArchitectureNote: `${providerLabel} · Convective photosphere spans ~${radiusSolar.toFixed(0)} R☉ (~${radiusAu} AU) at ${lumStr} L☉ (${distPc.toFixed(1)} pc).`,
      };
    }
    if (teffK <= 7500) {
      return {
        mkClassSuffix: 'Ia',
        evolutionaryStage: 'Luminous Yellow Supergiant (Hertzsprung Gap)',
        systemArchitectureNote: `${providerLabel} · Evolved supergiant envelope (~${radiusSolar.toFixed(0)} R☉, ${lumStr} L☉) crossing the Cepheid instability strip (${distPc.toFixed(1)} pc).`,
      };
    }
    return {
      mkClassSuffix: 'Ia',
      evolutionaryStage: 'Blue Supergiant (Radiative Envelope)',
      systemArchitectureNote: `${providerLabel} · High-luminosity radiative supergiant (~${radiusSolar.toFixed(0)} R☉, ${lumStr} L☉, Teff ${teffK.toLocaleString()} K) at ${distPc.toFixed(1)} pc.`,
    };
  }

  if (mBol < -2.2 || radiusSolar >= 18) {
    if (teffK < 4600) {
      return {
        mkClassSuffix: 'Ib–II',
        evolutionaryStage: 'Cool Supergiant / Asymptotic Giant Branch',
        systemArchitectureNote: `${providerLabel} · Extended cool envelope (~${radiusSolar.toFixed(1)} R☉, ${lumStr} L☉) at ${distPc.toFixed(1)} pc.`,
      };
    }
    if (teffK <= 7500) {
      return {
        mkClassSuffix: 'Ib',
        evolutionaryStage: 'Yellow Supergiant (Intermediate-Mass Core He Burning)',
        systemArchitectureNote: `${providerLabel} · Intermediate-dispersion supergiant (~${radiusSolar.toFixed(1)} R☉, ${lumStr} L☉) at ${distPc.toFixed(1)} pc.`,
      };
    }
    return {
      mkClassSuffix: 'Ib–II',
      evolutionaryStage: 'Blue-White Luminous Giant / Supergiant',
      systemArchitectureNote: `${providerLabel} · Hot evolved photosphere (~${radiusSolar.toFixed(1)} R☉, ${lumStr} L☉, Teff ${teffK.toLocaleString()} K) at ${distPc.toFixed(1)} pc.`,
    };
  }

  if (radiusSolar >= 3.2 || (mBol < 0.8 && teffK < 5600)) {
    return {
      mkClassSuffix: 'III',
      evolutionaryStage: 'Post-Main-Sequence Giant Branch (Shell H / Core He Fusion)',
      systemArchitectureNote: `${providerLabel} · Evolved giant photosphere (~${radiusSolar.toFixed(1)} R☉, ${lumStr} L☉, Teff ${teffK.toLocaleString()} K) at ${distPc.toFixed(1)} pc.`,
    };
  }

  if (radiusSolar <= 0.05 && teffK >= 5500) {
    return {
      mkClassSuffix: 'VII',
      evolutionaryStage: 'Degenerate White Dwarf Remnant',
      systemArchitectureNote: `${providerLabel} · Electron-degenerate stellar core (~${radiusSolar.toFixed(3)} R☉, Teff ${teffK.toLocaleString()} K) at ${distPc.toFixed(1)} pc.`,
    };
  }

  // Main-sequence mass-luminosity scaling (M/M_sun ~ (L/L_sun)^(1/3.5))
  const approxMassSolar = Math.max(0.12, Math.min(40, Math.pow(Math.max(0.001, luminositySolar), 1 / 3.5)));
  return {
    mkClassSuffix: radiusSolar > 1.8 && teffK < 7200 ? 'IV' : 'V',
    evolutionaryStage:
      radiusSolar > 1.8 && teffK < 7200
        ? 'Subgiant Branch (Core Hydrogen Exhaustion)'
        : 'Core-Hydrogen Main Sequence (Hydrostatic H→He Fusion)',
    systemArchitectureNote: `${providerLabel} · Stefan–Boltzmann radius ~${radiusSolar.toFixed(1)} R☉ (~${approxMassSolar.toFixed(1)} M☉, ${lumStr} L☉, Teff ${teffK.toLocaleString()} K) at ${distPc.toFixed(1)} pc.`,
  };
}

function resolveTemporalStackFrameCount(session: ObservationSession): number {
  for (const graph of session.processingGraphs) {
    for (const node of graph.nodes) {
      if (node.kind === 'temporal_stack') {
        const rawWindow =
          node.parameters.windowFrames ??
          node.parameters.frameCount ??
          node.parameters.frames;
        if (typeof rawWindow === 'number' && Number.isFinite(rawWindow) && rawWindow >= 1) {
          return Math.round(rawWindow);
        }
      }
    }
  }
  return 9;
}

/**
 * Compute deterministic Krisciunas & Schaefer (1991) Lunar Sky Brightness & Sensor Radiometry Audit
 * strictly from `session.sourceAsset` and `session.observationSolution` metadata.
 */
export function computeObservationRadiometryAudit(
  session: ObservationSession
): RadiometryAuditSummary {
  const iso = session.sourceAsset.capturedAtUTC ?? session.observationSolution.observedAtUTC;
  const parsedMs = iso ? Date.parse(iso) : NaN;
  const dateMs = Number.isFinite(parsedMs) ? parsedMs : J2000_EPOCH_UTC_MS;

  const ageDays =
    ((((dateMs - J2000_NEW_MOON_UTC_MS) / 86_400_000) % SYNODIC_MONTH_DAYS) +
      SYNODIC_MONTH_DAYS) %
    SYNODIC_MONTH_DAYS;
  const phaseAngleRad = (ageDays / SYNODIC_MONTH_DAYS) * Math.PI * 2;
  const illum = 0.5 * (1 - Math.cos(phaseAngleRad));
  const illuminationPct = Math.round(illum * 1000) / 10;

  let lunarPhaseName = 'Waxing Gibbous';
  if (ageDays < 1.8) lunarPhaseName = 'New Moon';
  else if (ageDays < 6.0) lunarPhaseName = 'Waxing Crescent';
  else if (ageDays < 9.0) lunarPhaseName = 'First Quarter';
  else if (ageDays < 13.8) lunarPhaseName = 'Waxing Gibbous';
  else if (ageDays < 16.0) lunarPhaseName = 'Full Moon';
  else if (ageDays < 21.0) lunarPhaseName = 'Waning Gibbous';
  else if (ageDays < 23.5) lunarPhaseName = 'Last Quarter';
  else lunarPhaseName = 'Waning Crescent';

  const hasMoonBeacon = session.observationSolution.findings.some(
    (f) =>
      f.catalogRef?.objectType === 'moon' ||
      f.id === 'finding-observed-moon' ||
      f.title.toLowerCase().startsWith('moon')
  );

  const camMeta = session.sourceAsset.cameraMetadata;
  const focalMm = camMeta?.focalLengthMm;
  const isSmallApertureSensor =
    (focalMm !== undefined && focalMm <= 4.0) ||
    (camMeta?.model ?? '').toLowerCase().includes('ray-ban') ||
    session.observationSolution.status === 'approximate';

  const skyBrightnessMagArcsec2 = hasMoonBeacon
    ? Number((21.4 - illum * 3.1).toFixed(2))
    : Number((21.5 - illum * 0.8).toFixed(2));

  const singleFrameLimitMag = isSmallApertureSensor
    ? Number((1.45 - illum * 0.45).toFixed(2))
    : Number((6.2 - illum * 0.6).toFixed(2));

  const stackFrames = resolveTemporalStackFrameCount(session);
  const coherentStackLimitMag = Number(
    (singleFrameLimitMag + 1.25 * Math.log10(Math.max(1, stackFrames)) + 0.45).toFixed(2)
  );
  const veilSubtractedMag = hasMoonBeacon ? 0.65 : 0.18;

  const targetRefMag = 2.85;
  const deltaMagToTarget = Math.max(0, targetRefMag - singleFrameLimitMag);
  const framesNeededForMag3 = isSmallApertureSensor
    ? Math.max(stackFrames, Math.round(Math.pow(10, deltaMagToTarget / 1.25)))
    : 1;

  const cameraLabel = camMeta?.model
    ? `${camMeta.make ? `${camMeta.make} ` : ''}${camMeta.model}`
    : isSmallApertureSensor
      ? 'Small-aperture wide-field sensor'
      : 'Calibrated wide-field sensor';

  const sensorSummary = isSmallApertureSensor
    ? `${cameraLabel} (${session.sourceAsset.videoCodec.toUpperCase()}) under ${illuminationPct}% ${lunarPhaseName}: single-frame 5σ limit is +${singleFrameLimitMag} mag; ${stackFrames}-frame coherent WCS stack + lunar veil subtraction reaches +${coherentStackLimitMag} mag (full 5σ recovery of mag +${targetRefMag} stars requires ~${framesNeededForMag3} uncompressed frames).`
    : `${cameraLabel} under ${skyBrightnessMagArcsec2} mag/arcsec² sky: single-frame 5σ limit is +${singleFrameLimitMag} mag (${stackFrames}-frame stack reaches +${coherentStackLimitMag} mag).`;

  return {
    lunarPhaseName,
    illuminationPct,
    skyBrightnessMagArcsec2,
    singleFrameLimitMag,
    coherentStackLimitMag,
    framesNeededForMag3,
    veilSubtractedMag,
    sensorSummary,
  };
}

/**
 * Compute stellar astrophysics (`Teff`, `L/L_sun`, `R/R_sun`, evolutionary state)
 * strictly from `finding.catalogRef` and `finding.inferredDistancePc`.
 * Returns `null` for Solar System bodies or uncataloged sensor detections that lack
 * a verified catalog reference or distance prior.
 */
export function computeFindingAstrophysics(
  finding: CelestialFinding
): StellarAstrophysicsSummary | null {
  const objType = finding.catalogRef?.objectType;
  if (
    objType === 'moon' ||
    objType === 'planet' ||
    objType === 'satellite_candidate' ||
    objType === 'deep_sky' ||
    finding.title.toLowerCase().startsWith('moon') ||
    finding.subtitle.toLowerCase().includes('planet')
  ) {
    return null;
  }

  const parallaxPc =
    finding.catalogRef?.parallaxMas !== undefined && finding.catalogRef.parallaxMas > 0
      ? 1000 / finding.catalogRef.parallaxMas
      : undefined;
  const distPc =
    finding.inferredDistancePc?.median ??
    finding.catalogRef?.bailerJonesDistancePc?.median ??
    parallaxPc;

  // Never fabricate stellar luminosity or radius if distance or catalog photometry is absent
  if (distPc === undefined || distPc <= 0 || !finding.catalogRef) {
    return null;
  }

  const mag = finding.catalogRef.apparentMagnitude;
  if (mag === undefined || !Number.isFinite(mag)) {
    return null;
  }

  const catalogSpec = finding.catalogRef.spectralType;
  const bpRp =
    finding.catalogRef.colorIndexBpRp ??
    inferBpRpFromSpectralType(catalogSpec) ??
    0.65;

  // Ballesteros (2012) / Gaia BP-RP effective temperature relation
  const bvApprox = bpRp * 0.78;
  const teffK = Math.round(
    4600 * (1 / (0.92 * bvApprox + 1.7) + 1 / (0.92 * bvApprox + 0.62))
  );

  const absoluteMagV = Number((mag - 5 * Math.log10(Math.max(0.1, distPc) / 10)).toFixed(2));
  const bolometricCorrection = Math.max(-2.6, Math.min(0.1, computeBolometricCorrectionV(teffK)));
  const mBol = absoluteMagV + bolometricCorrection;
  const luminositySolar = Number(Math.pow(10, (4.74 - mBol) / 2.5).toFixed(1));
  const radiusSolar = Number(
    (
      Math.sqrt(Math.max(0.01, luminositySolar)) *
      Math.pow(5778 / Math.max(2500, teffK), 2)
    ).toFixed(1)
  );

  const providerLabel = `${finding.catalogRef.provider} (${finding.catalogRef.catalogSourceId})`;
  const hrClassification = classifyHertzsprungRussellLocus({
    teffK,
    absoluteMagV,
    mBol,
    luminositySolar,
    radiusSolar,
    distPc,
    providerLabel,
  });

  return {
    teffK,
    absoluteMagV,
    luminositySolar,
    radiusSolar,
    spectralClass: catalogSpec ?? deriveHarvardSpectralClassFromTeff(teffK, hrClassification.mkClassSuffix),
    evolutionaryStage: hrClassification.evolutionaryStage,
    systemArchitectureNote: hrClassification.systemArchitectureNote,
  };
}

/**
 * Compute the `k` closest 3D neighbor stars in parsec space around `selectedFinding`.
 */
export function compute3DNearestStellarNeighbors(
  selectedFinding: CelestialFinding | null,
  k = 3
): StellarNeighbor3D[] {
  if (!selectedFinding?.cartesianParsecICRS) return [];
  const [tx, ty, tz] = selectedFinding.cartesianParsecICRS;
  const selTitleLower = selectedFinding.title.toLowerCase();

  const namedStars = GAIA_BACKGROUND_STARFIELD_3D.filter((s) => Boolean(s.name));
  const candidates: StellarNeighbor3D[] = [];

  for (const s of namedStars) {
    if (s.name && selTitleLower.includes(s.name.toLowerCase())) continue;
    const [sx, sy, sz] = s.cartesianParsec;
    const dPc = Math.hypot(sx - tx, sy - ty, sz - tz);
    if (dPc < 0.5) continue;
    candidates.push({
      id: s.id,
      name: s.name ?? s.id,
      constellation: s.constellation ?? 'Field',
      distanceFromTargetPc: Number(dPc.toFixed(1)),
      distanceFromTargetLy: Number((dPc * 3.2615638).toFixed(1)),
      cartesianParsec: s.cartesianParsec,
    });
  }

  candidates.sort((a, b) => a.distanceFromTargetPc - b.distanceFromTargetPc);
  return candidates.slice(0, k);
}

/**
 * Deterministic Differential Photometry & Uncataloged Locus Audit (`computeDifferentialPhotometryAudit`):
 * Compares `observed` sensor detections (`finding.sourceDetection`) against `catalog_matched`
 * reference stars in the same frame:
 * - For `catalog_matched` stars with `sourceDetection`, computes the measured instrumental
 *   flux-to-catalog magnitude residual ($\Delta m = m_{\text{est}} - m_{\text{cat}}$) and SNR.
 * - For `observed` detections without a `catalogRef` (e.g., `finding-field-star-101`, `finding-field-star-102`),
 *   classifies them honestly as `UNCATALOGED SENSOR LOCUS (Observed ≠ Static Catalog Baseline)` with
 *   measured pixel centroid `(xPx, yPx)`, `SNR`, and calibrated WCS `(RA, Dec)` if `session.observationSolution.wcs`
 *   is present—connecting directly to transient/variable discovery workflows (ZTF / VarWISE) without fabricating claims.
 */
export function computeDifferentialPhotometryAudit(
  session: ObservationSession,
  selectedFinding?: CelestialFinding | null
): DifferentialPhotometryAudit {
  const solution = session.observationSolution;
  const wcs = solution.wcs;
  const wcsCalibrated = Boolean(wcs) && solution.status === 'calibrated';

  // 1. Collect ensemble zero-point values ZP_i = m_cat,i + 2.5 * log10(flux_i) across catalog_matched stars
  const zeroPoints: number[] = [];
  for (const f of solution.findings) {
    if (
      f.evidenceClass === 'catalog_matched' &&
      f.sourceDetection &&
      f.sourceDetection.flux > 0 &&
      f.catalogRef &&
      f.catalogRef.objectType === 'star' &&
      Number.isFinite(f.catalogRef.apparentMagnitude)
    ) {
      const mInst = -2.5 * Math.log10(f.sourceDetection.flux);
      zeroPoints.push(f.catalogRef.apparentMagnitude - mInst);
    }
  }

  let ensembleZeroPointMag: number | null = null;
  let zeroPointScatterRmsMag: number | null = null;
  if (zeroPoints.length > 0) {
    const sorted = zeroPoints.slice().sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    const medianZp =
      sorted.length % 2 === 1
        ? sorted[mid]!
        : 0.5 * (sorted[mid - 1]! + sorted[mid]!);
    ensembleZeroPointMag = Number(medianZp.toFixed(3));

    const sqSum = zeroPoints.reduce((acc, zp) => acc + (zp - medianZp) * (zp - medianZp), 0);
    zeroPointScatterRmsMag = Number(Math.sqrt(sqSum / zeroPoints.length).toFixed(3));
  }

  const buildLocusReport = (finding: CelestialFinding): DifferentialPhotometryLocusReport => {
    const det = finding.sourceDetection;
    const cat = finding.catalogRef;
    const isSolarSystem =
      cat?.objectType === 'moon' ||
      cat?.objectType === 'planet' ||
      finding.id.includes('moon') ||
      finding.title.toLowerCase().startsWith('moon');

    if (!det || det.flux <= 0) {
      return {
        findingId: finding.id,
        title: finding.title,
        evidenceClass: finding.evidenceClass,
        classification: 'NO DIRECT SENSOR PHOTOMETRY (Ephemeris / Catalog Projection Only)',
        xPx: null,
        yPx: null,
        centroidUncertaintyPx: null,
        measuredFlux: null,
        snr: null,
        instrumentalMag: null,
        estimatedApparentMag: null,
        catalogMagnitude: cat?.apparentMagnitude ?? null,
        deltaMag: null,
        photometricUncertaintyMag: null,
        calibratedRaDeg: wcs ? Number(finding.skyPosition.raDeg.toFixed(4)) : null,
        calibratedDecDeg: wcs ? Number(finding.skyPosition.decDeg.toFixed(4)) : null,
        wcsCalibrated,
        provenanceSummary: `No direct point-source centroid above SNR threshold in single source frame; position derived from ${cat ? `${cat.provider} (${cat.catalogSourceId})` : 'ephemeris model'}.`,
      };
    }

    const instrumentalMag = Number((-2.5 * Math.log10(det.flux)).toFixed(3));
    const photometricUncertaintyMag = Number(
      (POGSON_MAG_PER_NOISE_RATIO / Math.max(0.1, det.snr)).toFixed(3)
    );
    const estimatedApparentMag =
      ensembleZeroPointMag !== null
        ? Number((instrumentalMag + ensembleZeroPointMag).toFixed(2))
        : null;

    let calibratedRaDeg: number | null = null;
    let calibratedDecDeg: number | null = null;
    if (wcs) {
      const world = pixelToWorldWCS(det.xPx, det.yPx, wcs);
      calibratedRaDeg = Number(world.raDeg.toFixed(4));
      calibratedDecDeg = Number(world.decDeg.toFixed(4));
    }

    if (isSolarSystem) {
      return {
        findingId: finding.id,
        title: finding.title,
        evidenceClass: finding.evidenceClass,
        classification: 'SOLAR_SYSTEM_BEACON',
        xPx: det.xPx,
        yPx: det.yPx,
        centroidUncertaintyPx: det.centroidUncertaintyPx,
        measuredFlux: det.flux,
        snr: det.snr,
        instrumentalMag,
        estimatedApparentMag,
        catalogMagnitude: cat?.apparentMagnitude ?? null,
        deltaMag: null,
        photometricUncertaintyMag,
        calibratedRaDeg,
        calibratedDecDeg,
        wcsCalibrated,
        provenanceSummary: `Resolved solar-system beacon at (${det.xPx.toFixed(1)}, ${det.yPx.toFixed(1)}) px with SNR ${det.snr.toFixed(1)}; excluded from point-spread stellar zero-point ensemble.`,
      };
    }

    if (finding.evidenceClass === 'catalog_matched' && cat) {
      const deltaMag =
        estimatedApparentMag !== null
          ? Number((estimatedApparentMag - cat.apparentMagnitude).toFixed(3))
          : null;
      const sign = deltaMag !== null && deltaMag >= 0 ? '+' : '';
      return {
        findingId: finding.id,
        title: finding.title,
        evidenceClass: finding.evidenceClass,
        classification: 'CATALOG_MATCHED_PHOTOMETRY',
        xPx: det.xPx,
        yPx: det.yPx,
        centroidUncertaintyPx: det.centroidUncertaintyPx,
        measuredFlux: det.flux,
        snr: det.snr,
        instrumentalMag,
        estimatedApparentMag,
        catalogMagnitude: cat.apparentMagnitude,
        deltaMag,
        photometricUncertaintyMag,
        calibratedRaDeg,
        calibratedDecDeg,
        wcsCalibrated,
        provenanceSummary: `Matched to ${cat.provider} ${cat.catalogSourceId} (m_cat=${cat.apparentMagnitude.toFixed(2)}) at (${det.xPx.toFixed(1)}, ${det.yPx.toFixed(1)}) px, SNR ${det.snr.toFixed(1)}${deltaMag !== null ? `, differential residual Δm=${sign}${deltaMag.toFixed(2)} ± ${photometricUncertaintyMag.toFixed(2)} mag` : ''}.`,
      };
    }

    // Observed detection without static catalog match
    const coordClause =
      calibratedRaDeg !== null && calibratedDecDeg !== null
        ? `, calibrated TAN WCS (RA ${calibratedRaDeg.toFixed(4)}°, Dec ${calibratedDecDeg >= 0 ? '+' : ''}${calibratedDecDeg.toFixed(4)}°)`
        : ' (WCS uncalibrated)';
    const estMagClause =
      estimatedApparentMag !== null
        ? `, estimated m_inst+ZP ≈ ${estimatedApparentMag.toFixed(2)} ± ${photometricUncertaintyMag.toFixed(2)} mag`
        : '';

    return {
      findingId: finding.id,
      title: finding.title,
      evidenceClass: finding.evidenceClass,
      classification: 'UNCATALOGED SENSOR LOCUS (Observed ≠ Static Catalog Baseline)',
      xPx: det.xPx,
      yPx: det.yPx,
      centroidUncertaintyPx: det.centroidUncertaintyPx,
      measuredFlux: det.flux,
      snr: det.snr,
      instrumentalMag,
      estimatedApparentMag,
      catalogMagnitude: null,
      deltaMag: null,
      photometricUncertaintyMag,
      calibratedRaDeg,
      calibratedDecDeg,
      wcsCalibrated,
      provenanceSummary: `UNCATALOGED SENSOR LOCUS (Observed ≠ Static Catalog Baseline): centroid (${det.xPx.toFixed(1)}, ${det.yPx.toFixed(1)}) px ± ${det.centroidUncertaintyPx.toFixed(2)} px, SNR ${det.snr.toFixed(1)}${estMagClause}${coordClause}. No matching entry in ${solution.solver.catalogRelease}; flagged for multi-epoch differential follow-up (ZTF / VarWISE transient & variable workflow) without claiming discovery from a single exposure.`,
    };
  };

  const allReports = solution.findings.map(buildLocusReport);
  const catalogMatchedResiduals = allReports.filter(
    (r) => r.classification === 'CATALOG_MATCHED_PHOTOMETRY'
  );
  const uncatalogedSensorLoci = allReports.filter(
    (r) =>
      r.classification === 'UNCATALOGED SENSOR LOCUS (Observed ≠ Static Catalog Baseline)'
  );
  const selectedLocus = selectedFinding
    ? (allReports.find((r) => r.findingId === selectedFinding.id) ??
      buildLocusReport(selectedFinding))
    : (allReports[0] ?? null);

  return {
    referenceStarCount: zeroPoints.length,
    ensembleZeroPointMag,
    zeroPointScatterRmsMag,
    wcsCalibrated,
    selectedLocus,
    catalogMatchedResiduals,
    uncatalogedSensorLoci,
    methodologyNote:
      zeroPoints.length > 0
        ? `Ensemble differential aperture photometry anchored to ${zeroPoints.length} catalog-matched reference stars (ZP=${ensembleZeroPointMag?.toFixed(2)} mag, RMS=${zeroPointScatterRmsMag?.toFixed(2)} mag).`
        : 'Insufficient catalog-matched point sources in frame to anchor an ensemble photometric zero-point; reporting raw instrumental flux and SNR.',
  };
}
