import { ALBEDIAN_COLORS } from '@albedian/design-system';
import { projectWorldPointWithCamera, raDecToUnitVector } from '@albedian/camera-rig';
import { ALL_SKY_CONSTELLATION_EDGES, GAIA_BACKGROUND_STARFIELD_3D } from './allSkyStarfield.js';
import { ProjectedMarker } from './MarkerOverlay.js';

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
}

/**
 * Render a 2D source media variant pass (live synchronized `<video>`, high-res frame,
 * user-uploaded image, or synthetic reference scene).
 */
export function render2DMediaVariantPass(opts: Render2DMediaPassOptions): void {
  const {
    ctx,
    width: w,
    height: h,
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
    ctx.drawImage(chosenVideo, 0, 0, w, h);
    return;
  }

  if (realOrigImg) {
    const chosenImg =
      variantProfile === 'original'
        ? realOrigImg
        : variantProfile === 'restored_natural'
          ? realV1Img ?? realOrigImg
          : realV2Img ?? realV1Img ?? realOrigImg;
    ctx.drawImage(chosenImg, 0, 0, w, h);
    return;
  }

  if (uploadedImg) {
    ctx.save();
    if (variantProfile === 'original') {
      ctx.filter = 'brightness(0.75) contrast(1.05)';
    } else if (variantProfile === 'restored_natural') {
      ctx.filter = 'brightness(1.38) contrast(1.12)';
    } else {
      ctx.filter = 'brightness(1.55) contrast(1.18) saturate(1.08)';
    }
    ctx.drawImage(uploadedImg, 0, 0, w, h);
    ctx.restore();
    return;
  }

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
    if (!pm.visible || pm.finding.evidenceClass === 'predicted') continue;
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
}

/**
 * Render the 3D Gaia DR3 Parsec Starfield (`Phase B` / `at_space`) with blackbody colors,
 * deforming 3D constellation stick figures, and the Earth origin vector.
 */
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
  } = opts;

  const spaceGrad = ctx.createRadialGradient(w * 0.5, h * 0.5, 20, w * 0.5, h * 0.5, Math.max(w, h) * 0.82);
  spaceGrad.addColorStop(0, '#0D1724');
  spaceGrad.addColorStop(0.55, '#080E17');
  spaceGrad.addColorStop(1, '#04070B');
  ctx.fillStyle = spaceGrad;
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = 'rgba(129, 197, 204, 0.07)';
  ctx.lineWidth = 1;
  for (const r of [120, 230, 350, 480]) {
    ctx.beginPath();
    ctx.arc(w * 0.5, h * 0.5, r, 0, Math.PI * 2);
    ctx.stroke();
  }

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

    const distRatio = Math.max(0.15, Math.min(4.0, bgStar.distancePc / Math.max(1.0, proj.distancePc)));
    const effectiveMag = bgStar.mag - 2.5 * Math.log10(distRatio);
    const radius = Math.max(0.75, Math.min(4.8, 3.8 - effectiveMag * 0.52));
    const alpha = Math.max(0.16, Math.min(0.96, (6.5 - effectiveMag) * 0.18));

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
    const ex = earthProj.visible ? Math.max(48, Math.min(w - 180, ((earthProj.ndcX + 1) * 0.5) * w)) : 76;
    const ey = earthProj.visible ? Math.max(48, Math.min(h - 48, ((1 - earthProj.ndcY) * 0.5) * h)) : h - 64;

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
      `⊕ EARTH ORIGIN (${distFromEarthPc.toFixed(1)} pc · ${(distFromEarthPc * 3.26156).toFixed(1)} ly)`,
      Math.max(20, ex - 24),
      Math.min(h - 20, ey + 18)
    );
    ctx.restore();
  }
}
