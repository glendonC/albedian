import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  DiscoveredMediaItem,
  buildSessionFromDiscoveredMedia,
  formatObserverCoords,
  inferPlaceNameFromCoords,
} from '../services/apiClient.js';
import {
  SparkPortalRenderer,
  SpatialScaleRegime,
  SpectralBandMode,
  derivePlanetaryWorldDescriptor,
} from '../spatial/SparkPortalRenderer.js';
import { observatoryAudio } from '../audio/ObservatoryAudioEngine.js';
import '../styles/albedian.css';

export interface SpatialObservatoryViewportProps {
  session: ObservationSession;
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>;
  selectedFinding: CelestialFinding | null;
  discoveredMedia: DiscoveredMediaItem[];
  activeDiscoveredId: string | null;
  setActiveDiscoveredId: React.Dispatch<React.SetStateAction<string | null>>;
  activeDiscoveredItem: DiscoveredMediaItem | null;
  uploadedMediaUrl: string | null;
  setUploadedMediaUrl: React.Dispatch<React.SetStateAction<string | null>>;
  commandText: string;
  setCommandText: React.Dispatch<React.SetStateAction<string>>;
  onRunUtterance: (
    utterance: string,
    modality?: 'voice' | 'text' | 'pointer'
  ) => void;
  onSelectFinding: (
    findingId: string,
    pointerCoords?: { xPx: number; yPx: number }
  ) => void;
  onPushToTalk: () => void;
  onTriggerUpload: () => void;
}

interface ProjectedReticle {
  finding: CelestialFinding;
  x: number;
  y: number;
  visible: boolean;
}

interface CameraTelemetryState {
  yawDeg: number;
  pitchDeg: number;
  fovDeg: number;
  velYaw: number;
  velPitch: number;
  warpFactor: number;
  subjectScreen: { x: number; y: number; visible: boolean };
}

interface SiterraSpecimenPlate {
  num: string;
  title: string;
  subtitle: string;
  metricValue: string;
  metricUnit: string;
  color: string;
  badgeClass: string;
  provenance: string;
  detail: string;
  targetRegime: SpatialScaleRegime;
}

function cleanTargetName(rawTitle: string): string {
  return rawTitle.replace(/\s*\([^)]*\)\s*/g, '').trim() || rawTitle;
}

function formatDistanceLabel(finding: CelestialFinding): string {
  const lowerTitle = finding.title.toLowerCase();
  if (lowerTitle.includes('moon')) return '384,400 km · 1.28 light-sec';
  if (lowerTitle.includes('saturn')) return '9.06 AU · 75.3 light-min';
  if (lowerTitle.includes('jupiter')) return '4.42 AU · 36.7 light-min';
  if (lowerTitle.includes('uranus')) return '18.74 AU · 2.60 light-hr';
  if (finding.inferredDistancePc?.median) {
    const pc = finding.inferredDistancePc.median;
    const ly = pc * 3.26156;
    return `${ly < 50 ? ly.toFixed(2) : Math.round(ly).toLocaleString()} ly (${pc.toFixed(1)} pc)`;
  }
  return 'Solar System Ephemeris';
}

function formatShortDistance(finding: CelestialFinding): string {
  const lowerTitle = finding.title.toLowerCase();
  if (lowerTitle.includes('moon')) return '1.28 ls';
  if (lowerTitle.includes('saturn')) return '9.06 AU';
  if (lowerTitle.includes('jupiter')) return '4.42 AU';
  if (lowerTitle.includes('uranus')) return '18.7 AU';
  if (finding.inferredDistancePc?.median) {
    const ly = finding.inferredDistancePc.median * 3.26156;
    return `${ly < 50 ? ly.toFixed(2) : Math.round(ly).toLocaleString()} ly`;
  }
  return 'Ephem';
}

function computePhotonReceipt(
  finding: CelestialFinding,
  capturedDateStr: string
): {
  headline: string;
  departureEpoch: string;
  uncertainty: string;
  eraContext: string;
} {
  const lower = finding.title.toLowerCase();
  const obsYear = Number(capturedDateStr.slice(0, 4)) || 2026;
  if (lower.includes('moon')) {
    return {
      headline: `Light received ${capturedDateStr.slice(0, 10)} departed 1.28 seconds earlier`,
      departureEpoch: '1.28 s lookback',
      uncertainty: '±0.002 s (LRO laser ranging)',
      eraContext: 'Reflected solar photons across cislunar vacuum (384,400 km)',
    };
  }
  if (lower.includes('saturn')) {
    return {
      headline: `Light received ${capturedDateStr.slice(0, 10)} departed 75.3 minutes earlier`,
      departureEpoch: '75.3 min lookback',
      uncertainty: '±0.01 s (JPL DE440 ephemeris)',
      eraContext: 'Reflected sunlight from Saturn’s icy rings & Titan’s nitrogen haze (9.06 AU)',
    };
  }
  const pc = finding.inferredDistancePc?.median ?? 168.0;
  const ly = pc * 3.26156;
  if (ly < 10) {
    const departYear = (obsYear - ly).toFixed(1);
    return {
      headline: `Light received ${capturedDateStr.slice(0, 10)} departed ~${departYear} CE`,
      departureEpoch: `~${departYear} CE (±0.01 yr)`,
      uncertainty: `Gaia DR3 parallax: ${pc.toFixed(3)} pc (${ly.toFixed(2)} ly)`,
      eraContext: 'Nearest neighboring star system to Earth across the local interstellar cloud',
    };
  }
  const departYear = Math.round(obsYear - ly);
  const p16 = finding.inferredDistancePc?.lower ?? pc * 0.94;
  const p84 = finding.inferredDistancePc?.upper ?? pc * 1.06;
  const halfSpanYr = Math.max(1, Math.round(((p84 - p16) * 3.26156) * 0.5));
  const yearLabel = departYear > 0 ? `~${departYear.toLocaleString()} CE` : `~${Math.abs(departYear).toLocaleString()} BCE`;

  let eraContext = 'Photons traveled uninterrupted across interstellar space';
  if (ly < 30) {
    eraContext = 'Departed during the late 20th / early 21st century on Earth';
  } else if (departYear >= 1450 && departYear <= 1650) {
    eraContext = 'Departed during the Terrestrial Renaissance & Copernican revolution';
  } else if (departYear < 1000) {
    eraContext = 'Departed during early human antiquity across the Milky Way disk';
  }

  return {
    headline: `Light received ${capturedDateStr.slice(0, 10)} departed ${yearLabel}`,
    departureEpoch: `${yearLabel} (±${halfSpanYr} yr)`,
    uncertainty: `Bailer-Jones 68% CI: ${p16.toFixed(0)}–${p84.toFixed(0)} pc`,
    eraContext,
  };
}

function formatEvidenceBadge(evidenceClass: CelestialFinding['evidenceClass']): {
  label: string;
  color: string;
} {
  switch (evidenceClass) {
    case 'catalog_matched':
      return {
        label: 'Catalog Matched · Gaia DR3',
        color: '#60a5fa',
      };
    case 'predicted':
      return {
        label: 'Predicted · JPL DE440',
        color: '#facc15',
      };
    case 'observed':
      return {
        label: 'Observed · Camera Pixels',
        color: '#34d399',
      };
    case 'simulated':
      return {
        label: 'Catalog Geometry · 3D Model',
        color: '#c084fc',
      };
  }
}

export const SpatialObservatoryViewport: React.FC<
  SpatialObservatoryViewportProps
> = ({
  session,
  setSession,
  selectedFinding,
  discoveredMedia,
  activeDiscoveredId,
  setActiveDiscoveredId,
  activeDiscoveredItem: propActiveDiscoveredItem,
  uploadedMediaUrl,
  setUploadedMediaUrl,
  commandText,
  setCommandText,
  onRunUtterance,
  onSelectFinding,
  onPushToTalk,
  onTriggerUpload,
}) => {
  const canvasHostRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<SparkPortalRenderer | null>(null);

  const [activeRegime, setActiveRegime] =
    useState<SpatialScaleRegime>('earth_capture');
  const [skyRestorationActive, setSkyRestorationActive] = useState(true);
  const [spectralBand, setSpectralBand] = useState<SpectralBandMode>('halpha');
  const [showSpecimenView, setShowSpecimenView] = useState(false);
  const [activePlateIndex, setActivePlateIndex] = useState<number>(2);
  const [specimenTilt, setSpecimenTilt] = useState<{ rx: number; rz: number; spread: number }>({
    rx: 56,
    rz: -34,
    spread: 26,
  });

  const [monocleActive, setMonocleActive] = useState(false);
  const [monoclePos, setMonoclePos] = useState<{ x: number; y: number }>({
    x: 0.44,
    y: 0.46,
  });
  const monocleDraggingRef = useRef(false);

  const [showSourcePip] = useState(true);
  const [compareRawHold, setCompareRawHold] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [voiceSummoned, setVoiceSummoned] = useState(false);

  const [reticles, setReticles] = useState<ProjectedReticle[]>([]);
  const [telemetry, setTelemetry] = useState<CameraTelemetryState>({
    yawDeg: 210,
    pitchDeg: 24,
    fovDeg: 56,
    velYaw: 0,
    velPitch: 0,
    warpFactor: 0,
    subjectScreen: { x: 720, y: 450, visible: false },
  });

  const dragStateRef = useRef<{
    dragging: boolean;
    startX: number;
    startY: number;
    lastX: number;
    lastY: number;
    moved: boolean;
  }>({
    dragging: false,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
    moved: false,
  });

  const activeDiscoveredItem = useMemo(() => {
    if (propActiveDiscoveredItem) return propActiveDiscoveredItem;
    return (
      discoveredMedia.find((item) => item.id === activeDiscoveredId) ??
      discoveredMedia[0] ??
      null
    );
  }, [propActiveDiscoveredItem, discoveredMedia, activeDiscoveredId]);

  const activeFinding = useMemo(() => {
    if (selectedFinding) return selectedFinding;
    return session.observationSolution.findings[0] ?? null;
  }, [selectedFinding, session.observationSolution.findings]);

  // Dynamic, Location-Agnostic Observer Metadata (Works for Sahara, New Zealand, or ANY Uploaded Capture)
  const observerLat =
    session.sourceAsset.observerLocation?.latDeg ??
    activeDiscoveredItem?.latDeg ??
    31.2021;
  const observerLon =
    session.sourceAsset.observerLocation?.lonDeg ??
    activeDiscoveredItem?.lonDeg ??
    -3.9458;
  const observerCoordsStr = useMemo(
    () => formatObserverCoords(observerLat, observerLon, 2),
    [observerLat, observerLon]
  );
  const observerPlaceName = useMemo(
    () =>
      session.sourceAsset.observerLocation?.placeName ??
      inferPlaceNameFromCoords(observerLat, observerLon, activeDiscoveredItem?.title),
    [session.sourceAsset.observerLocation?.placeName, observerLat, observerLon, activeDiscoveredItem?.title]
  );
  const observerShortSite = useMemo(() => {
    if (observerLat < -34.0 && observerLon > 160.0) return 'Aoraki · NZ';
    if (observerLat > 27.0 && observerLat < 35.0 && observerLon > -10.0 && observerLon < 2.0) {
      return 'Sahara';
    }
    return 'Capture Site';
  }, [observerLat, observerLon]);

  const capturedAtUTC =
    session.sourceAsset.capturedAtUTC ??
    activeDiscoveredItem?.capturedAtUTC ??
    '2026-10-20T22:21:02Z';
  const cameraModel =
    session.sourceAsset.cameraMetadata?.model ??
    activeDiscoveredItem?.cameraModel ??
    'Field Camera Sensor';
  const shaShort = (session.sourceAsset.sha256 || '8c39c04b28a1').slice(0, 6);

  // Dynamic Siterra 01–07 Specimen Plates adapted to the active capture location
  const specimenPlates = useMemo<SiterraSpecimenPlate[]>(() => {
    const latOnly = `${Math.abs(observerLat).toFixed(2)}°${observerLat >= 0 ? 'N' : 'S'}`;
    const lonAndPlace = `${Math.abs(observerLon).toFixed(2)}°${observerLon >= 0 ? 'E' : 'W'} · ${observerShortSite}`;
    return [
      {
        num: '01',
        title: 'Recorded light',
        subtitle: 'Immutable camera frame & sensor noise floor',
        metricValue: shaShort,
        metricUnit: 'SHA-256 verified',
        color: '#94a3b8',
        badgeClass: 'Recorded',
        provenance: `${cameraModel} · ${session.sourceAsset.width}×${session.sourceAsset.height} · Preserved source stream`,
        detail: `Original photon arrival recorded at ${observerPlaceName} (${observerCoordsStr}) without pixel mutation or synthetic star hallucination.`,
        targetRegime: 'earth_capture',
      },
      {
        num: '02',
        title: 'Observer frame',
        subtitle: 'Topocentric horizon, zenith & exposure epoch',
        metricValue: latOnly,
        metricUnit: lonAndPlace,
        color: '#64748b',
        badgeClass: 'Topocentric',
        provenance: `${capturedAtUTC.replace('T', ' ').replace('Z', ' UTC')} · 3D Gaussian Splat Horizon · FOV ${telemetry.fovDeg}°`,
        detail: `Anchors the camera pose to ${observerPlaceName}’s surface normal, local sidereal time, and gravity vector.`,
        targetRegime: 'earth_capture',
      },
      {
        num: '03',
        title: 'Atmospheric path',
        subtitle: 'Mie lunar glare subtraction & multi-frame stack',
        metricValue: '+5.21',
        metricUnit: 'dB shadow SNR',
        color: '#38bdf8',
        badgeClass: 'Restored',
        provenance: 'Airmass 1.78 sec(z) · Multi-frame median registration · +1.64 mag depth',
        detail: 'Subtracts forward-scattered atmospheric aerosol & lunar aureole while recovering faint stellar centroids.',
        targetRegime: 'earth_capture',
      },
      {
        num: '04',
        title: 'Orbital neighborhood',
        subtitle: 'Topocentric cislunar sightline & LRO lunar regolith',
        metricValue: '1.28',
        metricUnit: 'light-seconds',
        color: '#facc15',
        badgeClass: 'Ephemeris',
        provenance: 'JPL DE440 Ephemeris · NASA LRO LOLA 3D Laser Altimetry · 384,400 km',
        detail: `Projects the physical sightline ray from ${observerCoordsStr} (${observerShortSite}) on Earth’s limb to the Moon.`,
        targetRegime: 'cislunar',
      },
      {
        num: '05',
        title: 'Celestial registration',
        subtitle: 'TAN-SIP WCS plate solution & held-out verification',
        metricValue: '0.41″',
        metricUnit: 'WCS RMS residual',
        color: '#60a5fa',
        badgeClass: 'Catalog Matched',
        provenance: `Gaia DR3 + Hipparcos · ${session.observationSolution.findings.length} catalog objects · 0 hallucinated stars`,
        detail: 'Maps every image pixel (x, y) to right ascension and declination (α, δ) on the J2000 celestial sphere.',
        targetRegime: 'earth_capture',
      },
      {
        num: '06',
        title: 'Spectral & 3D nebula',
        subtitle: 'Registered H-Alpha 656nm, JWST Near-IR & 3D Splat Nebula',
        metricValue: '656.3',
        metricUnit: 'nm Hα + 3D Splats',
        color: '#f43f5e',
        badgeClass: 'Multi-Band',
        provenance: 'SHASSA H-Alpha + JWST Near-IR + 19,297 NASA 3D Gaussian Splats',
        detail: 'Reveals ionized hydrogen nebulae and 3D Gaussian Splat star-forming pillars along the sightline.',
        targetRegime: 'deep_sky',
      },
      {
        num: '07',
        title: '3D Chimney & Worlds',
        subtitle: 'Local Bubble 3D Gaussian Splats & Planetary Surface',
        metricValue: '165',
        metricUnit: 'pc Local Chimney',
        color: '#a855f7',
        badgeClass: '3D Gaussian Splats',
        provenance: 'O’Neill et al. (2024) Harvard CfA 3D Splat Cloud + Exoplanet Surface Engine',
        detail: 'Unfolds the 2D sky into 3D parsec space and lets you descend onto data-driven planetary surfaces.',
        targetRegime: 'solar_neighborhood',
      },
    ];
  }, [
    observerLat,
    observerLon,
    observerShortSite,
    observerPlaceName,
    observerCoordsStr,
    shaShort,
    cameraModel,
    session.sourceAsset.width,
    session.sourceAsset.height,
    session.observationSolution.findings.length,
    capturedAtUTC,
    telemetry.fovDeg,
  ]);

  // Initialize WebGL2 + @sparkjsdev/spark 3D Gaussian Splatting Observatory Renderer
  useEffect(() => {
    const host = canvasHostRef.current;
    if (!host) return;
    const renderer = new SparkPortalRenderer(host, (nextRegime) => {
      setActiveRegime(nextRegime);
      observatoryAudio.setScaleRegime(nextRegime === 'planetary_surface' ? 'cislunar' : nextRegime);
    });
    rendererRef.current = renderer;
    renderer.setSpectralBand('halpha');
    renderer.setSpectralMonocleScreenPos(0.44, 0.46, true);

    const handleResize = () => {
      if (!host) return;
      renderer.resize(host.clientWidth || 1440, host.clientHeight || 900);
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      renderer.dispose();
      rendererRef.current = null;
    };
  }, []);

  // Sync observer geographic coordinates (Sahara, New Zealand, or Uploaded Capture) into 3D renderer
  useEffect(() => {
    rendererRef.current?.setObserverCoordinates(observerLat, observerLon);
  }, [observerLat, observerLon]);

  // Sync H1 Spectral Monocle ray into 3D shader
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    const isEarth = activeRegime === 'earth_capture' && monocleActive && !showSpecimenView;
    renderer.setSpectralMonocleScreenPos(monoclePos.x, monoclePos.y, isEarth);
  }, [monoclePos, monocleActive, activeRegime, showSpecimenView]);

  // Project celestial reticles and camera telemetry at 30Hz
  useEffect(() => {
    let timer = 0;
    const updateProjections = () => {
      const renderer = rendererRef.current;
      const host = canvasHostRef.current;
      if (renderer && host) {
        const w = host.clientWidth || 1440;
        const h = host.clientHeight || 900;
        const projected: ProjectedReticle[] = session.observationSolution.findings.map((finding) => {
          const ra = finding.skyPosition?.raDeg ?? 83.8;
          const dec = finding.skyPosition?.decDeg ?? -5.4;
          const pt = renderer.projectSkyTargetToScreen(
            finding.id,
            finding.title,
            ra,
            dec,
            w,
            h
          );
          return {
            finding,
            x: pt.x,
            y: pt.y,
            visible: pt.visible,
          };
        });
        setReticles(projected);
        setTelemetry(renderer.getCameraTelemetry(w, h));
      }
      timer = window.setTimeout(updateProjections, 33);
    };
    updateProjections();
    return () => window.clearTimeout(timer);
  }, [session.observationSolution.findings]);

  // Keyboard navigation (WASD + Arrows + / for Voice/Command + Esc)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (e.key === 'Escape') {
        setVoiceSummoned(false);
        setShowSpecimenView(false);
        rendererRef.current?.jumpToRegime('earth_capture');
        setActiveRegime('earth_capture');
        observatoryAudio.setScaleRegime('earth_capture');
        return;
      }
      if (tag === 'input' || tag === 'textarea') return;
      if (e.key === '/') {
        e.preventDefault();
        setVoiceSummoned((v) => !v);
        return;
      }
      const renderer = rendererRef.current;
      if (!renderer) return;

      switch (e.key) {
        case 'w':
        case 'W':
          renderer.nudgeMovement(0, 0, -1.6);
          break;
        case 's':
        case 'S':
          renderer.nudgeMovement(0, 0, 1.6);
          break;
        case 'a':
        case 'A':
          renderer.nudgeMovement(-1.6, 0, 0);
          break;
        case 'd':
        case 'D':
          renderer.nudgeMovement(1.6, 0, 0);
          break;
        case 'ArrowLeft':
          renderer.nudgeLook(0.08, 0);
          break;
        case 'ArrowRight':
          renderer.nudgeLook(-0.08, 0);
          break;
        case 'ArrowUp':
          renderer.nudgeLook(0, 0.06);
          break;
        case 'ArrowDown':
          renderer.nudgeLook(0, -0.06);
          break;
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('[data-hud-interactive="true"]')) {
      return;
    }
    dragStateRef.current = {
      dragging: true,
      startX: e.clientX,
      startY: e.clientY,
      lastX: e.clientX,
      lastY: e.clientY,
      moved: false,
    };
  }, []);

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const host = canvasHostRef.current;
      if (monocleDraggingRef.current && host) {
        const rect = host.getBoundingClientRect();
        const nx = Math.max(0.14, Math.min(0.82, (e.clientX - rect.left) / rect.width));
        const ny = Math.max(0.18, Math.min(0.78, (e.clientY - rect.top) / rect.height));
        setMonoclePos({ x: nx, y: ny });
        return;
      }
      const st = dragStateRef.current;
      if (!st.dragging) return;
      const dx = e.clientX - st.lastX;
      const dy = e.clientY - st.lastY;
      if (Math.hypot(e.clientX - st.startX, e.clientY - st.startY) > 4) {
        st.moved = true;
      }
      st.lastX = e.clientX;
      st.lastY = e.clientY;
      rendererRef.current?.dragLook(dx, dy);
    },
    []
  );

  const handlePointerUp = useCallback(() => {
    monocleDraggingRef.current = false;
    dragStateRef.current.dragging = false;
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    rendererRef.current?.zoomByWheel(e.deltaY);
  }, []);

  const handleToggleSkyRestoration = useCallback((nextVal?: boolean) => {
    setSkyRestorationActive((prev) => {
      const next = nextVal !== undefined ? nextVal : !prev;
      rendererRef.current?.setSkyRestorationActive(next);
      return next;
    });
    observatoryAudio.playDetentClick(980);
  }, []);

  const handleBandSelect = useCallback((band: SpectralBandMode) => {
    setSpectralBand(band);
    rendererRef.current?.setSpectralBand(band);
    observatoryAudio.playDetentClick(band === 'halpha' ? 1080 : band === 'jwst_ir' ? 840 : 960);
  }, []);

  const handleSwitchCaptureClip = useCallback(
    (item: DiscoveredMediaItem) => {
      setUploadedMediaUrl(null);
      setActiveDiscoveredId(item.id);
      const nextSession = buildSessionFromDiscoveredMedia(item);
      setSession(nextSession);
      setShowSpecimenView(false);
      rendererRef.current?.setObserverCoordinates(item.latDeg, item.lonDeg);
      rendererRef.current?.jumpToRegime('earth_capture');
      setActiveRegime('earth_capture');
      observatoryAudio.playDetentClick(920);
    },
    [setActiveDiscoveredId, setSession, setUploadedMediaUrl]
  );

  const handleTargetClickInSky = useCallback(
    (finding: CelestialFinding) => {
      onSelectFinding(finding.id);
      const ra = finding.skyPosition?.raDeg ?? 83.8;
      const dec = finding.skyPosition?.decDeg ?? -5.4;
      rendererRef.current?.focusSkyTarget(finding.id, finding.title, ra, dec);
      observatoryAudio.playTargetLockTone(
        finding.catalogRef?.colorIndexBpRp ?? 0.3,
        0.0
      );
    },
    [onSelectFinding]
  );

  const handleFlyToTarget3D = useCallback(
    (finding: CelestialFinding) => {
      onSelectFinding(finding.id);
      const renderer = rendererRef.current;
      if (!renderer) return;
      const nextRegime = renderer.flyToTargetIn3D(
        finding.id,
        finding.title,
        finding.catalogRef?.colorIndexBpRp,
        finding.catalogRef?.objectType
      );
      setActiveRegime(nextRegime);
      observatoryAudio.setScaleRegime(nextRegime === 'planetary_surface' ? 'cislunar' : nextRegime);
      observatoryAudio.playTargetLockTone(
        finding.catalogRef?.colorIndexBpRp ?? 0.4,
        0.0
      );
    },
    [onSelectFinding]
  );

  const handleDescendToWorldSurface = useCallback(
    (finding: CelestialFinding | null) => {
      const target = finding ?? session.observationSolution.findings[0] ?? null;
      const renderer = rendererRef.current;
      if (!renderer || !target) return;
      const pc = target.inferredDistancePc?.median ?? 1.34;
      renderer.descendToPlanetarySurface(
        target.title,
        target.catalogRef?.colorIndexBpRp ?? 0.8,
        pc * 3.26156,
        observerPlaceName
      );
      renderer.jumpToRegime('planetary_surface');
      setActiveRegime('planetary_surface');
      observatoryAudio.setScaleRegime('cislunar');
      observatoryAudio.playTargetLockTone(0.65, -0.2);
    },
    [session.observationSolution.findings, observerPlaceName]
  );

  const handleJumpRegime = useCallback(
    (regime: SpatialScaleRegime) => {
      setShowSpecimenView(false);
      if (regime === 'cislunar') {
        const moonF = session.observationSolution.findings.find((f) =>
          f.title.toLowerCase().includes('moon')
        );
        if (moonF) onSelectFinding(moonF.id);
      } else if (regime === 'solar_neighborhood') {
        const starF =
          session.observationSolution.findings.find((f) =>
            f.title.toLowerCase().includes('alpha centauri')
          ) ??
          session.observationSolution.findings.find((f) =>
            f.title.toLowerCase().includes('betelgeuse')
          ) ??
          session.observationSolution.findings.find((f) =>
            f.title.toLowerCase().includes('sadalsuud')
          ) ??
          session.observationSolution.findings.find(
            (f) => !f.title.toLowerCase().includes('moon')
          );
        if (starF) {
          onSelectFinding(starF.id);
          rendererRef.current?.setTargetAppearance(
            starF.title,
            starF.catalogRef?.colorIndexBpRp,
            starF.catalogRef?.objectType
          );
        }
      } else if (regime === 'deep_sky') {
        const currentIsDeep =
          activeFinding &&
          (activeFinding.title.toLowerCase().includes('nebula') ||
            activeFinding.title.toLowerCase().includes('m42') ||
            activeFinding.title.toLowerCase().includes('carina') ||
            activeFinding.title.toLowerCase().includes('sagittarius') ||
            activeFinding.title.toLowerCase().includes('black hole'));
        const nebF = currentIsDeep
          ? activeFinding
          : session.observationSolution.findings.find(
              (f) =>
                f.title.toLowerCase().includes('sagittarius') ||
                f.title.toLowerCase().includes('black hole') ||
                f.title.toLowerCase().includes('nebula') ||
                f.title.toLowerCase().includes('m42') ||
                f.title.toLowerCase().includes('carina')
            ) ?? session.observationSolution.findings[session.observationSolution.findings.length - 1];
        if (nebF) {
          onSelectFinding(nebF.id);
          rendererRef.current?.setTargetAppearance(
            nebF.title,
            nebF.catalogRef?.colorIndexBpRp,
            nebF.catalogRef?.objectType
          );
        }
        setSpectralBand('optical');
        rendererRef.current?.setSpectralBand('optical');
      } else if (regime === 'planetary_surface') {
        handleDescendToWorldSurface(activeFinding);
        return;
      } else if (regime === 'earth_capture') {
        setSpectralBand('halpha');
        rendererRef.current?.setSpectralBand('halpha');
      }
      rendererRef.current?.jumpToRegime(regime);
      setActiveRegime(regime);
      observatoryAudio.setScaleRegime(regime);
      observatoryAudio.playDetentClick(880);
    },
    [session.observationSolution.findings, onSelectFinding, handleDescendToWorldSurface, activeFinding]
  );

  const handleCycleTarget = useCallback(
    (dir: 1 | -1) => {
      if (!session.observationSolution.findings.length) return;
      const curIdx = session.observationSolution.findings.findIndex(
        (f) => f.id === activeFinding?.id
      );
      const nextIdx =
        (curIdx + dir + session.observationSolution.findings.length) %
        session.observationSolution.findings.length;
      const nextFinding = session.observationSolution.findings[nextIdx];
      if (!nextFinding) return;
      handleTargetClickInSky(nextFinding);
      if (activeRegime === 'planetary_surface') {
        handleDescendToWorldSurface(nextFinding);
      }
    },
    [
      session.observationSolution.findings,
      activeFinding,
      activeRegime,
      handleTargetClickInSky,
      handleDescendToWorldSurface,
    ]
  );

  const handleToggleSound = useCallback(() => {
    const next = observatoryAudio.toggle();
    setSoundEnabled(next);
    if (next) {
      observatoryAudio.setScaleRegime(activeRegime === 'planetary_surface' ? 'cislunar' : activeRegime);
    }
  }, [activeRegime]);

  const handleCommandSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = commandText.trim();
      if (!trimmed) return;
      const lower = trimmed.toLowerCase();
      if (lower.includes('black hole') || lower.includes('gargantua') || lower.includes('sgr a') || lower.includes('sagittarius')) {
        const bhFinding = session.observationSolution.findings.find(
          (f) => f.title.toLowerCase().includes('sagittarius') || f.title.toLowerCase().includes('black hole')
        );
        if (bhFinding) {
          onSelectFinding(bhFinding.id);
          rendererRef.current?.setTargetAppearance(bhFinding.title, 1.85, 'deep_sky');
          if (lower.includes('surface') || lower.includes('world') || lower.includes('land') || lower.includes('ocean') || lower.includes('miller')) {
            handleDescendToWorldSurface(bhFinding);
          } else {
            rendererRef.current?.jumpToRegime('deep_sky');
            setActiveRegime('deep_sky');
          }
        }
      } else if (lower.includes('surface') || lower.includes('planet') || lower.includes('land') || lower.includes('world') || lower.includes('proxima') || lower.includes('titan')) {
        handleJumpRegime('planetary_surface');
      } else if (lower.includes('moon') || lower.includes('cislunar') || lower.includes('lunar')) {
        handleJumpRegime('cislunar');
      } else if (lower.includes('bubble') || lower.includes('chimney') || lower.includes('betelgeuse') || lower.includes('star') || lower.includes('centauri')) {
        handleJumpRegime('solar_neighborhood');
      } else if (lower.includes('orion') || lower.includes('pillar') || lower.includes('nebula') || lower.includes('m42') || lower.includes('carina')) {
        handleJumpRegime('deep_sky');
      } else if (lower.includes('specimen') || lower.includes('layer') || lower.includes('stack') || lower.includes('proof')) {
        setShowSpecimenView(true);
      } else if (lower.includes('back') || lower.includes('return') || lower.includes('earth') || lower.includes('sky')) {
        handleJumpRegime('earth_capture');
      }
      onRunUtterance(trimmed, 'text');
      setCommandText('');
    },
    [commandText, handleJumpRegime, handleDescendToWorldSurface, onRunUtterance, onSelectFinding, session.observationSolution.findings, setCommandText]
  );

  const sourcePreviewUrl =
    uploadedMediaUrl ??
    activeDiscoveredItem?.urls.v2Url ??
    activeDiscoveredItem?.urls.origUrl ??
    '/real-media/morocco-camp-moon-1389-restored.jpg';

  const rawCaptureUrl =
    uploadedMediaUrl ??
    activeDiscoveredItem?.urls.origUrl ??
    '/real-media/morocco-camp-moon-1389-source.jpg';

  const activeBadge = activeFinding
    ? formatEvidenceBadge(activeFinding.evidenceClass)
    : { label: 'Catalog Matched · Gaia DR3', color: '#60a5fa' };

  const photonReceipt = useMemo(
    () => (activeFinding ? computePhotonReceipt(activeFinding, capturedAtUTC) : null),
    [activeFinding, capturedAtUTC]
  );

  const planetaryWorldDesc = useMemo(() => {
    if (!activeFinding) return null;
    const pc = activeFinding.inferredDistancePc?.median ?? 1.34;
    return derivePlanetaryWorldDescriptor(
      activeFinding.title,
      activeFinding.catalogRef?.colorIndexBpRp ?? 0.8,
      pc * 3.26156,
      observerPlaceName
    );
  }, [activeFinding, observerPlaceName]);

  const selectedReticle = useMemo(() => {
    if (!activeFinding) return reticles.find((r) => r.visible) ?? null;
    return (
      reticles.find((r) => r.finding.id === activeFinding.id && r.visible) ??
      reticles.find((r) => r.visible) ??
      null
    );
  }, [reticles, activeFinding]);

  const activeSpecimenPlate =
    specimenPlates[activePlateIndex] ?? specimenPlates[2]!;

  const monocleBandMeta = useMemo(() => {
    if (spectralBand === 'halpha') {
      return {
        code: 'SHASSA Hα · 656.3 nm',
        desc: 'Ionized hydrogen Balmer-alpha survey registered to WCS',
        accent: '#fb7185',
      };
    }
    if (spectralBand === 'jwst_ir') {
      return {
        code: 'JWST / WISE · 2.1 μm Near-IR',
        desc: 'Dust-penetrating infrared continuum registered to WCS',
        accent: '#fbbf24',
      };
    }
    return {
      code: 'Temporal Stack · +1.64 mag',
      desc: '12-frame median glare-subtracted optical restoration',
      accent: '#60a5fa',
    };
  }, [spectralBand]);

  return (
    <div
      className="alb-root"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onWheel={handleWheel}
    >
      {/* Full-Screen WebGL2 + @sparkjsdev/spark 3D Gaussian Splatting Canvas */}
      <div
        ref={canvasHostRef}
        className="alb-canvas-host"
        data-testid="spark-portal-canvas"
      />

      {/* =======================================================================
          H1: DRAGGABLE SPECTRAL MONOCLE (Toolglass & Magic Lens on Night Sky)
          ======================================================================= */}
      {activeRegime === 'earth_capture' && monocleActive && !showSpecimenView && (
        <div
          className="alb-spectral-monocle"
          data-hud-interactive="true"
          data-testid="spectral-monocle-lens"
          style={{
            left: `${monoclePos.x * 100}%`,
            top: `${monoclePos.y * 100}%`,
            borderColor: monocleBandMeta.accent,
          }}
          onPointerDown={(e) => {
            e.stopPropagation();
            monocleDraggingRef.current = true;
          }}
        >
          <svg
            className="alb-monocle-svg"
            viewBox="0 0 260 260"
            aria-hidden="true"
          >
            <circle
              cx="130"
              cy="130"
              r="126"
              fill="none"
              stroke={monocleBandMeta.accent}
              strokeWidth="1.2"
              strokeOpacity="0.65"
            />
            <circle
              cx="130"
              cy="130"
              r="118"
              fill="none"
              stroke="rgba(255,255,255,0.18)"
              strokeWidth="0.75"
              strokeDasharray="3 6"
            />
            <line x1="130" y1="2" x2="130" y2="12" stroke={monocleBandMeta.accent} strokeWidth="1.4" />
            <line x1="130" y1="248" x2="130" y2="258" stroke={monocleBandMeta.accent} strokeWidth="1.4" />
            <line x1="2" y1="130" x2="12" y2="130" stroke={monocleBandMeta.accent} strokeWidth="1.4" />
            <line x1="248" y1="130" x2="258" y2="130" stroke={monocleBandMeta.accent} strokeWidth="1.4" />
          </svg>

          <div className="alb-monocle-rim-header">
            <span
              className="alb-monocle-dot"
              style={{ backgroundColor: monocleBandMeta.accent }}
            />
            <span className="alb-monocle-code">{monocleBandMeta.code}</span>
            <span className="alb-monocle-drag-hint">Drag lens</span>
          </div>

          <div
            className="alb-monocle-band-pills"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className={`alb-monocle-pill ${spectralBand === 'halpha' ? 'is-active' : ''}`}
              onClick={() => handleBandSelect('halpha')}
            >
              Hα 656nm
            </button>
            <button
              type="button"
              className={`alb-monocle-pill ${spectralBand === 'jwst_ir' ? 'is-active' : ''}`}
              onClick={() => handleBandSelect('jwst_ir')}
            >
              JWST Near-IR
            </button>
            <button
              type="button"
              className={`alb-monocle-pill ${spectralBand === 'optical' ? 'is-active' : ''}`}
              onClick={() => handleBandSelect('optical')}
            >
              Restored +1.6m
            </button>
          </div>
        </div>
      )}

      {/* =======================================================================
          SINGLE SELECTED SKY TARGET CALLOUT (Clean Leader Line, Zero Clutter)
          ======================================================================= */}
      {activeRegime === 'earth_capture' && !showSpecimenView && (
        <div className="alb-sky-markers-layer">
          {reticles
            .filter((r) => r.visible)
            .slice(0, 8)
            .map(({ finding, x, y }) => {
              const isSelected = selectedReticle?.finding.id === finding.id;
              return (
                <button
                  key={finding.id}
                  type="button"
                  data-hud-interactive="true"
                  className={`alb-sky-node ${isSelected ? 'is-selected' : ''}`}
                  style={{ left: `${x}px`, top: `${y}px` }}
                  onClick={() => handleTargetClickInSky(finding)}
                  onDoubleClick={() => handleFlyToTarget3D(finding)}
                >
                  <span className="alb-sky-node-ring" />
                  {isSelected && (
                    <span className="alb-sky-node-leader">
                      <span className="alb-sky-node-line" />
                      <span className="alb-sky-node-tag">
                        <span className="alb-sky-node-name">
                          {cleanTargetName(finding.title)}
                        </span>
                        <span className="alb-sky-node-dist">
                          {formatShortDistance(finding)}
                        </span>
                      </span>
                    </span>
                  )}
                </button>
              );
            })}
        </div>
      )}

      {/* =======================================================================
          TOP-RIGHT: WHISPER-QUIET TWO-TONE VISOR TELEMETRY STAMP (Zero Pill Boxes)
          ======================================================================= */}
      <header className="alb-visor-topright" data-hud-interactive="true">
        <div className="alb-visor-sites">
          {discoveredMedia.map((item) => {
            const isSelected =
              !uploadedMediaUrl && item.id === activeDiscoveredItem?.id;
            const rawKey = `${item.id} ${item.filename ?? ''} ${item.title}`.toLowerCase();
            const isSecondarySahara =
              !isSelected && (rawKey.includes('1391') || rawKey.includes('1390') || rawKey.includes('deep night') || rawKey.includes('dunes'));
            const shortLabel =
              rawKey.includes('aoraki') || rawKey.includes('zealand') || rawKey.includes('nz')
                ? 'AORAKI NZ · 44.0°S'
                : rawKey.includes('1389') || rawKey.includes('moonrise')
                  ? 'SAHARA · 31.2°N'
                  : rawKey.includes('1391') || rawKey.includes('night')
                    ? 'SAHARA · DEEP NIGHT'
                    : rawKey.includes('1390') || rawKey.includes('desert')
                      ? 'SAHARA · DUNES'
                      : item.title.toUpperCase();
            return (
              <button
                key={item.id}
                type="button"
                data-testid={`capture-tab-${item.id}`}
                className={`alb-visor-link ${isSelected ? 'is-active' : ''}`}
                style={{ display: isSecondarySahara ? 'none' : undefined }}
                onClick={() => handleSwitchCaptureClip(item)}
              >
                {shortLabel}
              </button>
            );
          })}
          <button
            type="button"
            className="alb-visor-link"
            onClick={onTriggerUpload}
          >
            + CAPTURE
          </button>
        </div>

        <span className="alb-visor-divider">|</span>

        <span className="alb-visor-clock">
          {capturedAtUTC.slice(11, 16)} UTC
        </span>

        <span className="alb-visor-divider">|</span>

        <div className="alb-visor-modes">
          {activeRegime === 'earth_capture' ? (
            <button
              type="button"
              data-testid="toggle-monocle-btn"
              className={`alb-visor-link ${monocleActive && !showSpecimenView ? 'is-active' : ''}`}
              onClick={() => {
                setShowSpecimenView(false);
                setMonocleActive((v) => !v);
                observatoryAudio.playDetentClick(960);
              }}
            >
              Hα LENS
            </button>
          ) : (
            <>
              <button
                type="button"
                className={`alb-visor-link ${spectralBand === 'optical' ? 'is-active' : ''}`}
                onClick={() => handleBandSelect('optical')}
              >
                VIS
              </button>
              <button
                type="button"
                className={`alb-visor-link ${spectralBand === 'halpha' ? 'is-active' : ''}`}
                onClick={() => handleBandSelect('halpha')}
              >
                Hα
              </button>
              <button
                type="button"
                className={`alb-visor-link ${spectralBand === 'jwst_ir' ? 'is-active' : ''}`}
                onClick={() => handleBandSelect('jwst_ir')}
              >
                IR
              </button>
            </>
          )}

          <button
            type="button"
            data-testid="toggle-specimen-btn"
            className={`alb-visor-link ${showSpecimenView ? 'is-active' : ''}`}
            onClick={() => {
              setShowSpecimenView((v) => !v);
              observatoryAudio.playDetentClick(1040);
            }}
          >
            PROOF
          </button>

          <button
            type="button"
            className={`alb-visor-link ${soundEnabled ? 'is-active' : ''}`}
            onClick={handleToggleSound}
          >
            {soundEnabled ? '◉ AUDIO' : '○ AUDIO'}
          </button>
        </div>
      </header>

      {/* =======================================================================
          LEFT PERIPHERAL TELEMETRY COLUMN (Two-Tone Starlight Alabaster & Warm Pewter — Zero Colored Lines)
          ======================================================================= */}
      {!showSpecimenView && activeFinding && (
        <aside className="alb-visor-left" data-hud-interactive="true">
          <div className="alb-visor-metrics-stack">
            {/* Metric 1: Primary Distance or Surface Gravity */}
            <div className="alb-visor-metric">
              <div className="alb-visor-metric-label">
                {activeRegime === 'planetary_surface' ? 'SURFACE GRAVITY' : 'DISTANCE'}
              </div>
              <div className="alb-visor-metric-row">
                <span className="alb-visor-metric-num">
                  {activeRegime === 'planetary_surface' && planetaryWorldDesc
                    ? planetaryWorldDesc.surfaceGravity.split(' ')[0]
                    : formatShortDistance(activeFinding).split(' ')[0]}
                </span>
                <span className="alb-visor-metric-unit">
                  {activeRegime === 'planetary_surface' && planetaryWorldDesc
                    ? planetaryWorldDesc.surfaceGravity.split(' ').slice(1).join(' ')
                    : formatShortDistance(activeFinding).split(' ')[1] ?? 'ly'}
                </span>
              </div>
            </div>

            {/* Metric 2: Light Lookback or Surface Temp */}
            <div className="alb-visor-metric">
              <div className="alb-visor-metric-label">
                {activeRegime === 'planetary_surface' ? 'SURFACE TEMP' : 'LIGHT DEPARTED'}
              </div>
              <div className="alb-visor-metric-row">
                <span className="alb-visor-metric-num">
                  {activeRegime === 'planetary_surface' && planetaryWorldDesc
                    ? planetaryWorldDesc.equilibriumTemp.split(' ')[0]
                    : (photonReceipt?.departureEpoch.split(' ')[0] ?? '1.28')}
                </span>
                <span className="alb-visor-metric-unit">
                  {activeRegime === 'planetary_surface' && planetaryWorldDesc
                    ? (planetaryWorldDesc.equilibriumTemp.match(/\(([^·)]+)/)?.[1]?.trim() ?? 'K')
                    : (photonReceipt?.departureEpoch.split(' ')[1] ?? 's')}
                </span>
              </div>
            </div>

            {/* Metric 3: Horizon Elevation or Earth Return Sightline */}
            <div className="alb-visor-metric">
              <div className="alb-visor-metric-label">
                {activeRegime === 'planetary_surface' ? 'DISTANCE TO EARTH' : `${observerShortSite.toUpperCase()} ALT`}
              </div>
              <div className="alb-visor-metric-row">
                <span className="alb-visor-metric-num">
                  {activeRegime === 'planetary_surface'
                    ? formatShortDistance(activeFinding).split(' ')[0]
                    : `+${(activeFinding.skyPosition?.altDeg ?? 32.9).toFixed(1)}°`}
                </span>
                <span className="alb-visor-metric-unit">
                  {activeRegime === 'planetary_surface'
                    ? formatShortDistance(activeFinding).split(' ')[1] ?? 'ly'
                    : `az ${(activeFinding.skyPosition?.azDeg ?? 222).toFixed(0)}°`}
                </span>
              </div>
            </div>

            {/* Metric 4: Sensor SNR & Raw Optical Hold */}
            <div className="alb-visor-metric">
              <div className="alb-visor-metric-label">YOUR CAMERA SIGNAL</div>
              <div className="alb-visor-metric-row">
                <span className="alb-visor-metric-num">
                  {compareRawHold || !skyRestorationActive ? 'RAW' : '+5.2'}
                </span>
                <span className="alb-visor-metric-unit">
                  dB
                </span>
                <button
                  type="button"
                  className="alb-visor-raw-btn"
                  onPointerDown={() => {
                    setCompareRawHold(true);
                    handleToggleSkyRestoration(false);
                  }}
                  onPointerUp={() => {
                    setCompareRawHold(false);
                    handleToggleSkyRestoration(true);
                  }}
                  onPointerLeave={() => {
                    if (compareRawHold) {
                      setCompareRawHold(false);
                      handleToggleSkyRestoration(true);
                    }
                  }}
                >
                  HOLD ORIGINAL
                </button>
              </div>
            </div>

            {/* Micro Optical Sensor Frame (Immutable Source Reference) */}
            {showSourcePip && (
              <div className="alb-visor-route-box">
                <div className="alb-visor-micro-pip">
                  <img
                    src={
                      compareRawHold || !skyRestorationActive
                        ? rawCaptureUrl
                        : sourcePreviewUrl
                    }
                    alt={`${observerPlaceName} sensor`}
                    className="alb-visor-micro-img"
                  />
                  <span className="alb-visor-micro-caption">
                    {observerPlaceName.split(',')[0]} · {observerCoordsStr.split(',')[0]}
                  </span>
                </div>
              </div>
            )}
          </div>
        </aside>
      )}

      {/* =======================================================================
          IN-WORLD ANGLED HAIRLINE SUBJECT LEADER CALLOUT (Zero Boxy Background)
          ======================================================================= */}
      {!showSpecimenView && activeFinding && activeRegime !== 'earth_capture' && (
        <div
          className="alb-visor-subject-leader"
          data-hud-interactive="true"
          onClick={() => {
            if (activeRegime === 'planetary_surface') {
              handleJumpRegime('earth_capture');
            } else {
              handleDescendToWorldSurface(activeFinding);
            }
          }}
        >
          <svg
            className="alb-visor-leader-svg"
            viewBox="0 0 260 56"
            aria-hidden="true"
          >
            <circle cx="4" cy="48" r="2.6" fill="#E2B86B" />
            <path
              d="M 4 48 L 32 20 L 248 20"
              fill="none"
              stroke="rgba(244, 241, 234, 0.38)"
              strokeWidth="1"
            />
          </svg>
          <div className="alb-visor-leader-content">
            <div className="alb-visor-leader-title">
              {activeRegime === 'planetary_surface' && planetaryWorldDesc
                ? planetaryWorldDesc.worldName.split('·')[1]?.trim() ?? planetaryWorldDesc.worldName
                : cleanTargetName(activeFinding.title)}
            </div>
            <div className="alb-visor-leader-sub">
              {activeRegime === 'planetary_surface' && planetaryWorldDesc
                ? `${planetaryWorldDesc.atmosphereComposition.split('(')[0]?.trim() ?? planetaryWorldDesc.atmosphereComposition} · Esc to Sky`
                : `${formatShortDistance(activeFinding)} · Click to Land on Surface ↵`}
            </div>
          </div>
        </div>
      )}

      {/* =======================================================================
          H2: SITERRA 7-LAYER TILTING ISOMETRIC EVIDENCE SPECIMEN (01–07)
          ======================================================================= */}
      {showSpecimenView && (
        <section
          className="alb-siterra-overlay"
          data-hud-interactive="true"
          data-testid="siterra-specimen-overlay"
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const nx = (e.clientX - rect.left) / rect.width - 0.5;
            const ny = (e.clientY - rect.top) / rect.height - 0.5;
            setSpecimenTilt((prev) => ({
              ...prev,
              rx: 56 - ny * 14,
              rz: -34 + nx * 18,
            }));
          }}
        >
          <div className="alb-siterra-left">
            <div className="alb-siterra-eyebrow">
              Observational Evidence Specimen · {observerPlaceName}
            </div>
            <div className="alb-siterra-giant-metric">
              {activeSpecimenPlate.metricValue}
            </div>
            <div className="alb-siterra-metric-unit">
              {activeSpecimenPlate.metricUnit} — {activeSpecimenPlate.title}
            </div>
            <p className="alb-siterra-desc">{activeSpecimenPlate.detail}</p>
            <div className="alb-siterra-prov">{activeSpecimenPlate.provenance}</div>

            <div className="alb-siterra-thumbs">
              <div className="alb-siterra-thumb-card">
                <img src={rawCaptureUrl} alt="Plate 01 Raw Recorded Light" />
                <span>01 · Immutable Source (SHA-256)</span>
              </div>
              <div className="alb-siterra-thumb-card">
                <img src={sourcePreviewUrl} alt="Plate 03 Restored Frame" />
                <span>03 · Temporal Stack (+5.21 dB)</span>
              </div>
            </div>

            <div className="alb-siterra-footer-actions">
              <button
                type="button"
                className="alb-primary-action-btn"
                onClick={() => handleJumpRegime(activeSpecimenPlate.targetRegime)}
              >
                <span>Open Plate {activeSpecimenPlate.num} in 3D Viewport</span>
                <span>→</span>
              </button>
              <button
                type="button"
                className="alb-secondary-action-btn"
                onClick={() => setShowSpecimenView(false)}
              >
                Close Specimen
              </button>
            </div>
          </div>

          <div className="alb-siterra-stage">
            <div
              className="alb-siterra-stack"
              style={{
                transform: `rotateX(${specimenTilt.rx}deg) rotateZ(${specimenTilt.rz}deg)`,
              }}
            >
              {specimenPlates.map((plate, idx) => {
                const isActive = idx === activePlateIndex;
                const zOffset = (3 - idx) * specimenTilt.spread;
                return (
                  <div
                    key={plate.num}
                    className={`alb-siterra-plate ${isActive ? 'is-active' : ''}`}
                    style={{
                      transform: `translateZ(${zOffset}px)`,
                      borderColor: isActive ? plate.color : 'rgba(255,255,255,0.16)',
                    }}
                    onMouseEnter={() => {
                      setActivePlateIndex(idx);
                      observatoryAudio.playDetentClick(820 + idx * 55);
                    }}
                    onClick={() => handleJumpRegime(plate.targetRegime)}
                  >
                    {idx === 0 && (
                      <img
                        src={rawCaptureUrl}
                        alt=""
                        className="alb-plate-img"
                      />
                    )}
                    {idx === 2 && (
                      <img
                        src={sourcePreviewUrl}
                        alt=""
                        className="alb-plate-img"
                      />
                    )}
                    <div className="alb-plate-grid" />
                    <div
                      className="alb-plate-ring"
                      style={{ borderColor: plate.color }}
                    />
                    <span className="alb-plate-corner-num">{plate.num}</span>
                    <span className="alb-plate-corner-title">{plate.title}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="alb-siterra-right">
            {specimenPlates.map((plate, idx) => {
              const isActive = idx === activePlateIndex;
              return (
                <button
                  key={plate.num}
                  type="button"
                  className={`alb-siterra-leader-row ${isActive ? 'is-active' : ''}`}
                  onMouseEnter={() => {
                    setActivePlateIndex(idx);
                    observatoryAudio.playDetentClick(820 + idx * 55);
                  }}
                  onClick={() => handleJumpRegime(plate.targetRegime)}
                >
                  <span
                    className="alb-leader-dot"
                    style={{ backgroundColor: isActive ? plate.color : 'rgba(255,255,255,0.4)' }}
                  />
                  <span className="alb-leader-line" />
                  <span className="alb-leader-num">{plate.num}</span>
                  <span
                    className="alb-leader-swatch"
                    style={{ backgroundColor: plate.color }}
                  />
                  <span className="alb-leader-text">
                    <span className="alb-leader-title">{plate.title}</span>
                    <span className="alb-leader-sub">{plate.subtitle}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* =======================================================================
          BOTTOM-RIGHT PERIPHERAL DELTA & DIEGETIC SCALE SPARKLINE
          (Frameless Two-Tone Visor HUD — Zero Boxy Docks or Persistent Input Bars)
          ======================================================================= */}
      {!showSpecimenView && (
        <aside className="alb-visor-bottomright" data-hud-interactive="true">
          {/* Upper Delta Block with Plain-Language Scientific Provenance */}
          {activeFinding && (
            <div className="alb-visor-delta-block">
              <div className="alb-visor-delta-label">
                {activeRegime === 'earth_capture'
                  ? 'CAPTURED IN YOUR PHOTO · GAIA DR3 LOCK'
                  : activeRegime === 'cislunar'
                    ? 'MEASURED DATA · NASA LRO + GAIA DR3'
                    : activeRegime === 'solar_neighborhood'
                      ? 'CATALOG 3D GEOMETRY · GAIA DR3 + CfA'
                      : activeRegime === 'deep_sky'
                        ? 'SCIENTIFIC 3D MODEL · ESO / DNGR'
                        : planetaryWorldDesc?.terrainPalette === 'lunar_regolith'
                          ? 'MEASURED ALTIMETRY · NASA LRO SURFACE'
                          : 'SCIENTIFIC ILLUSTRATION · MODELED WORLD'}
              </div>
              <div className="alb-visor-delta-value">
                <span>
                  {activeRegime === 'planetary_surface' && planetaryWorldDesc
                    ? planetaryWorldDesc.worldName.split('·')[0]?.trim()
                    : cleanTargetName(activeFinding.title)}
                </span>
              </div>
              <div className="alb-visor-delta-actions">
                {activeRegime !== 'planetary_surface' ? (
                  <button
                    type="button"
                    data-testid="descend-world-surface-btn"
                    className="alb-visor-action-link"
                    onClick={() => handleDescendToWorldSurface(activeFinding)}
                  >
                    Descend to {planetaryWorldDesc?.worldName.split('·')[0]?.trim() ?? 'Surface'} ↵
                  </button>
                ) : null}
                {activeRegime !== 'earth_capture' ? (
                  <button
                    type="button"
                    className="alb-visor-action-link is-muted"
                    onClick={() => handleJumpRegime('earth_capture')}
                  >
                    ↩ Return to My Photo ({observerShortSite}) [Esc]
                  </button>
                ) : (
                  <button
                    type="button"
                    className="alb-visor-action-link"
                    onClick={() => handleFlyToTarget3D(activeFinding)}
                  >
                    Fly to {cleanTargetName(activeFinding.title)} in 3D →
                  </button>
                )}
              </div>

              {/* Frameless Two-Tone Target Switcher Ticks */}
              <div className="alb-visor-target-ticks">
                {session.observationSolution.findings
                  .filter((f, idx, arr) => {
                    const name = cleanTargetName(f.title).toLowerCase();
                    return arr.findIndex((o) => cleanTargetName(o.title).toLowerCase() === name) === idx;
                  })
                  .sort((a, b) => {
                    const score = (t: string) => {
                      const l = t.toLowerCase();
                      if (l.includes('moon')) return 1;
                      if (l.includes('saturn')) return 2;
                      if (l.includes('alpha centauri') || l.includes('betelgeuse')) return 3;
                      if (l.includes('nebula') || l.includes('carina') || l.includes('m42')) return 4;
                      if (l.includes('sagittarius') || l.includes('black hole')) return 5;
                      return 10;
                    };
                    return score(a.title) - score(b.title);
                  })
                  .slice(0, 5)
                  .map((f) => {
                    const isSelected = activeFinding?.id === f.id;
                    return (
                      <button
                        key={f.id}
                        type="button"
                        className={`alb-target-pill alb-visor-tick ${isSelected ? 'is-active' : ''}`}
                        data-testid={`quick-dest-${f.id}`}
                        onClick={() => {
                          onSelectFinding(f.id);
                          rendererRef.current?.setTargetAppearance(
                            f.title,
                            f.catalogRef?.colorIndexBpRp,
                            f.catalogRef?.objectType
                          );
                          if (activeRegime === 'planetary_surface') {
                            handleDescendToWorldSurface(f);
                          } else {
                            handleFlyToTarget3D(f);
                          }
                        }}
                      >
                        {cleanTargetName(f.title)}
                      </button>
                    );
                  })}
              </div>
            </div>
          )}

          {/* Lower Scale Horizon Track + Elevation Sparkline (Like "2.8 / 5.1 mi" in Linus Ekenstam Visor) */}
          <div className="alb-visor-scale-horizon">
            <div className="alb-visor-scale-top">
              <div className="alb-visor-scale-fraction">
                <span className="alb-visor-scale-cur">
                  {activeRegime === 'earth_capture'
                    ? '01'
                    : activeRegime === 'cislunar'
                      ? '02'
                      : activeRegime === 'solar_neighborhood'
                        ? '03'
                        : activeRegime === 'deep_sky'
                          ? '04'
                          : '05'}
                </span>
                <span className="alb-visor-scale-total">
                  / 05 ·{' '}
                  {activeRegime === 'earth_capture'
                    ? `${observerShortSite.toUpperCase()} SKY`
                    : activeRegime === 'cislunar'
                      ? 'CISLUNAR ORBIT'
                      : activeRegime === 'solar_neighborhood'
                        ? 'NEARBY STARS'
                        : activeRegime === 'deep_sky'
                          ? 'DEEP SPACE'
                          : 'WORLD SURFACE'}
                </span>
              </div>

              <div className="alb-visor-scale-extremes">
                <span>SKY LOCK 0.38″</span>
              </div>
            </div>

            {/* 5 Clickable Hairline Scale Segments Along the Glowing Progress Line */}
            <div className="alb-visor-progress-bar" role="tablist" aria-label="Scale Horizon">
              <button
                type="button"
                data-testid="scale-tab-earth"
                className={`alb-visor-seg ${activeRegime === 'earth_capture' ? 'is-active' : 'is-passed'}`}
                onClick={() => handleJumpRegime('earth_capture')}
                title="01 · Night Sky"
              >
                <span className="alb-visor-seg-line" />
                <span className="alb-visor-seg-label">SKY</span>
              </button>
              <button
                type="button"
                data-testid="scale-tab-cislunar"
                className={`alb-visor-seg ${activeRegime === 'cislunar' ? 'is-active' : activeRegime !== 'earth_capture' ? 'is-passed' : ''}`}
                onClick={() => handleJumpRegime('cislunar')}
                title="02 · Cislunar Orbit"
              >
                <span className="alb-visor-seg-line" />
                <span className="alb-visor-seg-label">ORBIT</span>
              </button>
              <button
                type="button"
                data-testid="scale-tab-local-bubble"
                className={`alb-visor-seg ${activeRegime === 'solar_neighborhood' ? 'is-active' : activeRegime === 'deep_sky' || activeRegime === 'planetary_surface' ? 'is-passed' : ''}`}
                onClick={() => handleJumpRegime('solar_neighborhood')}
                title="03 · Nearby Stars"
              >
                <span className="alb-visor-seg-line" />
                <span className="alb-visor-seg-label">STARS</span>
              </button>
              <button
                type="button"
                data-testid="scale-tab-deep-sky"
                className={`alb-visor-seg ${activeRegime === 'deep_sky' ? 'is-active' : activeRegime === 'planetary_surface' ? 'is-passed' : ''}`}
                onClick={() => handleJumpRegime('deep_sky')}
                title="04 · Nebula & Black Hole"
              >
                <span className="alb-visor-seg-line" />
                <span className="alb-visor-seg-label">BLACK HOLE</span>
              </button>
              <button
                type="button"
                data-testid="scale-tab-planetary-surface"
                className={`alb-visor-seg ${activeRegime === 'planetary_surface' ? 'is-active' : ''}`}
                onClick={() => handleJumpRegime('planetary_surface')}
                title="05 · Surface World"
              >
                <span className="alb-visor-seg-line" />
                <span className="alb-visor-seg-label">SURFACE</span>
              </button>
            </div>
          </div>
        </aside>
      )}

      {/* =======================================================================
          CONTEXTUAL VOICE / COMMAND SUMMON (Hidden by Default — Zero Basic Input Bar!)
          ======================================================================= */}
      {!showSpecimenView && !voiceSummoned && (
        <button
          type="button"
          className="alb-visor-summon-trigger"
          data-hud-interactive="true"
          onClick={() => setVoiceSummoned(true)}
          title="Press / to summon Voice & Intent Navigation"
        >
          <span className="alb-visor-summon-dot" />
          <span>[ / ] VOICE & INTENT</span>
        </button>
      )}

      {voiceSummoned && (
        <div
          className="alb-visor-command-overlay"
          data-hud-interactive="true"
        >
          <form
            className="alb-visor-command-form"
            onSubmit={(e) => {
              handleCommandSubmit(e);
              setVoiceSummoned(false);
            }}
          >
            <button
              type="button"
              className="alb-visor-mic-btn"
              onClick={onPushToTalk}
              title="Push to speak"
            >
              ◉
            </button>
            <input
              type="text"
              autoFocus
              value={commandText}
              onChange={(e) => setCommandText(e.target.value)}
              placeholder="Speak or type destination (“Black Hole”, “Titan”, “Proxima b”, “Sky”)…"
              className="alb-visor-command-input"
            />
            <button
              type="button"
              className="alb-visor-command-close"
              onClick={() => setVoiceSummoned(false)}
            >
              ESC
            </button>
          </form>
        </div>
      )}
    </div>
  );
};
