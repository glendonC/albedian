import { CelestialFinding, WCSCalibrationModel } from '@albedian/contracts';
import { ALBEDIAN_COLORS } from '@albedian/design-system';
import {
  projectWorldPointWithCamera,
  raDecToUnitVector,
  worldToPixelWCS,
} from '@albedian/camera-rig';
import {
  ALL_SKY_CONSTELLATION_EDGES,
  GAIA_BACKGROUND_STARFIELD_3D,
  bpRpToStarColorHex,
} from './allSkyStarfield.js';
import { compute3DNearestStellarNeighbors } from './astrophysicsEngine.js';
import { ProjectedMarker } from './MarkerOverlay.js';
import { AspectFitRect } from './liveVideoTracker.js';
import {
  render3DTargetStellarPhotosphere,
  renderCurvedEquatorialGraticule,
  renderDeepSkyObjectsLayer,
  renderOpticalStarPSF,
  renderPhysicalLunarDisc,
} from './frontierAstronomyRenderer.js';
import {
  renderFieldSimulatorRegistrationOverlay,
  renderIsometricGeodesicHologram,
} from './holographicIsometricRenderer.js';

export const ORION_CONSTELLATION_EDGES: Array<[string, string]> = [
  ['finding_betelgeuse', 'finding_bellatrix'],
  ['finding_betelgeuse', 'finding_alnitak'],
  ['finding_bellatrix', 'finding_mintaka'],
  ['finding_alnitak', 'finding_alnilam'],
  ['finding_alnilam', 'finding_mintaka'],
  ['finding_alnitak', 'finding_saiph'],
  ['finding_mintaka', 'finding_rigel'],
  ['finding_saiph', 'finding_rigel'],
];

export interface Render2DMediaPassOptions {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  fitRect: AspectFitRect;
  variantProfile: 'original' | 'restored_natural' | 'restored_temporal';
  origVideo: HTMLVideoElement | null;
  v1Video: HTMLVideoElement | null;
  v2Video: HTMLVideoElement | null;
  hasLiveVideos: boolean;
  realOrigImg: HTMLImageElement | null;
  realV1Img: HTMLImageElement | null;
  realV2Img: HTMLImageElement | null;
  uploadedImg: HTMLImageElement | null;
  projectedMarkers: ProjectedMarker[];
  wcs?: WCSCalibrationModel;
}

function drawAspectFittedMediaSource(
  ctx: CanvasRenderingContext2D,
  source: CanvasImageSource,
  width: number,
  height: number,
  fitRect: AspectFitRect,
  extraFilter?: string
): void {
  const hasBars = fitRect.x > 2 || fitRect.y > 2;
  if (hasBars) {
    ctx.save();
    ctx.filter = 'blur(28px) brightness(0.32) saturate(1.2)';
    ctx.drawImage(source, -24, -24, width + 48, height + 48);
    ctx.restore();

    ctx.fillStyle = 'rgba(3, 5, 9, 0.65)';
    ctx.fillRect(0, 0, width, height);
  }

  ctx.save();
  if (extraFilter) {
    ctx.filter = extraFilter;
  }
  ctx.drawImage(source, fitRect.x, fitRect.y, fitRect.width, fitRect.height);
  ctx.restore();

  if (hasBars) {
    ctx.strokeStyle = 'rgba(255, 69, 34, 0.28)';
    ctx.lineWidth = 1;
    ctx.strokeRect(fitRect.x + 0.5, fitRect.y + 0.5, fitRect.width - 1, fitRect.height - 1);
  }
}

/**
 * Render a 2D source media variant pass inside the aspect-preserving `fitRect`
 * with an ambient blurred instrument backdrop when pillarboxed/letterboxed.
 */
export function render2DMediaVariantPass(opts: Render2DMediaPassOptions): void {
  const {
    ctx,
    width: w,
    height: h,
    fitRect,
    variantProfile,
    origVideo,
    v1Video,
    v2Video,
    hasLiveVideos,
    realOrigImg,
    realV1Img,
    realV2Img,
    uploadedImg,
    projectedMarkers,
    wcs,
  } = opts;

  if (hasLiveVideos && origVideo && origVideo.readyState >= 2) {
    const chosenVideo =
      variantProfile === 'original'
        ? origVideo
        : variantProfile === 'restored_natural'
          ? v1Video && v1Video.readyState >= 2
            ? v1Video
            : origVideo
          : v2Video && v2Video.readyState >= 2
            ? v2Video
            : v1Video && v1Video.readyState >= 2
              ? v1Video
              : origVideo;
    drawAspectFittedMediaSource(ctx, chosenVideo, w, h, fitRect);
    return;
  }

  if (realOrigImg) {
    const chosenImg =
      variantProfile === 'original'
        ? realOrigImg
        : variantProfile === 'restored_natural'
          ? realV1Img ?? realOrigImg
          : realV2Img ?? realV1Img ?? realOrigImg;
    drawAspectFittedMediaSource(ctx, chosenImg, w, h, fitRect);
    return;
  }

  if (uploadedImg) {
    const filter =
      variantProfile === 'original'
        ? 'brightness(0.75) contrast(1.05)'
        : variantProfile === 'restored_natural'
          ? 'brightness(1.38) contrast(1.12)'
          : 'brightness(1.55) contrast(1.18) saturate(1.08)';
    drawAspectFittedMediaSource(ctx, uploadedImg, w, h, fitRect, filter);
    return;
  }

  // Deep nocturnal Sahara sky canvas with Milky Way galactic band & optical star PSF
  const grad = ctx.createLinearGradient(0, 0, 0, h * 0.82);
  if (variantProfile === 'original') {
    grad.addColorStop(0, '#020306');
    grad.addColorStop(0.65, '#050811');
    grad.addColorStop(1, '#090F1A');
  } else if (variantProfile === 'restored_natural') {
    grad.addColorStop(0, '#03060C');
    grad.addColorStop(0.6, '#07101E');
    grad.addColorStop(1, '#0F1D30');
  } else {
    grad.addColorStop(0, '#040811');
    grad.addColorStop(0.6, '#0A1526');
    grad.addColorStop(1, '#15263D');
  }
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);

  // Diagonal Milky Way winter galactic band across Monoceros / Taurus / Orion
  if (variantProfile !== 'original') {
    ctx.save();
    ctx.translate(w * 0.44, h * 0.42);
    ctx.rotate(-0.58);
    const mwGrad = ctx.createRadialGradient(0, 0, 12, 0, 0, w * 0.46);
    mwGrad.addColorStop(0, 'rgba(56, 189, 248, 0.085)');
    mwGrad.addColorStop(0.45, 'rgba(167, 139, 250, 0.045)');
    mwGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = mwGrad;
    ctx.scale(1.6, 0.48);
    ctx.beginPath();
    ctx.arc(0, 0, w * 0.45, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // Project background Gaia DR3 stars into the 2D sky field
  if (wcs) {
    for (const bg of GAIA_BACKGROUND_STARFIELD_3D) {
      const p = worldToPixelWCS(bg.raDeg, bg.decDeg, wcs);
      if (!p.visible) continue;
      const sx = fitRect.x + p.uPx * fitRect.scaleX;
      const sy = fitRect.y + p.vPx * fitRect.scaleY;
      if (sx < 0 || sx > w || sy < 0 || sy > h * 0.74) continue;
      const alpha =
        variantProfile === 'original'
          ? Math.max(0.08, Math.min(0.45, (5.5 - bg.mag) * 0.12))
          : Math.max(0.18, Math.min(0.85, (6.2 - bg.mag) * 0.18));
      const r = Math.max(0.65, (5.8 - bg.mag) * 0.34);
      ctx.fillStyle = bg.colorHex;
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1.0;
  }

  // Render primary catalog & observed stars with full Optical PSF + diffraction spikes
  for (const pm of projectedMarkers) {
    if (!pm.visible || pm.finding.evidenceClass === 'predicted') continue;
    const mag = pm.finding.catalogRef?.apparentMagnitude ?? 2.8;
    const bpRp = pm.finding.catalogRef?.colorIndexBpRp ?? 0.2;
    const starHex = bpRpToStarColorHex(bpRp);
    const radius = Math.max(1.4, 4.1 - mag * 0.52);
    renderOpticalStarPSF(ctx, pm.screenX, pm.screenY, radius, starHex, mag);
  }

  // Horizon sodium/desert airglow rim + synthetic site dune ridge
  const airglow = ctx.createLinearGradient(0, h * 0.68, 0, h * 0.82);
  airglow.addColorStop(0, 'rgba(255, 69, 34, 0)');
  airglow.addColorStop(0.75, 'rgba(255, 69, 34, 0.065)');
  airglow.addColorStop(1, 'rgba(15, 23, 42, 0.4)');
  ctx.fillStyle = airglow;
  ctx.fillRect(0, h * 0.68, w, h * 0.16);

  ctx.beginPath();
  ctx.moveTo(0, h * 0.79);
  ctx.quadraticCurveTo(w * 0.24, h * 0.71, w * 0.52, h * 0.76);
  ctx.quadraticCurveTo(w * 0.78, h * 0.81, w, h * 0.73);
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fillStyle = '#04060A';
  ctx.fill();

  // Subtle dune crest hairline
  ctx.strokeStyle = 'rgba(255, 69, 34, 0.24)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, h * 0.79);
  ctx.quadraticCurveTo(w * 0.24, h * 0.71, w * 0.52, h * 0.76);
  ctx.quadraticCurveTo(w * 0.78, h * 0.81, w, h * 0.73);
  ctx.stroke();
}

export function renderConstellationStickFigures(params: {
  ctx: CanvasRenderingContext2D;
  projectedMarkers: ProjectedMarker[];
  wcs: WCSCalibrationModel | undefined;
  fitRect: AspectFitRect;
  srcWidth: number;
  srcHeight: number;
  liveBeaconOffsetPx: [number, number];
  isSpace3D: boolean;
  selectedFinding?: CelestialFinding;
  showIsometricGrid?: boolean;
  showGeodesicRays?: boolean;
  showExplodedTeardown?: boolean;
  simulationYear?: number;
}): void {
  const {
    ctx,
    projectedMarkers,
    wcs,
    fitRect,
    srcWidth,
    srcHeight,
    liveBeaconOffsetPx,
    isSpace3D,
    selectedFinding,
    showIsometricGrid = true,
    showGeodesicRays = true,
    showExplodedTeardown = false,
    simulationYear = 2026,
  } = params;

  const maxSkyY = srcHeight < srcWidth ? srcHeight * 0.75 : srcHeight * 0.46;

  if (!isSpace3D && wcs) {
    renderCurvedEquatorialGraticule(
      ctx,
      wcs,
      fitRect,
      srcWidth,
      srcHeight,
      maxSkyY,
      liveBeaconOffsetPx
    );
    renderDeepSkyObjectsLayer(ctx, wcs, fitRect, srcWidth, maxSkyY, liveBeaconOffsetPx);
  }

  const markerMap = new Map(projectedMarkers.map((m) => [m.finding.id, m]));
  ctx.save();
  ctx.strokeStyle = isSpace3D ? 'rgba(171, 176, 214, 0.45)' : 'rgba(56, 189, 248, 0.42)';
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

  if (!isSpace3D && wcs) {
    const namedStarMap = new Map(
      GAIA_BACKGROUND_STARFIELD_3D.filter((s) => Boolean(s.name)).map((s) => [s.id, s])
    );
    ctx.strokeStyle = 'rgba(251, 191, 36, 0.36)';
    ctx.setLineDash([4, 3]);
    for (const edge of ALL_SKY_CONSTELLATION_EDGES) {
      const sA = namedStarMap.get(edge.fromId);
      const sB = namedStarMap.get(edge.toId);
      if (!sA || !sB) continue;
      const pA = worldToPixelWCS(sA.raDeg, sA.decDeg, wcs);
      const pB = worldToPixelWCS(sB.raDeg, sB.decDeg, wcs);
      if (!pA.visible || !pB.visible) continue;

      const uA = pA.uPx + liveBeaconOffsetPx[0];
      const vA = pA.vPx + liveBeaconOffsetPx[1];
      const uB = pB.uPx + liveBeaconOffsetPx[0];
      const vB = pB.vPx + liveBeaconOffsetPx[1];

      const inBounds =
        uA >= -30 &&
        uA <= srcWidth + 30 &&
        vA >= -30 &&
        vA <= maxSkyY &&
        uB >= -30 &&
        uB <= srcWidth + 30 &&
        vB >= -30 &&
        vB <= maxSkyY;
      if (!inBounds) continue;

      const sxA = fitRect.x + uA * fitRect.scaleX;
      const syA = fitRect.y + vA * fitRect.scaleY;
      const sxB = fitRect.x + uB * fitRect.scaleX;
      const syB = fitRect.y + vB * fitRect.scaleY;

      if (Math.hypot(sxA - sxB, syA - syB) < fitRect.width * 0.75) {
        ctx.beginPath();
        ctx.moveTo(sxA, syA);
        ctx.lineTo(sxB, syB);
        ctx.stroke();
      }
    }
  }
  ctx.restore();

  // Render physical lunar disc if Moon is in the field
  const moonMarker = markerMap.get('finding-observed-moon');
  if (moonMarker?.visible && !isSpace3D) {
    renderPhysicalLunarDisc(ctx, moonMarker.screenX, moonMarker.screenY, 10.5);
  }

  // Render Isometric 3D Floor + Geodesic Photon Deflection Streamlines + Exploded Teardown
  renderIsometricGeodesicHologram(ctx, {
    width: ctx.canvas.width,
    height: ctx.canvas.height,
    projectedMarkers,
    selectedFinding,
    isSpace3D,
    showIsometricGrid,
    showGeodesicRays,
    showExplodedTeardown,
    simulationYear,
  });

  renderFieldSimulatorRegistrationOverlay(
    ctx,
    ctx.canvas.width,
    ctx.canvas.height,
    selectedFinding,
    isSpace3D,
    showExplodedTeardown
  );
}

export interface Render3DCatalogSpaceOptions {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  aspect: number;
  simulationYear: number;
  effectiveObserverOriginParsec: [number, number, number];
  activeCameraQuat: [number, number, number, number];
  fovVerticalDeg: number;
  frustumShiftNdc: [number, number];
  showConstellationLines: boolean;
  showEarthDirectionVector: boolean;
  selectedFinding?: ProjectedMarker['finding'] | null;
}

export function render3DCatalogSpacePass(opts: Render3DCatalogSpaceOptions): void {
  const {
    ctx,
    width: w,
    height: h,
    aspect,
    simulationYear,
    effectiveObserverOriginParsec,
    activeCameraQuat,
    fovVerticalDeg,
    frustumShiftNdc,
    showConstellationLines,
    showEarthDirectionVector,
    selectedFinding,
  } = opts;

  const spaceGrad = ctx.createRadialGradient(
    w * 0.5,
    h * 0.5,
    18,
    w * 0.5,
    h * 0.5,
    Math.max(w, h) * 0.85
  );
  spaceGrad.addColorStop(0, '#070C15');
  spaceGrad.addColorStop(0.55, '#04070D');
  spaceGrad.addColorStop(1, '#010204');
  ctx.fillStyle = spaceGrad;
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = 'rgba(255, 69, 34, 0.09)';
  ctx.lineWidth = 1;
  for (const r of [110, 210, 320, 440]) {
    ctx.beginPath();
    ctx.arc(w * 0.5, h * 0.5, r, 0, Math.PI * 2);
    ctx.stroke();
  }

  const dtYears = simulationYear - 2026;
  const projectedBgMap = new Map<
    string,
    { x: number; y: number; distPc: number; visible: boolean }
  >();

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
      fovVerticalDeg,
      aspect,
      frustumShiftNdc
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

    const distRatio = Math.max(
      0.15,
      Math.min(4.0, bgStar.distancePc / Math.max(1.0, proj.distancePc))
    );
    const effectiveMag = bgStar.mag - 2.5 * Math.log10(distRatio);
    const radius = Math.max(0.75, Math.min(4.8, 3.8 - effectiveMag * 0.52));
    const alpha = Math.max(0.16, Math.min(0.96, (6.5 - effectiveMag) * 0.18));

    if (effectiveMag < 2.2) {
      renderOpticalStarPSF(ctx, sx, sy, radius, bgStar.colorHex, effectiveMag);
    } else {
      ctx.beginPath();
      ctx.arc(sx, sy, radius, 0, Math.PI * 2);
      ctx.fillStyle = bgStar.colorHex;
      ctx.globalAlpha = alpha;
      ctx.fill();
      ctx.globalAlpha = 1.0;
    }
  }

  if (showConstellationLines) {
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

  const [ox, oy, oz] = effectiveObserverOriginParsec;
  const distFromEarthPc = Math.hypot(ox, oy, oz);
  if (showEarthDirectionVector && distFromEarthPc > 0.01) {
    const earthProj = projectWorldPointWithCamera(
      [0, 0, 0],
      effectiveObserverOriginParsec,
      activeCameraQuat,
      fovVerticalDeg,
      aspect,
      frustumShiftNdc
    );
    const ex = earthProj.visible
      ? Math.max(300, Math.min(w - 340, ((earthProj.ndcX + 1) * 0.5) * w))
      : 310;
    const ey = earthProj.visible
      ? Math.max(80, Math.min(h - 190, ((1 - earthProj.ndcY) * 0.5) * h))
      : h - 195;

    ctx.save();
    ctx.strokeStyle = '#FF4522';
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(w * 0.5, h * 0.5);
    ctx.lineTo(ex, ey);
    ctx.stroke();
    ctx.fillStyle = '#FF4522';
    ctx.font = '600 10px "JetBrains Mono", monospace';
    ctx.fillText(
      `⊕ EARTH ORIGIN (${distFromEarthPc.toFixed(1)} pc · ${(distFromEarthPc * 3.26156).toFixed(1)} ly)`,
      ex + 8,
      ey + 4
    );
    ctx.restore();
  }

  if (selectedFinding?.cartesianParsecICRS) {
    const selProj = projectWorldPointWithCamera(
      selectedFinding.cartesianParsecICRS,
      effectiveObserverOriginParsec,
      activeCameraQuat,
      fovVerticalDeg,
      aspect,
      frustumShiftNdc
    );
    if (selProj.visible) {
      const sx0 = ((selProj.ndcX + 1) * 0.5) * w;
      const sy0 = ((1 - selProj.ndcY) * 0.5) * h;

      render3DTargetStellarPhotosphere(
        ctx,
        sx0,
        sy0,
        selProj.distancePc,
        selectedFinding,
        bpRpToStarColorHex
      );

      const neighbors = compute3DNearestStellarNeighbors(selectedFinding, 3);
      ctx.save();
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.52)';
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1.1;
      ctx.font = '500 9.5px "JetBrains Mono", monospace';
      ctx.fillStyle = ALBEDIAN_COLORS.accentCatalog;

      for (const nb of neighbors) {
        const nbProj = projectWorldPointWithCamera(
          nb.cartesianParsec,
          effectiveObserverOriginParsec,
          activeCameraQuat,
          fovVerticalDeg,
          aspect,
          frustumShiftNdc
        );
        if (!nbProj.visible) continue;
        const nx = ((nbProj.ndcX + 1) * 0.5) * w;
        const ny = ((1 - nbProj.ndcY) * 0.5) * h;
        if (nx < 0 || nx > w || ny < 0 || ny > h) continue;

        ctx.beginPath();
        ctx.moveTo(sx0, sy0);
        ctx.lineTo(nx, ny);
        ctx.stroke();

        const mx = (sx0 + nx) * 0.5;
        const my = (sy0 + ny) * 0.5;
        ctx.fillText(`↔ ${nb.name} (${nb.distanceFromTargetPc} pc)`, mx + 6, my - 4);
      }
      ctx.restore();
    }
  }
}
