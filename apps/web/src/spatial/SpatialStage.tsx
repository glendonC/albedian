import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ObservationSession } from '@albedian/contracts';
import { ALBEDIAN_COLORS } from '@albedian/design-system';
import {
  computeAnchorLockedPhaseAState,
  lookAtDirectionQuaternion,
  projectWorldPointWithCamera,
  propagateCatalogPositionToYear,
  raDecToUnitVector,
  worldToPixelWCS,
} from '@albedian/camera-rig';
import { MarkerOverlay, ProjectedMarker } from './MarkerOverlay.js';
import {
  render2DMediaVariantPass,
  render3DCatalogSpacePass,
  renderConstellationStickFigures,
} from './canvasRenderers.js';
import {
  computeAspectFitRect,
  computeLiveVideoTrackingOffset,
} from './liveVideoTracker.js';

export interface SpatialStageProps {
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
  showIsometricGrid?: boolean;
  showGeodesicRays?: boolean;
  showExplodedTeardown?: boolean;
}

/**
 * Unified Frontier Observational & 3D Spatial Rendering Stage (`Reveal` + `Register` + `Explore`).
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
  showIsometricGrid = true,
  showGeodesicRays = true,
  showExplodedTeardown = false,
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

  const [isDraggingOrbit, setIsDraggingOrbit] = useState(false);
  const orbitDragStartRef = useRef({ x: 0, y: 0, yaw: 0, pitch: 0 });
  const [orbitYawRad, setOrbitYawRad] = useState(0);
  const [orbitPitchRad, setOrbitPitchRad] = useState(0);
  const [orbitDistanceScale, setOrbitDistanceScale] = useState(1.0);

  useEffect(() => {
    if (session.machines.navigation === 'source') {
      setOrbitYawRad(0);
      setOrbitPitchRad(0);
      setOrbitDistanceScale(1.0);
    }
  }, [session.machines.navigation]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const updateSize = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setViewport({ width: Math.round(rect.width), height: Math.round(rect.height) });
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
        if (v1 && Math.abs(v1.currentTime - t) > 0.045) v1.currentTime = t;
        if (v2 && Math.abs(v2.currentTime - t) > 0.045) v2.currentTime = t;
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
  }, [session.isPlaying, session.audioGain, isMuted, isSpace3D, realMediaSet, onVideoTimeUpdate]);

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
  const aspect = viewport.width / Math.max(1, viewport.height);
  const simulationYear = parseInt(session.spatialView.simulationEpochISO.slice(0, 4), 10) || 2026;

  // Aspect-preserving fit rect for real media (portrait or landscape)
  const fitRect = useMemo(() => {
    const hasExternalMedia = Boolean(realMediaSet || uploadedMediaUrl);
    if (!hasExternalMedia) {
      return {
        x: 0,
        y: 0,
        width: viewport.width,
        height: viewport.height,
        scaleX: viewport.width / Math.max(1, srcWidth),
        scaleY: viewport.height / Math.max(1, srcHeight),
      };
    }
    return computeAspectFitRect(srcWidth, srcHeight, viewport.width, viewport.height);
  }, [realMediaSet, uploadedMediaUrl, srcWidth, srcHeight, viewport.width, viewport.height]);

  const selectedFinding =
    session.observationSolution.findings.find((f) => f.id === session.selectedFindingId) ?? null;

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
    const basePhi = Math.max(
      -Math.PI * 0.46,
      Math.min(Math.PI * 0.46, Math.asin(dz / Math.max(1e-6, Math.hypot(dx, dy, dz))) + orbitPitchRad)
    );
    return [
      targetCenter[0] + r * Math.cos(basePhi) * Math.cos(baseTheta),
      targetCenter[1] + r * Math.cos(basePhi) * Math.sin(baseTheta),
      targetCenter[2] + r * Math.sin(basePhi),
    ];
  }, [session.spatialView.observerOriginParsec, isSpace3D, orbitYawRad, orbitPitchRad, orbitDistanceScale, selectedFinding]);

  const targetCenterParsec = useMemo<[number, number, number]>(() => {
    return (
      selectedFinding?.cartesianParsecICRS ??
      (raDecToUnitVector(83.8, 4.0).map((c) => c * 150) as [number, number, number])
    );
  }, [selectedFinding]);

  const activeCameraQuat = useMemo<[number, number, number, number]>(() => {
    return lookAtDirectionQuaternion([
      targetCenterParsec[0] - effectiveObserverOriginParsec[0],
      targetCenterParsec[1] - effectiveObserverOriginParsec[1],
      targetCenterParsec[2] - effectiveObserverOriginParsec[2],
    ]);
  }, [targetCenterParsec, effectiveObserverOriginParsec]);

  const liveBeaconOffsetPx = useMemo<[number, number]>(() => {
    if (isSpace3D) return [0, 0];
    const moonFinding = session.observationSolution.findings.find(
      (f) => f.id === 'finding-observed-moon' && f.sourceDetection
    );
    return computeLiveVideoTrackingOffset({
      videoEl: origVideoRef.current,
      moonFinding,
      srcWidth,
      srcHeight,
    });
  }, [isSpace3D, session.observationSolution.findings, srcWidth, srcHeight, videoFrameTick, realImagesRevision]);

  const projectedMarkers: ProjectedMarker[] = session.observationSolution.findings.map((finding) => {
    if (!isSpace3D && finding.sourceDetection && finding.evidenceClass === 'observed' && simulationYear === 2026) {
      return {
        finding,
        screenX: fitRect.x + (finding.sourceDetection.xPx + liveBeaconOffsetPx[0]) * fitRect.scaleX,
        screenY: fitRect.y + (finding.sourceDetection.yPx + liveBeaconOffsetPx[1]) * fitRect.scaleY,
        distancePc: finding.inferredDistancePc?.median ?? 0,
        visible: true,
      };
    }

    if (!isSpace3D && wcs) {
      let ra = finding.skyPosition.raDeg;
      let dec = finding.skyPosition.decDeg;
      if (finding.catalogRef && simulationYear !== 2026) {
        const prop = propagateCatalogPositionToYear(finding.catalogRef, simulationYear);
        ra = prop.raDeg;
        dec = prop.decDeg;
      }
      const px = worldToPixelWCS(ra, dec, wcs);
      const uShifted = px.uPx + liveBeaconOffsetPx[0];
      const vShifted = px.vPx + liveBeaconOffsetPx[1];
      const maxSkyY =
        session.observationSolution.status === 'approximate' &&
        session.machines.navigation !== 'sky_registered'
          ? srcHeight * 0.46
          : srcHeight + 12;
      return {
        finding,
        screenX: fitRect.x + uShifted * fitRect.scaleX,
        screenY: fitRect.y + vShifted * fitRect.scaleY,
        distancePc: finding.inferredDistancePc?.median ?? 0,
        visible:
          px.visible &&
          uShifted >= -12 &&
          uShifted <= srcWidth + 12 &&
          vShifted >= -12 &&
          vShifted <= maxSkyY,
      };
    }

    let pointParsec: [number, number, number];
    if (finding.catalogRef && simulationYear !== 2026) {
      pointParsec = propagateCatalogPositionToYear(finding.catalogRef, simulationYear).cartesianParsec;
    } else if (finding.cartesianParsecICRS) {
      pointParsec = finding.cartesianParsecICRS;
    } else {
      const d = finding.inferredDistancePc?.median ?? 100;
      const u = raDecToUnitVector(finding.skyPosition.raDeg, finding.skyPosition.decDeg);
      pointParsec = [u[0] * d, u[1] * d, u[2] * d];
    }

    let shiftNdc = session.spatialView.frustumShiftNdc;
    if (
      session.spatialView.transitionPhase === 'phase_a_photo_to_sky' &&
      selectedFinding &&
      session.spatialView.lockedAnchorNdc
    ) {
      shiftNdc = computeAnchorLockedPhaseAState({
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
      }).frustumShiftNdc;
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
      visible: proj.visible && proj.ndcX >= -1.05 && proj.ndcX <= 1.05 && proj.ndcY >= -1.05 && proj.ndcY <= 1.05,
    };
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    const w = viewport.width;
    const h = viewport.height;
    canvas.width = w;
    canvas.height = h;
    ctx.fillStyle = ALBEDIAN_COLORS.space0;
    ctx.fillRect(0, 0, w, h);

    if (!isSpace3D) {
      const activeMode =
        session.activeVariantId === 'variant_original'
          ? 'original'
          : session.activeVariantId === 'variant_temporal'
            ? 'restored_temporal'
            : 'restored_natural';

      const drawPass = (profile: 'original' | 'restored_natural' | 'restored_temporal') =>
        render2DMediaVariantPass({
          ctx,
          width: w,
          height: h,
          fitRect,
          variantProfile: profile,
          origVideo: origVideoRef.current,
          v1Video: v1VideoRef.current,
          v2Video: v2VideoRef.current,
          hasLiveVideos: Boolean(realMediaSet?.origVideoUrl),
          realOrigImg: realOrigImgRef.current,
          realV1Img: realV1ImgRef.current,
          realV2Img: realV2ImgRef.current,
          uploadedImg: uploadedImgRef.current,
          projectedMarkers,
          wcs,
        });

      if (session.machines.navigation === 'comparison') {
        const splitX = Math.round(session.compareWipePosition * w);
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, splitX, h);
        ctx.clip();
        drawPass('original');
        ctx.restore();

        ctx.save();
        ctx.beginPath();
        ctx.rect(splitX, 0, w - splitX, h);
        ctx.clip();
        drawPass(activeMode === 'original' ? 'restored_natural' : activeMode);
        ctx.restore();

        ctx.beginPath();
        ctx.moveTo(splitX, 0);
        ctx.lineTo(splitX, h);
        ctx.strokeStyle = ALBEDIAN_COLORS.accentObservation;
        ctx.lineWidth = 1.8;
        ctx.stroke();
      } else {
        drawPass(activeMode);
      }

      if (session.spatialView.transitionPhase === 'phase_a_photo_to_sky') {
        ctx.fillStyle = `rgba(6, 9, 13, ${(session.spatialView.transitionProgress * 0.55).toFixed(3)})`;
        ctx.fillRect(0, 0, w, h);
      }
    } else {
      render3DCatalogSpacePass({
        ctx,
        width: w,
        height: h,
        aspect,
        simulationYear,
        effectiveObserverOriginParsec,
        activeCameraQuat,
        fovVerticalDeg: session.spatialView.fovVerticalDeg,
        frustumShiftNdc: session.spatialView.frustumShiftNdc,
        showConstellationLines: session.spatialView.activeOverlays.constellationLines,
        showEarthDirectionVector: session.spatialView.activeOverlays.earthDirectionVector,
        selectedFinding,
      });
    }

    renderConstellationStickFigures({
      ctx,
      projectedMarkers,
      wcs,
      fitRect,
      srcWidth,
      srcHeight,
      liveBeaconOffsetPx,
      isSpace3D,
      selectedFinding: selectedFinding ?? undefined,
      showIsometricGrid,
      showGeodesicRays,
      showExplodedTeardown,
      simulationYear,
    });
  }, [
    viewport,
    fitRect,
    session.activeVariantId,
    session.machines.navigation,
    session.compareWipePosition,
    session.spatialView,
    session.observationSolution.status,
    projectedMarkers,
    wcs,
    srcWidth,
    srcHeight,
    liveBeaconOffsetPx,
    isSpace3D,
    realImagesRevision,
    videoFrameTick,
    effectiveObserverOriginParsec,
    activeCameraQuat,
    aspect,
    simulationYear,
    realMediaSet,
    selectedFinding,
    showIsometricGrid,
    showGeodesicRays,
    showExplodedTeardown,
  ]);

  const [ox, oy, oz] = effectiveObserverOriginParsec;
  const observerDistPc = Math.hypot(ox, oy, oz);

  return (
    <div
      ref={containerRef}
      className="alb-viewport-container"
      onPointerDown={(e) => {
        if (!isSpace3D || (e.target as HTMLElement).tagName === 'BUTTON') return;
        setIsDraggingOrbit(true);
        orbitDragStartRef.current = { x: e.clientX, y: e.clientY, yaw: orbitYawRad, pitch: orbitPitchRad };
      }}
      onPointerMove={(e) => {
        if (isDraggingWipe && session.machines.navigation === 'comparison') {
          const rect = e.currentTarget.getBoundingClientRect();
          onChangeCompareWipe(Math.max(0.05, Math.min(0.95, (e.clientX - rect.left) / rect.width)));
          return;
        }
        if (isDraggingOrbit && isSpace3D) {
          const dx = e.clientX - orbitDragStartRef.current.x;
          const dy = e.clientY - orbitDragStartRef.current.y;
          setOrbitYawRad(orbitDragStartRef.current.yaw - dx * 0.0065);
          setOrbitPitchRad(Math.max(-1.2, Math.min(1.2, orbitDragStartRef.current.pitch + dy * 0.0055)));
        }
      }}
      onPointerUp={() => {
        setIsDraggingWipe(false);
        setIsDraggingOrbit(false);
      }}
      onPointerLeave={() => {
        setIsDraggingWipe(false);
        setIsDraggingOrbit(false);
      }}
      onWheel={(e) => {
        if (!isSpace3D) return;
        setOrbitDistanceScale((prev) => Math.max(0.15, Math.min(3.5, prev * (e.deltaY > 0 ? 1.08 : 0.92))));
      }}
      style={{ cursor: isSpace3D ? (isDraggingOrbit ? 'grabbing' : 'grab') : 'default' }}
    >
      {realMediaSet?.origVideoUrl && (
        <div style={{ display: 'none' }} aria-hidden="true">
          <video ref={origVideoRef} src={realMediaSet.origVideoUrl} playsInline loop preload="auto" onLoadedData={() => setVideoFrameTick((t) => t + 1)} />
          {realMediaSet.v1VideoUrl && (
            <video ref={v1VideoRef} src={realMediaSet.v1VideoUrl} playsInline loop muted preload="auto" onLoadedData={() => setVideoFrameTick((t) => t + 1)} />
          )}
          {realMediaSet.v2VideoUrl && (
            <video ref={v2VideoRef} src={realMediaSet.v2VideoUrl} playsInline loop muted preload="auto" onLoadedData={() => setVideoFrameTick((t) => t + 1)} />
          )}
        </div>
      )}

      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />

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

      <MarkerOverlay
        projectedMarkers={projectedMarkers}
        selectedFindingId={session.selectedFindingId}
        hoveredFindingId={hoveredFindingId}
        focusedFindingId={focusedFindingId}
        isSpace3D={isSpace3D}
        solutionStatus={session.observationSolution.status}
        activeOverlays={session.spatialView.activeOverlays}
        containerRef={containerRef}
        onSelectFinding={onSelectFinding}
        onHoverFinding={setHoveredFindingId}
        onFocusFinding={setFocusedFindingId}
      />
    </div>
  );
};
