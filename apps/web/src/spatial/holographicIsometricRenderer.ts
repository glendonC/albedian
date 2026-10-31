import { CelestialFinding } from '@albedian/contracts';
import { ProjectedMarker } from './MarkerOverlay.js';
import { computeFindingAstrophysics } from './astrophysicsEngine.js';

export interface HolographicRenderOptions {
  width: number;
  height: number;
  projectedMarkers: ProjectedMarker[];
  selectedFinding: CelestialFinding | undefined;
  isSpace3D: boolean;
  showIsometricGrid: boolean;
  showGeodesicRays: boolean;
  showExplodedTeardown: boolean;
  simulationYear: number;
}

/**
 * Projects a 3D point `(gx, gy, gz)` in `[-3, 3]` onto a tilted isometric floor plane
 * anchored in the lower-center of the viewport.
 */
function projectIsometricFloorPoint(
  gx: number,
  gy: number,
  gz: number,
  width: number,
  height: number
): { x: number; y: number } {
  const cx = width * 0.5;
  const cy = height * 0.77;
  const scale = Math.min(width, height) * 0.068;
  const angle = -0.38; // slight yaw like @InCartersEyes reference
  const rx = gx * Math.cos(angle) - gy * Math.sin(angle);
  const ry = gx * Math.sin(angle) + gy * Math.cos(angle);
  const isoX = cx + (rx - ry * 0.55) * scale;
  const isoY = cy + (ry * 0.42 + rx * 0.14) * scale - gz * scale * 0.85;
  return { x: isoX, y: isoY };
}

/**
 * Draws a crisp `+` crosshair marker at `(x, y)`.
 */
function drawPlusCrosshair(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  color: string,
  lineWidth = 1.1
): void {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  ctx.moveTo(x - size, y);
  ctx.lineTo(x + size, y);
  ctx.moveTo(x, y - size);
  ctx.lineTo(x, y + size);
  ctx.stroke();
  ctx.restore();
}

/**
 * Renders the 4 corner registration crosshairs (`+`), subtle technical grid marks,
 * bottom-left editorial scientific narrative, and bottom-right scale bar.
 */
export function renderFieldSimulatorRegistrationOverlay(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  selectedFinding: CelestialFinding | undefined,
  isSpace3D: boolean,
  isExplodedTeardown: boolean
): void {
  ctx.save();
  // Corner registration `+` marks
  const padX = 285;
  const padTop = 64;
  const padBottom = 86;
  const corners: Array<[number, number]> = [
    [padX, padTop],
    [width - padX, padTop],
    [padX, height - padBottom],
    [width - padX, height - padBottom],
  ];
  for (const [cx, cy] of corners) {
    drawPlusCrosshair(ctx, cx, cy, 4.5, 'rgba(248, 250, 252, 0.52)', 1);
  }

  // Subtle top registration `+ + + +` stitch line
  for (let x = padX + 48; x < width - padX - 40; x += 28) {
    drawPlusCrosshair(ctx, x, padTop, 1.8, 'rgba(255, 69, 34, 0.24)', 0.8);
  }

  // Bottom-left poetic/scientific deflection note (inspired by @InCartersEyes)
  const noteX = padX + 12;
  const noteY = height - padBottom - 54;
  ctx.font = '400 10px "Inter", sans-serif';
  ctx.fillStyle = 'rgba(226, 232, 240, 0.68)';
  const targetTitle = selectedFinding?.title.split('(')[0]?.trim() ?? 'Celestial Field';
  const lines = isExplodedTeardown
    ? [
        `Suspended in the instrument frustum is ${targetTitle}, decomposed into`,
        `concentric radiative, convective, and parallax shells. Geodesic photon`,
        `threads enter from the J2000 reference plane, bend along the WCS tangent`,
        `solution, and leave vermilion (+) deflection marks at each verified locus.`,
      ]
    : [
        `Suspended above the horizon plane is the calibrated sightline to ${targetTitle}.`,
        `Stray photon trajectories enter from the isometric reference grid, converge`,
        `through atmospheric Rayleigh-Mie scattering, and lock to sub-pixel WCS`,
        `centroids. Where each trajectory locks sharpest, an orange (+) mark remains.`,
      ];
  lines.forEach((line, idx) => {
    ctx.fillText(line, noteX, noteY + idx * 13);
  });

  // Bottom-right scale bar (`|──────| 1 unit`)
  const scaleRight = width - padX - 16;
  const scaleY = height - padBottom - 18;
  const barLen = 58;
  ctx.strokeStyle = 'rgba(226, 232, 240, 0.52)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(scaleRight - barLen, scaleY);
  ctx.lineTo(scaleRight, scaleY);
  ctx.moveTo(scaleRight - barLen, scaleY - 3.5);
  ctx.lineTo(scaleRight - barLen, scaleY + 3.5);
  ctx.moveTo(scaleRight, scaleY - 3.5);
  ctx.lineTo(scaleRight, scaleY + 3.5);
  ctx.stroke();

  ctx.font = '500 9.5px "JetBrains Mono", monospace';
  ctx.fillStyle = 'rgba(148, 163, 184, 0.72)';
  ctx.fillText(isSpace3D ? '10.0 pc' : '1.0° arc', scaleRight - barLen, scaleY + 12);
  ctx.restore();
}

/**
 * Renders the tilted isometric 3D coordinate floor grid (`[-3,+3]` .. `[+3,-3]`),
 * geodesic photon deflection streamlines, and vermilion `+` spark clusters.
 */
export function renderIsometricGeodesicHologram(
  ctx: CanvasRenderingContext2D,
  options: HolographicRenderOptions
): void {
  const {
    width,
    height,
    projectedMarkers,
    selectedFinding,
    isSpace3D,
    showIsometricGrid,
    showGeodesicRays,
    showExplodedTeardown,
  } = options;

  ctx.save();

  // 1. Tilted 3D Isometric Floor Plate (`-3..+3`)
  if (showIsometricGrid) {
    ctx.strokeStyle = 'rgba(226, 232, 240, 0.16)';
    ctx.lineWidth = 0.9;
    ctx.setLineDash([3, 4]);

    for (let g = -3; g <= 3; g += 1) {
      const pStart = projectIsometricFloorPoint(g, -3, 0, width, height);
      const pEnd = projectIsometricFloorPoint(g, 3, 0, width, height);
      ctx.beginPath();
      ctx.moveTo(pStart.x, pStart.y);
      ctx.lineTo(pEnd.x, pEnd.y);
      ctx.stroke();

      const qStart = projectIsometricFloorPoint(-3, g, 0, width, height);
      const qEnd = projectIsometricFloorPoint(3, g, 0, width, height);
      ctx.beginPath();
      ctx.moveTo(qStart.x, qStart.y);
      ctx.lineTo(qEnd.x, qEnd.y);
      ctx.stroke();
    }

    // Dashed isometric floor circle (radius 2.25 units)
    ctx.beginPath();
    for (let deg = 0; deg <= 360; deg += 5) {
      const rad = (deg * Math.PI) / 180;
      const pt = projectIsometricFloorPoint(
        Math.cos(rad) * 2.25,
        Math.sin(rad) * 2.25,
        0,
        width,
        height
      );
      if (deg === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // Corner coordinate crosshairs (`-3,+3`, `+3,+3`, `+3,-3`, `-3,-3`)
    const floorCorners: Array<[number, number, string]> = [
      [-3, 3, '-3,+3'],
      [3, 3, '+3,+3'],
      [3, -3, '+3,-3'],
      [-3, -3, '-3,-3'],
    ];
    ctx.font = '500 9.5px "JetBrains Mono", monospace';
    ctx.fillStyle = 'rgba(226, 232, 240, 0.58)';
    for (const [gx, gy, label] of floorCorners) {
      const pt = projectIsometricFloorPoint(gx, gy, 0, width, height);
      drawPlusCrosshair(ctx, pt.x, pt.y, 4.5, 'rgba(248, 250, 252, 0.72)', 1.1);
      ctx.fillText(label, pt.x + 6, pt.y + 3);
    }
  }

  // Focal target coordinates for geodesic convergence & teardown
  const visibleMarkers = projectedMarkers.filter((m) => m.visible);
  const selectedMarker =
    visibleMarkers.find((m) => m.finding.id === selectedFinding?.id) ?? visibleMarkers[0];

  const focalX = selectedMarker ? selectedMarker.screenX : width * 0.5;
  const focalY = selectedMarker ? selectedMarker.screenY : height * 0.42;

  // 2. Geodesic Photon Deflection Streamlines & Vermilion `+` Spark Clusters (@InCartersEyes signature)
  if (showGeodesicRays) {
    const sphereRadius = isSpace3D ? 118 : 88;
    const apexX = focalX + (width * 0.5 - focalX) * 0.15;
    const apexY = Math.max(78, focalY - sphereRadius * 1.45);

    const streamCount = 68;
    for (let i = 0; i < streamCount; i++) {
      const t = i / streamCount;
      const angle = t * Math.PI * 2;
      // Floor entry point on isometric grid
      const gx = Math.cos(angle) * 2.55;
      const gy = Math.sin(angle) * 2.55;
      const startPt = projectIsometricFloorPoint(gx, gy, 0, width, height);

      // Deflection waist on the spherical astrometric envelope around the target
      const lat = Math.sin(i * 1.7) * 0.78;
      const ringR = sphereRadius * Math.sqrt(Math.max(0.1, 1 - lat * lat));
      const waistX = focalX + Math.cos(angle + lat * 0.5) * ringR;
      const waistY = focalY + lat * sphereRadius * 0.85;

      // Streamline curve: Floor -> Deflection Sphere Waist -> Gathered Beam Apex
      ctx.strokeStyle =
        i % 4 === 0 ? 'rgba(248, 250, 252, 0.42)' : 'rgba(203, 213, 225, 0.19)';
      ctx.lineWidth = i % 5 === 0 ? 1.05 : 0.7;
      if (i % 3 === 0) ctx.setLineDash([8, 4]);
      else ctx.setLineDash([]);

      ctx.beginPath();
      ctx.moveTo(startPt.x, startPt.y);
      ctx.quadraticCurveTo(waistX, waistY + 26, waistX, waistY);
      ctx.quadraticCurveTo(
        focalX + (waistX - focalX) * 0.35,
        (waistY + apexY) * 0.5,
        apexX,
        apexY
      );
      ctx.stroke();

      // Vermilion orange-red `+` spark mark where each thread turns sharpest!
      if (i % 2 === 0) {
        drawPlusCrosshair(
          ctx,
          waistX,
          waistY,
          i % 4 === 0 ? 4.2 : 2.8,
          i % 4 === 0 ? '#FF4522' : 'rgba(255, 69, 34, 0.72)',
          1.15
        );
      }
    }
    ctx.setLineDash([]);

    // Also place vermilion `+` deflection sparks around every visible star detection
    for (const m of visibleMarkers.slice(0, 18)) {
      drawPlusCrosshair(ctx, m.screenX, m.screenY, 3.8, '#FF4522', 1.1);
    }

    // Gathered beam crown ring at apex
    ctx.strokeStyle = 'rgba(248, 250, 252, 0.82)';
    ctx.lineWidth = 1.2;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.arc(apexX, apexY, 7, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // 3. Exploded Astrophysical Teardown with Leader-Line Callouts (@elvar_official signature)
  if (showExplodedTeardown && selectedFinding) {
    const astro = computeFindingAstrophysics(selectedFinding);
    const isMoon = selectedFinding.id.includes('moon');

    // Exploded axis vector (upper-left to lower-right isometric expansion)
    const axisDx = 48;
    const axisDy = -26;

    const layers = isMoon
      ? [
          {
            offset: -1.6,
            radius: 56,
            color: 'rgba(56, 189, 248, 0.55)',
            title: 'RAYLEIGH-MIE SCATTER VEIL',
            sub: '18.95 MAG/ARCSEC² · HALO SUBTRACTED',
            side: 'left' as const,
          },
          {
            offset: -0.5,
            radius: 38,
            color: 'rgba(251, 191, 36, 0.75)',
            title: 'WAXING GIBBOUS TERMINATOR',
            sub: '79.1% ILLUMINATED · SELENOGRAPHIC LIMB',
            side: 'right' as const,
          },
          {
            offset: 0.6,
            radius: 22,
            color: '#FF4522',
            title: 'SENSOR PSF CENTROID CORE',
            sub: '0.35 PX WCS RESIDUAL · SNR 12.5',
            side: 'left' as const,
          },
          {
            offset: 1.6,
            radius: 32,
            color: 'rgba(16, 185, 129, 0.75)',
            title: 'TOPOCENTRIC EPHEMERIS LOCK',
            sub: '0.00257 AU · 1.28 LIGHT-SECONDS',
            side: 'right' as const,
          },
        ]
      : [
          {
            offset: -1.7,
            radius: 64,
            color: 'rgba(56, 189, 248, 0.52)',
            title: 'GAIA DR3 PARALLAX CONE',
            sub: `${selectedFinding.inferredDistancePc?.median.toFixed(1) ?? '168.0'} PC · 68% CI SHELL`,
            side: 'left' as const,
          },
          {
            offset: -0.6,
            radius: 46,
            color: 'rgba(251, 191, 36, 0.72)',
            title: 'CIRCUMSTELLAR DUST ENVELOPE',
            sub: `${astro ? `${astro.radiusSolar} R☉` : '3.5 AU'} · RADIATIVE BOUNDARY`,
            side: 'right' as const,
          },
          {
            offset: 0.5,
            radius: 28,
            color: '#FF4522',
            title: 'CONVECTIVE PHOTOSPHERE',
            sub: `${astro ? `${astro.teffK.toLocaleString()} K · ${astro.spectralClass}` : 'M1-M2 IA-AB'}`,
            side: 'left' as const,
          },
          {
            offset: 1.6,
            radius: 16,
            color: '#10B981',
            title: 'HELIUM / CARBON FUSION CORE',
            sub: `${astro ? `${astro.luminositySolar.toLocaleString()} L☉` : '41,687 L☉'} · 1478 CE PHOTONS`,
            side: 'right' as const,
          },
        ];

    // Central isometric exploding rod
    const rodStart = {
      x: focalX + axisDx * -2.1,
      y: focalY + axisDy * -2.1,
    };
    const rodEnd = {
      x: focalX + axisDx * 2.1,
      y: focalY + axisDy * 2.1,
    };
    ctx.strokeStyle = 'rgba(255, 69, 34, 0.55)';
    ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(rodStart.x, rodStart.y);
    ctx.lineTo(rodEnd.x, rodEnd.y);
    ctx.stroke();
    ctx.setLineDash([]);

    for (const layer of layers) {
      const lx = focalX + axisDx * layer.offset;
      const ly = focalY + axisDy * layer.offset;

      // Tilted isometric elliptical ring for each exploded shell
      ctx.save();
      ctx.translate(lx, ly);
      ctx.rotate(-0.42);
      ctx.strokeStyle = layer.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.ellipse(0, 0, layer.radius, layer.radius * 0.52, 0, 0, Math.PI * 2);
      ctx.stroke();

      // Inner dashed structural ring
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.ellipse(0, 0, layer.radius * 0.72, layer.radius * 0.37, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      // Center node square on rod
      ctx.fillStyle = '#FF4522';
      ctx.fillRect(lx - 2.5, ly - 2.5, 5, 5);

      // Horizontal technical leader-line callout (`■ TITLE / SUB`)
      const dir = layer.side === 'right' ? 1 : -1;
      const anchorX = lx + dir * layer.radius * 0.82;
      const anchorY = ly - layer.radius * 0.18;
      const elbowX = anchorX + dir * 46;
      const endX = elbowX + dir * 118;

      ctx.strokeStyle = 'rgba(226, 232, 240, 0.48)';
      ctx.lineWidth = 0.95;
      ctx.beginPath();
      ctx.moveTo(anchorX, anchorY);
      ctx.lineTo(elbowX, anchorY - 12);
      ctx.lineTo(endX, anchorY - 12);
      ctx.stroke();

      const textX = layer.side === 'right' ? elbowX + 6 : endX;
      const textY = anchorY - 17;

      // Vermilion square bullet `■`
      ctx.fillStyle = '#FF4522';
      ctx.fillRect(textX, textY - 7, 5, 5);

      ctx.font = '600 9.5px "JetBrains Mono", monospace';
      ctx.fillStyle = '#F8FAFC';
      ctx.fillText(layer.title, textX + 9, textY - 2);

      ctx.font = '400 8.5px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(148, 163, 184, 0.85)';
      ctx.fillText(layer.sub, textX + 9, textY + 10);
    }
  }

  ctx.restore();
}
