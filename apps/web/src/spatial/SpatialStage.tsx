import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  ALBEDIAN_COLORS,
  getMarkerVisualSpec,
} from '@albedian/design-system';
import {
  computeAnchorLockedPhaseAState,
  lookAtDirectionQuaternion,
  projectWorldPointWithCamera,
  propagateCatalogPositionToYear,
  raDecToUnitVector,
  worldToPixelWCS,
} from '@albedian/camera-rig';
import {
  ALL_SKY_CONSTELLATION_EDGES,
  GAIA_BACKGROUND_STARFIELD_3D,
} from './allSkyStarfield.js';

const ORION_CONSTELLATION_EDGES: Array<[string, string]> = [
  ['finding_betelgeuse', 'finding_bellatrix'],
  ['finding_betelgeuse', 'finding_alnitak'],
  ['finding_bellatrix', 'finding_mintaka'],
  ['finding_alnitak', 'finding_alnilam'],
  ['finding_alnilam', 'finding_mintaka'],
  ['finding_alnitak', 'finding_saiph'],
  ['finding_mintaka', 'finding_rigel'],
  ['finding_saiph', 'finding_rigel'],
];

interface SpatialStageProps {
  session: ObservationSession;
  uploadedMediaUrl: string | null;
  uploadedMediaKind: 'image' | 'video' | null;
  isMuted?: boolean;
  realMediaSet?: {
    origUrl: string;
    v1Url: string;
    v2Url: string;
    origVideoUrl?: string;
    v1VideoUrl?: string;
    v2VideoUrl?: string;
  } | null;
  onSelectFinding: (findingId: string, pointerEvent?: { xPx: number; yPx: number }) => void;
  onChangeCompareWipe: (position: number) => void;
  onVideoTimeUpdate?: (timeSec: number) => void;
}

interface ProjectedMarker {
  finding: CelestialFinding;
  screenX: number;
  screenY: number;
  distancePc: number;
  visible: boolean;
}

/**
 * Unified Frontier Observational & 3D Spatial Rendering Stage (`Reveal` + `Register` + `Explore`).
 * Supports:
 * 1. Synchronized dual-<video> A/B wipe playback with real stereo audio & spatial ducking
 * 2. Exact WCS gnomonic + radial-distortion sky registration
 * 3. Phase A off-axis anchor-locked camera transition + Phase B 3D parsec flight
 * 4. 585-star Gaia DR3 3D starfield with blackbody colors, constellation stick-figure deformation,
 *    and interactive 3D orbit drag & dolly zoom.
 */
export const SpatialStage: React.FC<SpatialStageProps> = ({
  session,
  uploadedMediaUrl,
  uploadedMediaKind,
  isMuted = false,
  realMediaSet = null,
  onSelectFinding,
  onChangeCompareWipe,
  onVideoTimeUpdate,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const uploadedImgRef = useRef<HTMLImageElement | null>(null);
  const realOrigImgRef = useRef<HTMLImageElement | null>(null);
  const realV1ImgRef = useRef<HTMLImageElement | null>(null);
  const realV2ImgRef = useRef<HTMLImageElement | null>(null);

  const origVideoRef = useRef<HTMLVideoElement | null>(null);
  const v1VideoRef = useRef<HTMLVideoElement | null>(null);
  const v2VideoRef = useRef<HTMLVideoElement | null>(null);

  const [realImagesRevision, setRealImagesRevision] = useState(0);
  const [videoFrameTick, setVideoFrameTick] = useState(0);
  const [viewport, setViewport] = useState({ width: 1100, height: 680 });
  const [hoveredFindingId, setHoveredFindingId] = useState<string | null>(null);
  const [focusedFindingId, setFocusedFindingId] = useState<string | null>(null);
  const [isDraggingWipe, setIsDraggingWipe] = useState(false);

  // Interactive 3D Orbit Camera State in Space mode
  const [isDraggingOrbit, setIsDraggingOrbit] = useState(false);
  const orbitDragStartRef = useRef<{ x: number; y: number; yaw: number; pitch: number }>({
    x: 0,
    y: 0,
    yaw: 0,
    pitch: 0,
  });
  const [orbitYawRad, setOrbitYawRad] = useState(0);
  const [orbitPitchRad, setOrbitPitchRad] = useState(0);
  const [orbitDistanceScale, setOrbitDistanceScale] = useState(1.0);

  // Reset orbit offset when returning to source
  useEffect(() => {
    if (session.machines.navigation === 'source') {
      setOrbitYawRad(0);
      setOrbitPitchRad(0);
      setOrbitDistanceScale(1.0);
    }
  }, [session.machines.navigation]);

  // Resize observer for crisp rendering
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const updateSize = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setViewport({
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
      }
    };
    updateSize();
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(updateSize);
      ro.observe(el);
      return () => ro.disconnect();
    }
    return undefined;
  }, []);

  // Load user-uploaded image if present
  useEffect(() => {
    if (uploadedMediaUrl && uploadedMediaKind === 'image') {
      const img = new Image();
      img.src = uploadedMediaUrl;
      img.onload = () => {
        uploadedImgRef.current = img;
        setRealImagesRevision((r) => r + 1);
      };
    } else {
      uploadedImgRef.current = null;
    }
  }, [uploadedMediaUrl, uploadedMediaKind]);

  // Load real precomputed Morocco Original / V1 / V2 image fallbacks
  useEffect(() => {
    if (!realMediaSet) {
      realOrigImgRef.current = null;
      realV1ImgRef.current = null;
      realV2ImgRef.current = null;
      setRealImagesRevision((r) => r + 1);
      return;
    }
    const loadImg = (url: string, targetRef: React.MutableRefObject<HTMLImageElement | null>) => {
      const img = new Image();
      img.src = url;
      img.onload = () => {
        targetRef.current = img;
        setRealImagesRevision((r) => r + 1);
      };
    };
    loadImg(realMediaSet.origUrl, realOrigImgRef);
    loadImg(realMediaSet.v1Url, realV1ImgRef);
    loadImg(realMediaSet.v2Url, realV2ImgRef);
  }, [realMediaSet]);

  const isSpace3D =
    session.machines.navigation === 'space' ||
    session.spatialView.mode === 'catalog_3d' ||
    session.spatialView.transitionPhase === 'phase_b_sky_to_space' ||
    session.spatialView.transitionPhase === 'at_space';

  // Synchronize HTML5 <video> elements (play/pause, audio gain ducking in 3D space, and 60fps canvas draw loop)
  useEffect(() => {
    const vOrig = origVideoRef.current;
    const v1 = v1VideoRef.current;
    const v2 = v2VideoRef.current;
    if (!vOrig || !realMediaSet?.origVideoUrl) return undefined;

    const spaceDuckFactor = isSpace3D ? 0.22 : 1.0;
    vOrig.muted = isMuted;
    vOrig.volume = Math.max(0, Math.min(1, session.audioGain * spaceDuckFactor));

    if (session.isPlaying && !isSpace3D) {
      void vOrig.play().catch(() => {});
      void v1?.play().catch(() => {});
      void v2?.play().catch(() => {});
    } else {
      vOrig.pause();
      v1?.pause();
      v2?.pause();
    }

    let rafId = 0;
    const syncLoop = () => {
      if (vOrig && !vOrig.paused) {
        const t = vOrig.currentTime;
        if (v1 && Math.abs(v1.currentTime - t) > 0.045) {
          v1.currentTime = t;
        }
        if (v2 && Math.abs(v2.currentTime - t) > 0.045) {
          v2.currentTime = t;
        }
        onVideoTimeUpdate?.(Number(t.toFixed(2)));
        setVideoFrameTick((tick) => (tick + 1) % 1000000);
      }
      if (session.isPlaying && !isSpace3D) {
        rafId = window.requestAnimationFrame(syncLoop);
      }
    };

    if (session.isPlaying && !isSpace3D) {
      rafId = window.requestAnimationFrame(syncLoop);
    }
    return () => {
      if (rafId) window.cancelAnimationFrame(rafId);
    };
  }, [
    session.isPlaying,
    session.audioGain,
    isMuted,
    isSpace3D,
    realMediaSet,
    onVideoTimeUpdate,
  ]);

  // Seek <video> elements when user scrubs timeline while paused
  useEffect(() => {
    const vOrig = origVideoRef.current;
    const v1 = v1VideoRef.current;
    const v2 = v2VideoRef.current;
    if (!vOrig || !realMediaSet?.origVideoUrl) return;
    if (Math.abs(vOrig.currentTime - session.currentSourceTimeSec) > 0.25) {
      vOrig.currentTime = session.currentSourceTimeSec;
      if (v1) v1.currentTime = session.currentSourceTimeSec;
      if (v2) v2.currentTime = session.currentSourceTimeSec;
      setVideoFrameTick((t) => t + 1);
    }
  }, [session.currentSourceTimeSec, realMediaSet]);

  const wcs = session.observationSolution.wcs;
  const srcWidth = session.sourceAsset.width;
  const srcHeight = session.sourceAsset.height;
  const scaleX = viewport.width / srcWidth;
  const scaleY = viewport.height / srcHeight;
  const aspect = viewport.width / Math.max(1, viewport.height);

  const simulationYear =
    parseInt(session.spatialView.simulationEpochISO.slice(0, 4), 10) || 2026;

  const selectedFinding =
    session.observationSolution.findings.find(
      (f) => f.id === session.selectedFindingId
    ) ?? null;

  // Compute effective 3D observer origin with interactive orbit offset
  const effectiveObserverOriginParsec = useMemo<[number, number, number]>(() => {
    const base = session.spatialView.observerOriginParsec;
    if (!isSpace3D || (orbitYawRad === 0 && orbitPitchRad === 0 && orbitDistanceScale === 1.0)) {
      return base;
    }
    const targetCenter =
      selectedFinding?.cartesianParsecICRS ??
      (raDecToUnitVector(83.8, 4.0).map((c) => c * 150) as [number, number, number]);
    const dx = base[0] - targetCenter[0];
    const dy = base[1] - targetCenter[1];
    const dz = base[2] - targetCenter[2];
    const r = Math.max(1.5, Math.hypot(dx, dy, dz) * orbitDistanceScale);
    const baseTheta = Math.atan2(dy, dx) + orbitYawRad;
    const basePhi =
      Math.max(
        -Math.PI * 0.46,
        Math.min(Math.PI * 0.46, Math.asin(dz / Math.max(1e-6, Math.hypot(dx, dy, dz))) + orbitPitchRad)
      );
    return [
      targetCenter[0] + r * Math.cos(basePhi) * Math.cos(baseTheta),
      targetCenter[1] + r * Math.cos(basePhi) * Math.sin(baseTheta),
      targetCenter[2] + r * Math.sin(basePhi),
    ];
  }, [
    session.spatialView.observerOriginParsec,
    isSpace3D,
    orbitYawRad,
    orbitPitchRad,
    orbitDistanceScale,
    selectedFinding,
  ]);

  const targetCenterParsec = useMemo<[number, number, number]>(() => {
    return (
      selectedFinding?.cartesianParsecICRS ??
      (raDecToUnitVector(83.8, 4.0).map((c) => c * 150) as [number, number, number])
    );
  }, [selectedFinding]);

  const activeCameraQuat = useMemo<[number, number, number, number]>(() => {
    const lookDir: [number, number, number] = [
      targetCenterParsec[0] - effectiveObserverOriginParsec[0],
      targetCenterParsec[1] - effectiveObserverOriginParsec[1],
      targetCenterParsec[2] - effectiveObserverOriginParsec[2],
    ];
    return lookAtDirectionQuaternion(lookDir);
  }, [targetCenterParsec, effectiveObserverOriginParsec]);

  const projectedMarkers: ProjectedMarker[] = session.observationSolution.findings.map(
    (finding) => {
      if (
        !isSpace3D &&
        finding.sourceDetection &&
        finding.evidenceClass === 'observed' &&
        simulationYear === 2026
      ) {
        return {
          finding,
          screenX: finding.sourceDetection.xPx * scaleX,
          screenY: finding.sourceDetection.yPx * scaleY,
          distancePc: finding.inferredDistancePc?.median ?? 0,
          visible: true,
        };
      }

      if (!isSpace3D && wcs) {
        let ra = finding.skyPosition.raDeg;
        let dec = finding.skyPosition.decDeg;
        if (finding.catalogRef && simulationYear !== 2026) {
          const prop = propagateCatalogPositionToYear(
            finding.catalogRef,
            simulationYear
          );
          ra = prop.raDeg;
          dec = prop.decDeg;
        }
        const px = worldToPixelWCS(ra, dec, wcs);
        return {
          finding,
          screenX: px.uPx * scaleX,
          screenY: px.vPx * scaleY,
          distancePc: finding.inferredDistancePc?.median ?? 0,
          visible:
            px.visible &&
            px.uPx >= -20 &&
            px.uPx <= srcWidth + 20 &&
            px.vPx >= -20 &&
            px.vPx <= srcHeight + 20,
        };
      }

      let pointParsec: [number, number, number];
      if (finding.catalogRef && simulationYear !== 2026) {
        pointParsec = propagateCatalogPositionToYear(
          finding.catalogRef,
          simulationYear
        ).cartesianParsec;
      } else if (finding.cartesianParsecICRS) {
        pointParsec = finding.cartesianParsecICRS;
      } else {
        const d = finding.inferredDistancePc?.median ?? 100;
        const u = raDecToUnitVector(
          finding.skyPosition.raDeg,
          finding.skyPosition.decDeg
        );
        pointParsec = [u[0] * d, u[1] * d, u[2] * d];
      }

      let shiftNdc = session.spatialView.frustumShiftNdc;
      if (
        session.spatialView.transitionPhase === 'phase_a_photo_to_sky' &&
        selectedFinding &&
        session.spatialView.lockedAnchorNdc
      ) {
        const phaseA = computeAnchorLockedPhaseAState({
          anchorRaDeg: selectedFinding.skyPosition.raDeg,
          anchorDecDeg: selectedFinding.skyPosition.decDeg,
          anchorTargetNdc: session.spatialView.lockedAnchorNdc,
          qStart: session.spatialView.cameraQuaternion,
          qEnd: activeCameraQuat,
          fovStartDeg: 37.8,
          fovEndDeg: session.spatialView.fovVerticalDeg,
          aspect,
          progress: session.spatialView.transitionProgress,
          viewportWidthPx: viewport.width,
          viewportHeightPx: viewport.height,
        });
        shiftNdc = phaseA.frustumShiftNdc;
      }

      const proj = projectWorldPointWithCamera(
        pointParsec,
        effectiveObserverOriginParsec,
        activeCameraQuat,
        session.spatialView.fovVerticalDeg,
        aspect,
        shiftNdc
      );

      return {
        finding,
        screenX: ((proj.ndcX + 1) * 0.5) * viewport.width,
        screenY: ((1 - proj.ndcY) * 0.5) * viewport.height,
        distancePc: proj.distancePc,
        visible:
          proj.visible &&
          proj.ndcX >= -1.05 &&
          proj.ndcX <= 1.05 &&
          proj.ndcY >= -1.05 &&
          proj.ndcY <= 1.05,
      };
    }
  );

  // Render canvas background (Synchronized Video/Image A/B Wipe, Registered Sky, or 585-Star 3D Space)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const w = viewport.width;
    const h = viewport.height;
    canvas.width = w;
    canvas.height = h;

    ctx.fillStyle = ALBEDIAN_COLORS.space0;
    ctx.fillRect(0, 0, w, h);

    const renderMediaPass = (
      variantProfile: 'original' | 'restored_natural' | 'restored_temporal'
    ) => {
      // 1. If live <video> streams are ready for the Morocco recording, draw the live video frame!
      const vOrig = origVideoRef.current;
      const v1 = v1VideoRef.current;
      const v2 = v2VideoRef.current;
      if (realMediaSet?.origVideoUrl && vOrig && vOrig.readyState >= 2) {
        const chosenVideo =
          variantProfile === 'original'
            ? vOrig
            : variantProfile === 'restored_natural'
              ? v1 && v1.readyState >= 2
                ? v1
                : vOrig
              : v2 && v2.readyState >= 2
                ? v2
                : v1 && v1.readyState >= 2
                  ? v1
                  : vOrig;
        ctx.drawImage(chosenVideo, 0, 0, w, h);
        return;
      }

      // 2. If precomputed Morocco Original/V1/V2 high-res frame is loaded, draw it
      if (realOrigImgRef.current) {
        const chosenImg =
          variantProfile === 'original'
            ? realOrigImgRef.current
            : variantProfile === 'restored_natural'
              ? realV1ImgRef.current ?? realOrigImgRef.current
              : realV2ImgRef.current ?? realV1ImgRef.current ?? realOrigImgRef.current;
        ctx.drawImage(chosenImg, 0, 0, w, h);
        return;
      }

      // 3. If user uploaded an image, draw it with exposure/tone filter matching branch
      if (uploadedImgRef.current) {
        ctx.save();
        if (variantProfile === 'original') {
          ctx.filter = 'brightness(0.75) contrast(1.05)';
        } else if (variantProfile === 'restored_natural') {
          ctx.filter = 'brightness(1.38) contrast(1.12)';
        } else {
          ctx.filter = 'brightness(1.55) contrast(1.18) saturate(1.08)';
        }
        ctx.drawImage(uploadedImgRef.current, 0, 0, w, h);
        ctx.restore();
        return;
      }

      // 4. Default Sahara Wide-Field Reference Observation
      const grad = ctx.createLinearGradient(0, 0, 0, h * 0.78);
      if (variantProfile === 'original') {
        grad.addColorStop(0, '#040609');
        grad.addColorStop(0.7, '#080B10');
        grad.addColorStop(1, '#0C1017');
      } else if (variantProfile === 'restored_natural') {
        grad.addColorStop(0, '#080F18');
        grad.addColorStop(0.65, '#101B29');
        grad.addColorStop(1, '#192636');
      } else {
        grad.addColorStop(0, '#09131F');
        grad.addColorStop(0.65, '#132234');
        grad.addColorStop(1, '#1E2F42');
      }
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);

      for (const pm of projectedMarkers) {
        if (!pm.visible) continue;
        if (pm.finding.evidenceClass === 'predicted') continue;
        const mag = pm.finding.catalogRef?.apparentMagnitude ?? 3.2;
        const baseAlpha =
          variantProfile === 'original'
            ? Math.max(0.18, Math.min(0.85, (4.2 - mag) * 0.22))
            : Math.max(0.45, Math.min(1.0, (4.8 - mag) * 0.28));
        const radius = Math.max(1.1, 3.6 - mag * 0.45);

        ctx.beginPath();
        ctx.arc(pm.screenX, pm.screenY, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(235, 244, 250, ${baseAlpha.toFixed(3)})`;
        ctx.fill();
      }

      const noiseCount =
        variantProfile === 'original'
          ? 420
          : variantProfile === 'restored_natural'
            ? 90
            : 25;
      for (let i = 0; i < noiseCount; i++) {
        const nx = (((i * 7919 + 104729) % 10000) / 10000) * w;
        const ny = (((i * 6271 + 54881) % 10000) / 10000) * (h * 0.78);
        ctx.fillStyle =
          variantProfile === 'original'
            ? 'rgba(180, 195, 215, 0.11)'
            : 'rgba(180, 195, 215, 0.04)';
        ctx.fillRect(nx, ny, 1.5, 1.5);
      }

      ctx.beginPath();
      ctx.moveTo(0, h * 0.79);
      ctx.quadraticCurveTo(w * 0.24, h * 0.71, w * 0.52, h * 0.76);
      ctx.quadraticCurveTo(w * 0.78, h * 0.81, w, h * 0.73);
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.closePath();
      ctx.fillStyle =
        variantProfile === 'original'
          ? '#05070A'
          : variantProfile === 'restored_natural'
            ? '#131920'
            : '#182029';
      ctx.fill();

      if (variantProfile !== 'original') {
        ctx.beginPath();
        ctx.moveTo(0, h * 0.79);
        ctx.quadraticCurveTo(w * 0.24, h * 0.71, w * 0.52, h * 0.76);
        ctx.quadraticCurveTo(w * 0.78, h * 0.81, w, h * 0.73);
        ctx.strokeStyle = 'rgba(209, 233, 239, 0.16)';
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
    };

    if (!isSpace3D) {
      const activeMode =
        session.activeVariantId === 'variant_original'
          ? 'original'
          : session.activeVariantId === 'variant_temporal'
            ? 'restored_temporal'
            : 'restored_natural';

      if (session.machines.navigation === 'comparison') {
        const splitX = Math.round(session.compareWipePosition * w);
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, splitX, h);
        ctx.clip();
        renderMediaPass('original');
        ctx.restore();

        ctx.save();
        ctx.beginPath();
        ctx.rect(splitX, 0, w - splitX, h);
        ctx.clip();
        renderMediaPass(activeMode === 'original' ? 'restored_natural' : activeMode);
        ctx.restore();

        ctx.beginPath();
        ctx.moveTo(splitX, 0);
        ctx.lineTo(splitX, h);
        ctx.strokeStyle = ALBEDIAN_COLORS.accentObservation;
        ctx.lineWidth = 1.8;
        ctx.stroke();
      } else {
        renderMediaPass(activeMode);
      }

      // If in Phase A (photo -> registered sky handoff), subtly crossfade celestial graticule
      if (session.spatialView.transitionPhase === 'phase_a_photo_to_sky') {
        ctx.fillStyle = `rgba(6, 9, 13, ${(session.spatialView.transitionProgress * 0.55).toFixed(3)})`;
        ctx.fillRect(0, 0, w, h);
      }
    } else {
      // 3D Catalog Geometry Environment (585 Gaia DR3 3D Stars + Blackbody Colors + 3D Constellation Deformation)
      const spaceGrad = ctx.createRadialGradient(
        w * 0.5,
        h * 0.5,
        20,
        w * 0.5,
        h * 0.5,
        Math.max(w, h) * 0.82
      );
      spaceGrad.addColorStop(0, '#0D1724');
      spaceGrad.addColorStop(0.55, '#080E17');
      spaceGrad.addColorStop(1, '#04070B');
      ctx.fillStyle = spaceGrad;
      ctx.fillRect(0, 0, w, h);

      // Subtle 3D spherical graticule rings
      ctx.strokeStyle = 'rgba(129, 197, 204, 0.07)';
      ctx.lineWidth = 1;
      for (const r of [120, 230, 350, 480]) {
        ctx.beginPath();
        ctx.arc(w * 0.5, h * 0.5, r, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Project all 585 3D Gaia DR3 stars relative to current 3D observer origin
      const dtYears = simulationYear - 2026;
      const projectedBgMap = new Map<string, { x: number; y: number; distPc: number; visible: boolean }>();

      for (const bgStar of GAIA_BACKGROUND_STARFIELD_3D) {
        let pt = bgStar.cartesianParsec;
        if (dtYears !== 0 && (bgStar.pmRaMasYr !== 0 || bgStar.pmDecMasYr !== 0)) {
          const dRaDeg = (bgStar.pmRaMasYr * dtYears) / 3_600_000;
          const dDecDeg = (bgStar.pmDecMasYr * dtYears) / 3_600_000;
          const u = raDecToUnitVector(bgStar.raDeg + dRaDeg, bgStar.decDeg + dDecDeg);
          pt = [u[0] * bgStar.distancePc, u[1] * bgStar.distancePc, u[2] * bgStar.distancePc];
        }

        const proj = projectWorldPointWithCamera(
          pt,
          effectiveObserverOriginParsec,
          activeCameraQuat,
          session.spatialView.fovVerticalDeg,
          aspect,
          session.spatialView.frustumShiftNdc
        );

        const sx = ((proj.ndcX + 1) * 0.5) * w;
        const sy = ((1 - proj.ndcY) * 0.5) * h;
        const vis =
          proj.visible &&
          proj.ndcX >= -1.08 &&
          proj.ndcX <= 1.08 &&
          proj.ndcY >= -1.08 &&
          proj.ndcY <= 1.08;

        if (bgStar.name) {
          projectedBgMap.set(bgStar.id, { x: sx, y: sy, distPc: proj.distancePc, visible: vis });
        }

        if (!vis) continue;

        // Apparent brightness adjusts with 3D inverse-square proximity as you fly through parsecs!
        const distRatio = Math.max(0.15, Math.min(4.0, bgStar.distancePc / Math.max(1.0, proj.distancePc)));
        const effectiveMag = bgStar.mag - 2.5 * Math.log10(distRatio);
        const radius = Math.max(0.75, Math.min(4.8, 3.8 - effectiveMag * 0.52));
        const alpha = Math.max(0.16, Math.min(0.96, (6.5 - effectiveMag) * 0.18));

        // Soft radial blackbody halo for bright navigational stars
        if (effectiveMag < 2.2) {
          const halo = ctx.createRadialGradient(sx, sy, 0.5, sx, sy, radius * 4.2);
          halo.addColorStop(0, bgStar.colorHex);
          halo.addColorStop(0.4, `${bgStar.colorHex}44`);
          halo.addColorStop(1, 'rgba(0,0,0,0)');
          ctx.fillStyle = halo;
          ctx.beginPath();
          ctx.arc(sx, sy, radius * 4.2, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(sx, sy, radius, 0, Math.PI * 2);
        ctx.fillStyle = bgStar.colorHex;
        ctx.globalAlpha = alpha;
        ctx.fill();
        ctx.globalAlpha = 1.0;
      }

      // Draw All-Sky 3D Constellation Stick Figures (deforming in 3D perspective!)
      if (session.spatialView.activeOverlays.constellationLines) {
        ctx.save();
        ctx.strokeStyle = 'rgba(171, 176, 214, 0.28)';
        ctx.lineWidth = 1.1;
        for (const edge of ALL_SKY_CONSTELLATION_EDGES) {
          const a = projectedBgMap.get(edge.fromId);
          const b = projectedBgMap.get(edge.toId);
          if (a?.visible && b?.visible && Math.hypot(a.x - b.x, a.y - b.y) < w * 0.65) {
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
        ctx.restore();
      }

      // Render Earth Origin 3D Vector when observer has left Earth
      const [ox, oy, oz] = effectiveObserverOriginParsec;
      const distFromEarthPc = Math.hypot(ox, oy, oz);
      if (
        session.spatialView.activeOverlays.earthDirectionVector &&
        distFromEarthPc > 0.01
      ) {
        const earthProj = projectWorldPointWithCamera(
          [0, 0, 0],
          effectiveObserverOriginParsec,
          activeCameraQuat,
          session.spatialView.fovVerticalDeg,
          aspect,
          session.spatialView.frustumShiftNdc
        );
        const ex = earthProj.visible
          ? Math.max(48, Math.min(w - 180, ((earthProj.ndcX + 1) * 0.5) * w))
          : 76;
        const ey = earthProj.visible
          ? Math.max(48, Math.min(h - 48, ((1 - earthProj.ndcY) * 0.5) * h))
          : h - 64;

        ctx.save();
        ctx.strokeStyle = ALBEDIAN_COLORS.accentCatalog;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(w * 0.5, h * 0.5);
        ctx.lineTo(ex, ey);
        ctx.stroke();
        ctx.fillStyle = ALBEDIAN_COLORS.accentCatalog;
        ctx.font = '11px "SF Mono", monospace';
        ctx.fillText(
          `⊕ EARTH / MOROCCO ORIGIN (${distFromEarthPc.toFixed(1)} pc · ${(distFromEarthPc * 3.26156).toFixed(1)} ly)`,
          Math.max(20, ex - 24),
          Math.min(h - 20, ey + 18)
        );
        ctx.restore();
      }
    }

    // Draw primary session constellation edges in 2D / 3D
    if (
      session.spatialView.activeOverlays.constellationLines ||
      session.machines.navigation === 'sky_registered' ||
      isSpace3D
    ) {
      const markerMap = new Map(
        projectedMarkers.map((m) => [m.finding.id, m])
      );
      ctx.save();
      ctx.strokeStyle = isSpace3D
        ? 'rgba(171, 176, 214, 0.42)'
        : 'rgba(129, 197, 204, 0.30)';
      ctx.lineWidth = 1.25;
      for (const [idA, idB] of ORION_CONSTELLATION_EDGES) {
        const a = markerMap.get(idA);
        const b = markerMap.get(idB);
        if (a?.visible && b?.visible) {
          ctx.beginPath();
          ctx.moveTo(a.screenX, a.screenY);
          ctx.lineTo(b.screenX, b.screenY);
          ctx.stroke();
        }
      }
      ctx.restore();
    }
  }, [
    viewport,
    session.activeVariantId,
    session.machines.navigation,
    session.compareWipePosition,
    session.spatialView,
    projectedMarkers,
    isSpace3D,
    realImagesRevision,
    videoFrameTick,
    effectiveObserverOriginParsec,
    activeCameraQuat,
    aspect,
    simulationYear,
    realMediaSet,
  ]);

  // Handle pointer drag for comparison wipe slider OR 3D orbit rotation in Space mode
  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isSpace3D) return;
    if ((e.target as HTMLElement).tagName === 'BUTTON') return;
    setIsDraggingOrbit(true);
    orbitDragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      yaw: orbitYawRad,
      pitch: orbitPitchRad,
    };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (isDraggingWipe && session.machines.navigation === 'comparison') {
      const rect = e.currentTarget.getBoundingClientRect();
      const ratio = Math.max(0.05, Math.min(0.95, (e.clientX - rect.left) / rect.width));
      onChangeCompareWipe(ratio);
      return;
    }
    if (isDraggingOrbit && isSpace3D) {
      const dx = e.clientX - orbitDragStartRef.current.x;
      const dy = e.clientY - orbitDragStartRef.current.y;
      setOrbitYawRad(orbitDragStartRef.current.yaw - dx * 0.0065);
      setOrbitPitchRad(
        Math.max(
          -1.2,
          Math.min(1.2, orbitDragStartRef.current.pitch + dy * 0.0055)
        )
      );
    }
  };

  const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (!isSpace3D) return;
    setOrbitDistanceScale((prev) =>
      Math.max(0.15, Math.min(3.5, prev * (e.deltaY > 0 ? 1.08 : 0.92)))
    );
  };

  const [ox, oy, oz] = effectiveObserverOriginParsec;
  const observerDistPc = Math.hypot(ox, oy, oz);

  return (
    <div
      ref={containerRef}
      className="alb-viewport-container"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={() => {
        setIsDraggingWipe(false);
        setIsDraggingOrbit(false);
      }}
      onPointerLeave={() => {
        setIsDraggingWipe(false);
        setIsDraggingOrbit(false);
      }}
      onWheel={handleWheel}
      style={{
        cursor: isSpace3D ? (isDraggingOrbit ? 'grabbing' : 'grab') : 'default',
      }}
    >
      {/* Hidden Synchronized Video Decoders for Real Morocco Recordings */}
      {realMediaSet?.origVideoUrl && (
        <div style={{ display: 'none' }} aria-hidden="true">
          <video
            ref={origVideoRef}
            src={realMediaSet.origVideoUrl}
            playsInline
            loop
            preload="auto"
            onLoadedData={() => setVideoFrameTick((t) => t + 1)}
          />
          {realMediaSet.v1VideoUrl && (
            <video
              ref={v1VideoRef}
              src={realMediaSet.v1VideoUrl}
              playsInline
              loop
              muted
              preload="auto"
              onLoadedData={() => setVideoFrameTick((t) => t + 1)}
            />
          )}
          {realMediaSet.v2VideoUrl && (
            <video
              ref={v2VideoRef}
              src={realMediaSet.v2VideoUrl}
              playsInline
              loop
              muted
              preload="auto"
              onLoadedData={() => setVideoFrameTick((t) => t + 1)}
            />
          )}
        </div>
      )}

      <canvas
        ref={canvasRef}
        style={{
          width: '100%',
          height: '100%',
          display: 'block',
        }}
      />

      {/* Epistemic Disclosure Pill for Phase B (3D Space) or Modeled Epoch */}
      {(isSpace3D || simulationYear !== 2026 || session.observationSolution.status === 'predicted_only') && (
        <div className="alb-disclosure-pill alb-tabular" role="status">
          {isSpace3D && (
            <span style={{ color: 'var(--alb-accent-simulation)' }}>
              Catalog Geometry · Distance Estimates (Observer Offset {observerDistPc.toFixed(1)} pc / {(observerDistPc * 3.26156).toFixed(1)} ly · Drag to Orbit · Scroll to Dolly)
            </span>
          )}
          {simulationYear !== 2026 && (
            <span style={{ color: 'var(--alb-accent-predicted)' }}>
              Modeled Sky · {simulationYear} CE (Observation Pinned: 2026 CE)
            </span>
          )}
          {!isSpace3D && session.observationSolution.status === 'predicted_only' && (
            <span style={{ color: 'var(--alb-accent-predicted)' }}>
              Predicted Sky · Computed from Embedded QuickTime GPS & UTC Timestamp
            </span>
          )}
        </div>
      )}

      {/* Before / After Drag Handle in Comparison Mode */}
      {session.machines.navigation === 'comparison' && (
        <button
          type="button"
          aria-label="Synchronized Before/After Comparison Wipe Handle"
          onPointerDown={() => setIsDraggingWipe(true)}
          style={{
            position: 'absolute',
            top: '50%',
            left: `${session.compareWipePosition * 100}%`,
            transform: 'translate(-50%, -50%)',
            width: 36,
            height: 36,
            borderRadius: '50%',
            backgroundColor: 'var(--alb-space-1)',
            border: '1px solid var(--alb-accent-observation)',
            color: 'var(--alb-text-primary)',
            cursor: 'ew-resize',
            zIndex: 14,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 12,
          }}
        >
          ↔
        </button>
      )}

      {/* Accessible Interactive Celestial Markers Overlay */}
      {projectedMarkers.map(({ finding, screenX, screenY, distancePc, visible }) => {
        if (!visible) return null;

        if (
          finding.evidenceClass === 'observed' &&
          !session.spatialView.activeOverlays.observedMarkers
        ) {
          return null;
        }
        if (
          finding.evidenceClass === 'predicted' &&
          !session.spatialView.activeOverlays.predictedEphemerides &&
          session.observationSolution.status !== 'predicted_only'
        ) {
          return null;
        }

        const isSelected = finding.id === session.selectedFindingId;
        const isHovered = finding.id === hoveredFindingId;
        const isFocused = finding.id === focusedFindingId;

        const effectiveEvidenceClass = isSpace3D ? 'simulated' : finding.evidenceClass;
        const spec = getMarkerVisualSpec({
          evidenceClass: effectiveEvidenceClass,
          title: finding.title,
          isSelected,
          isFocused,
          isHovered,
        });

        const showLabel =
          isSelected ||
          isHovered ||
          isFocused ||
          (session.spatialView.activeOverlays.catalogMatchedLabels &&
            (finding.evidenceClass === 'catalog_matched' || finding.evidenceClass === 'observed'));

        return (
          <button
            key={finding.id}
            type="button"
            aria-label={spec.ariaDescription}
            onClick={(e) => {
              const rect = containerRef.current?.getBoundingClientRect();
              onSelectFinding(
                finding.id,
                rect
                  ? { xPx: e.clientX - rect.left, yPx: e.clientY - rect.top }
                  : undefined
              );
            }}
            onMouseEnter={() => setHoveredFindingId(finding.id)}
            onMouseLeave={() => setHoveredFindingId(null)}
            onFocus={() => setFocusedFindingId(finding.id)}
            onBlur={() => setFocusedFindingId(null)}
            style={{
              position: 'absolute',
              left: `${screenX}px`,
              top: `${screenY}px`,
              width: `${spec.hitTargetSizePx}px`,
              height: `${spec.hitTargetSizePx}px`,
              transform: 'translate(-50%, -50%)',
              background: 'transparent',
              border: spec.showKeyboardFocusRing
                ? `2px solid ${ALBEDIAN_COLORS.accentObservation}`
                : 'none',
              borderRadius: '50%',
              padding: 0,
              cursor: 'pointer',
              zIndex: isSelected ? 11 : 9,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <svg
              width={spec.hitTargetSizePx}
              height={spec.hitTargetSizePx}
              viewBox="-22 -22 44 44"
              style={{ overflow: 'visible' }}
            >
              {spec.shape !== 'solid_dot_tick' && (
                <circle
                  cx={0}
                  cy={0}
                  r={spec.visibleRadiusPx}
                  fill="none"
                  stroke={spec.colorHex}
                  strokeWidth={isSelected ? 1.8 : 1.2}
                  strokeDasharray={spec.strokeDasharray}
                />
              )}

              {finding.evidenceClass !== 'predicted' && (
                <circle
                  cx={0}
                  cy={0}
                  r={isSelected ? 3.2 : 2.2}
                  fill={spec.colorHex}
                />
              )}

              {(isSelected || spec.shape === 'bracket_reticle' || spec.shape === 'solid_dot_tick') && (
                <g stroke={spec.colorHex} strokeWidth={1.1}>
                  <line x1={0} y1={-spec.visibleRadiusPx - 4} x2={0} y2={-spec.visibleRadiusPx - 1} />
                  <line x1={0} y1={spec.visibleRadiusPx + 1} x2={0} y2={spec.visibleRadiusPx + 4} />
                  <line x1={-spec.visibleRadiusPx - 4} y1={0} x2={-spec.visibleRadiusPx - 1} y2={0} />
                  <line x1={spec.visibleRadiusPx + 1} y1={0} x2={spec.visibleRadiusPx + 4} y2={0} />
                </g>
              )}

              {showLabel && (
                <g>
                  <polyline
                    points={`${spec.visibleRadiusPx * 0.7},${-spec.visibleRadiusPx * 0.7} ${
                      spec.visibleRadiusPx + 8
                    },${-spec.visibleRadiusPx - 6} ${spec.visibleRadiusPx + 18},${
                      -spec.visibleRadiusPx - 6
                    }`}
                    fill="none"
                    stroke={spec.colorHex}
                    strokeWidth={1}
                  />
                  <text
                    x={spec.visibleRadiusPx + 22}
                    y={-spec.visibleRadiusPx - 3}
                    fill={ALBEDIAN_COLORS.textPrimary}
                    fontSize={11}
                    fontFamily="var(--alb-font-mono)"
                  >
                    {finding.title}{' '}
                    {isSpace3D && distancePc > 0 ? `(${distancePc.toFixed(1)} pc)` : `[${spec.shortCode}]`}
                  </text>
                </g>
              )}
            </svg>
          </button>
        );
      })}
    </div>
  );
};
