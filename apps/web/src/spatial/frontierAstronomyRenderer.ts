import { CelestialFinding, WCSCalibrationModel } from '@albedian/contracts';
import { worldToPixelWCS } from '@albedian/camera-rig';
import { AspectFitRect } from './liveVideoTracker.js';

export interface DeepSkyRenderObject {
  id: string;
  catalogCode: string;
  name: string;
  kind: 'emission_nebula' | 'reflection_cluster' | 'planetary_nebula' | 'globular_cluster';
  raDeg: number;
  decDeg: number;
  angularRadiusDeg: number;
  distancePc: number;
  coreColor: string;
  outerColor: string;
}

export const FRONTIER_DEEP_SKY_OBJECTS: DeepSkyRenderObject[] = [
  {
    id: 'dso_m42',
    catalogCode: 'M42 · NGC 1976',
    name: 'Great Orion Nebula',
    kind: 'emission_nebula',
    raDeg: 83.8221,
    decDeg: -5.3911,
    angularRadiusDeg: 1.45,
    distancePc: 412,
    coreColor: 'rgba(244, 114, 182, 0.34)',
    outerColor: 'rgba(56, 189, 248, 0.14)',
  },
  {
    id: 'dso_barnard',
    catalogCode: 'Sh 2-276',
    name: "Barnard's Loop H-II Arc",
    kind: 'emission_nebula',
    raDeg: 85.8,
    decDeg: -2.2,
    angularRadiusDeg: 4.8,
    distancePc: 440,
    coreColor: 'rgba(251, 113, 133, 0.09)',
    outerColor: 'rgba(225, 29, 72, 0.04)',
  },
  {
    id: 'dso_m45',
    catalogCode: 'M45 · Mel 22',
    name: 'Pleiades (Seven Sisters)',
    kind: 'reflection_cluster',
    raDeg: 56.75,
    decDeg: 24.1167,
    angularRadiusDeg: 1.8,
    distancePc: 136.2,
    coreColor: 'rgba(96, 165, 250, 0.28)',
    outerColor: 'rgba(56, 189, 248, 0.09)',
  },
  {
    id: 'dso_hyades',
    catalogCode: 'Mel 25',
    name: 'Hyades Open Cluster',
    kind: 'reflection_cluster',
    raDeg: 66.75,
    decDeg: 15.87,
    angularRadiusDeg: 2.6,
    distancePc: 47.0,
    coreColor: 'rgba(251, 191, 36, 0.14)',
    outerColor: 'rgba(245, 158, 11, 0.04)',
  },
  {
    id: 'dso_ngc7293',
    catalogCode: 'NGC 7293 · C63',
    name: 'Helix Planetary Nebula',
    kind: 'planetary_nebula',
    raDeg: 337.4108,
    decDeg: -20.8372,
    angularRadiusDeg: 0.9,
    distancePc: 201,
    coreColor: 'rgba(56, 189, 248, 0.26)',
    outerColor: 'rgba(244, 114, 182, 0.12)',
  },
  {
    id: 'dso_m2',
    catalogCode: 'M2 · NGC 7089',
    name: 'Aquarius Globular Cluster',
    kind: 'globular_cluster',
    raDeg: 323.3625,
    decDeg: -0.8233,
    angularRadiusDeg: 0.75,
    distancePc: 11500,
    coreColor: 'rgba(253, 230, 138, 0.28)',
    outerColor: 'rgba(245, 158, 11, 0.08)',
  },
  {
    id: 'dso_m30',
    catalogCode: 'M30 · NGC 7099',
    name: 'Capricornus Core-Collapsed Cluster',
    kind: 'globular_cluster',
    raDeg: 325.0921,
    decDeg: -23.1799,
    angularRadiusDeg: 0.7,
    distancePc: 8400,
    coreColor: 'rgba(254, 240, 138, 0.26)',
    outerColor: 'rgba(56, 189, 248, 0.08)',
  },
];

/**
 * Renders curved J2000 Right Ascension / Declination equatorial graticule lines
 * and an azimuthal compass tape along the horizon silhouette.
 */
export function renderCurvedEquatorialGraticule(
  ctx: CanvasRenderingContext2D,
  wcs: WCSCalibrationModel,
  fitRect: AspectFitRect,
  srcWidth: number,
  srcHeight: number,
  maxSkyY: number,
  liveOffsetPx: [number, number]
): void {
  ctx.save();
  ctx.beginPath();
  ctx.rect(fitRect.x, fitRect.y, fitRect.width, Math.min(fitRect.height, maxSkyY * fitRect.scaleY));
  ctx.clip();

  ctx.strokeStyle = 'rgba(56, 189, 248, 0.085)';
  ctx.lineWidth = 0.85;
  ctx.font = '500 9.5px "JetBrains Mono", monospace';
  ctx.fillStyle = 'rgba(148, 163, 184, 0.46)';

  // Declination parallels (-30° to +40° every 10°)
  for (let dec = -30; dec <= 40; dec += 10) {
    ctx.beginPath();
    let started = false;
    let labelPt: { x: number; y: number } | null = null;
    for (let raStep = 0; raStep <= 360; raStep += 2) {
      const px = worldToPixelWCS(raStep, dec, wcs);
      if (!px.visible) {
        started = false;
        continue;
      }
      const u = px.uPx + liveOffsetPx[0];
      const v = px.vPx + liveOffsetPx[1];
      if (u < -80 || u > srcWidth + 80 || v < -80 || v > maxSkyY + 40) {
        started = false;
        continue;
      }
      const sx = fitRect.x + u * fitRect.scaleX;
      const sy = fitRect.y + v * fitRect.scaleY;
      if (!started) {
        ctx.moveTo(sx, sy);
        started = true;
      } else {
        ctx.lineTo(sx, sy);
      }
      if (!labelPt && u > 45 && u < srcWidth - 90 && v > 55 && v < maxSkyY - 35) {
        labelPt = { x: sx, y: sy };
      }
    }
    if (dec === 0) {
      ctx.save();
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.16)';
      ctx.setLineDash([6, 4]);
      ctx.stroke();
      ctx.restore();
    } else {
      ctx.stroke();
    }
    if (labelPt) {
      const decLabel = dec === 0 ? 'CEQ 0°' : `DEC ${dec > 0 ? `+${dec}` : dec}°`;
      ctx.fillText(decLabel, labelPt.x + 4, labelPt.y - 4);
    }
  }

  // Right Ascension meridians (every 15° = 1h)
  for (let ra = 0; ra < 360; ra += 15) {
    ctx.beginPath();
    let started = false;
    let topPt: { x: number; y: number } | null = null;
    for (let dec = -45; dec <= 55; dec += 2) {
      const px = worldToPixelWCS(ra, dec, wcs);
      if (!px.visible) {
        started = false;
        continue;
      }
      const u = px.uPx + liveOffsetPx[0];
      const v = px.vPx + liveOffsetPx[1];
      if (u < -80 || u > srcWidth + 80 || v < -80 || v > maxSkyY + 40) {
        started = false;
        continue;
      }
      const sx = fitRect.x + u * fitRect.scaleX;
      const sy = fitRect.y + v * fitRect.scaleY;
      if (!started) {
        ctx.moveTo(sx, sy);
        started = true;
      } else {
        ctx.lineTo(sx, sy);
      }
      if (!topPt && u > 60 && u < srcWidth - 60 && v > 62 && v < 180) {
        topPt = { x: sx, y: sy };
      }
    }
    ctx.stroke();
    if (topPt) {
      const hours = Math.round(ra / 15) % 24;
      ctx.fillText(`RA ${String(hours).padStart(2, '0')}h`, topPt.x + 4, topPt.y + 10);
    }
  }

  ctx.restore();
}

/**
 * Renders glowing multi-band emission/reflection nebulae & globular clusters
 * (Orion Nebula M42, Barnard's Loop, Pleiades M45, Helix NGC 7293, M2, M30).
 */
export function renderDeepSkyObjectsLayer(
  ctx: CanvasRenderingContext2D,
  wcs: WCSCalibrationModel,
  fitRect: AspectFitRect,
  srcWidth: number,
  maxSkyY: number,
  liveOffsetPx: [number, number]
): void {
  ctx.save();
  for (const dso of FRONTIER_DEEP_SKY_OBJECTS) {
    const px = worldToPixelWCS(dso.raDeg, dso.decDeg, wcs);
    if (!px.visible) continue;
    const u = px.uPx + liveOffsetPx[0];
    const v = px.vPx + liveOffsetPx[1];
    if (u < -60 || u > srcWidth + 60 || v < -40 || v > maxSkyY - 12) continue;

    const sx = fitRect.x + u * fitRect.scaleX;
    const sy = fitRect.y + v * fitRect.scaleY;
    const radiusPx = Math.max(
      14,
      (dso.angularRadiusDeg / Math.max(0.01, wcs.scaleDegPerPx)) * fitRect.scaleX
    );

    const grad = ctx.createRadialGradient(sx, sy, 1, sx, sy, radiusPx);
    grad.addColorStop(0, dso.coreColor);
    grad.addColorStop(0.45, dso.outerColor);
    grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(sx, sy, radiusPx, 0, Math.PI * 2);
    ctx.fill();

    // Subtle diamond DSO marker for primary catalog nebulae/clusters (skip giant Barnard's Loop label)
    if (dso.angularRadiusDeg < 3.0) {
      ctx.strokeStyle = 'rgba(167, 139, 250, 0.55)';
      ctx.lineWidth = 1;
      const d = 5.5;
      ctx.beginPath();
      ctx.moveTo(sx, sy - d);
      ctx.lineTo(sx + d, sy);
      ctx.lineTo(sx, sy + d);
      ctx.lineTo(sx - d, sy);
      ctx.closePath();
      ctx.stroke();

      ctx.font = '500 10px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(196, 181, 253, 0.78)';
      ctx.fillText(`◇ ${dso.catalogCode} · ${dso.name}`, sx + 9, sy + 3);
    }
  }
  ctx.restore();
}

/**
 * Renders an optical Point Spread Function (PSF) star with Planckian blackbody halo,
 * Airy core, and 4-point diffraction spikes for bright stars (mag <= 2.3).
 */
export function renderOpticalStarPSF(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  coreRadiusPx: number,
  colorHex: string,
  mag: number,
  isHighlighted = false
): void {
  const haloR = coreRadiusPx * (mag < 1.2 ? 7.5 : mag < 2.5 ? 5.2 : 3.6);
  const grad = ctx.createRadialGradient(x, y, 0.2, x, y, haloR);
  grad.addColorStop(0, '#FFFFFF');
  grad.addColorStop(0.18, colorHex);
  grad.addColorStop(0.48, `${colorHex}55`);
  grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(x, y, haloR, 0, Math.PI * 2);
  ctx.fill();

  // Saturated photospheric core
  ctx.fillStyle = '#FFFFFF';
  ctx.beginPath();
  ctx.arc(x, y, Math.max(1.1, coreRadiusPx * 0.72), 0, Math.PI * 2);
  ctx.fill();

  // 4-point optical diffraction spikes on bright stars
  if (mag <= 2.35 || isHighlighted) {
    const spikeLen = Math.min(34, Math.max(10, (2.8 - Math.min(2.4, mag)) * 12));
    ctx.save();
    ctx.strokeStyle = isHighlighted ? 'rgba(56, 189, 248, 0.65)' : `${colorHex}88`;
    ctx.lineWidth = mag < 1.0 ? 1.15 : 0.75;
    ctx.beginPath();
    ctx.moveTo(x - spikeLen, y);
    ctx.lineTo(x + spikeLen, y);
    ctx.moveTo(x, y - spikeLen);
    ctx.lineTo(x, y + spikeLen);
    ctx.stroke();
    ctx.restore();
  }
}

/**
 * Renders a physically shaded Waxing Gibbous Moon disc & Rayleigh/Mie scattering aureole.
 */
export function renderPhysicalLunarDisc(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  discRadiusPx = 11
): void {
  ctx.save();
  // Atmospheric scattering aureole
  const aureole = ctx.createRadialGradient(x, y, discRadiusPx * 0.5, x, y, discRadiusPx * 5.2);
  aureole.addColorStop(0, 'rgba(254, 243, 199, 0.48)');
  aureole.addColorStop(0.35, 'rgba(251, 191, 36, 0.16)');
  aureole.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = aureole;
  ctx.beginPath();
  ctx.arc(x, y, discRadiusPx * 5.2, 0, Math.PI * 2);
  ctx.fill();

  // Illuminated lunar limb
  const limbGrad = ctx.createRadialGradient(
    x - discRadiusPx * 0.25,
    y - discRadiusPx * 0.2,
    1,
    x,
    y,
    discRadiusPx
  );
  limbGrad.addColorStop(0, '#FFFBEB');
  limbGrad.addColorStop(0.7, '#FDE68A');
  limbGrad.addColorStop(1, '#D97706');
  ctx.fillStyle = limbGrad;
  ctx.beginPath();
  ctx.arc(x, y, discRadiusPx, 0, Math.PI * 2);
  ctx.fill();

  // Subtle lunar maria patches
  ctx.fillStyle = 'rgba(120, 113, 108, 0.28)';
  ctx.beginPath();
  ctx.arc(x - discRadiusPx * 0.22, y - discRadiusPx * 0.15, discRadiusPx * 0.28, 0, Math.PI * 2);
  ctx.arc(x + discRadiusPx * 0.18, y + discRadiusPx * 0.12, discRadiusPx * 0.22, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

/**
 * Renders a resolved 3D stellar photosphere, convective corona, and AU scale ring
 * when the observer arrives in the 3D parsec neighborhood of a selected star.
 */
export function render3DTargetStellarPhotosphere(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  distancePc: number,
  finding: CelestialFinding,
  bpRpToColorHex: (bpRp: number) => string
): void {
  if (distancePc > 135) return;
  const bpRp = finding.catalogRef?.colorIndexBpRp ?? 1.85;
  const starColor = bpRpToColorHex(bpRp);
  const proximityFactor = Math.max(0.25, Math.min(1.6, (130 - distancePc) / 48));
  const photosphereRadius = 16 * proximityFactor;

  ctx.save();
  // Extended circumstellar corona & dust envelope
  const corona = ctx.createRadialGradient(
    x,
    y,
    photosphereRadius * 0.4,
    x,
    y,
    photosphereRadius * 4.5
  );
  corona.addColorStop(0, '#FFFFFF');
  corona.addColorStop(0.22, starColor);
  corona.addColorStop(0.55, `${starColor}44`);
  corona.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = corona;
  ctx.beginPath();
  ctx.arc(x, y, photosphereRadius * 4.5, 0, Math.PI * 2);
  ctx.fill();

  // Resolved stellar photosphere disc
  const coreGrad = ctx.createRadialGradient(
    x - photosphereRadius * 0.22,
    y - photosphereRadius * 0.22,
    1,
    x,
    y,
    photosphereRadius
  );
  coreGrad.addColorStop(0, '#FFFFFF');
  coreGrad.addColorStop(0.62, starColor);
  coreGrad.addColorStop(1, 'rgba(234, 88, 12, 0.85)');
  ctx.fillStyle = coreGrad;
  ctx.beginPath();
  ctx.arc(x, y, photosphereRadius, 0, Math.PI * 2);
  ctx.fill();

  // Physical Circumstellar Scale Ring (e.g. ~3.5 AU / 512 R☉ envelope)
  const scaleRingR = photosphereRadius * 2.35;
  ctx.strokeStyle = 'rgba(251, 191, 36, 0.38)';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.arc(x, y, scaleRingR, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.font = '500 9.5px "JetBrains Mono", monospace';
  ctx.fillStyle = 'rgba(253, 230, 138, 0.85)';
  const specLabel = finding.catalogRef?.spectralType ?? 'Supergiant';
  ctx.fillText(
    `Photosphere Envelope · ${specLabel}`,
    x - scaleRingR * 0.65,
    y + scaleRingR + 13
  );
  ctx.restore();
}
