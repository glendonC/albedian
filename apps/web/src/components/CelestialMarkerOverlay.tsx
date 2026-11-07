import React, { useState } from 'react';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import {
  projectWorldPointWithCamera,
  raDecToUnitVector,
  resolveFindingParsecPosition,
  type Vec3,
} from '@albedian/camera-rig';
import { getMarkerVisualSpec, type MarkerVisualSpec } from '@albedian/design-system';

export interface CelestialMarkerOverlayProps {
  session: ObservationSession;
  selectedFinding: CelestialFinding | null;
  onSelectFinding: (
    findingId: string,
    pointerCoords?: { xPx: number; yPx: number }
  ) => void;
  viewportWidthPx?: number;
  viewportHeightPx?: number;
}

export interface ProjectedMarkerPosition {
  visible: boolean;
  xPct: number;
  yPct: number;
  xPx: number;
  yPx: number;
}

/**
 * Projects a `CelestialFinding` onto the viewport (`source_2d` pixel coordinates or
 * `registered_sky` / `catalog_3d` camera projection).
 *
 * Scientific honesty rules:
 * - In `source_2d`, only findings with an actual `sourceDetection` in camera pixels are placed;
 *   predicted or simulated findings without a pixel detection are never drawn over raw pixels.
 * - In sky/3D modes, `activeOverlays.observedMarkers` and `activeOverlays.predictedEphemerides`
 *   gate observed-only and predicted findings respectively.
 */
export function projectFindingToViewport(
  finding: CelestialFinding,
  session: ObservationSession,
  viewportWidthPx = 1440,
  viewportHeightPx = 900
): ProjectedMarkerPosition {
  const safeW = Math.max(1, viewportWidthPx);
  const safeH = Math.max(1, viewportHeightPx);
  const mode = session.spatialView?.mode ?? 'source_2d';

  if (mode === 'source_2d') {
    const det = finding.sourceDetection;
    if (!det) {
      return { visible: false, xPct: 0, yPct: 0, xPx: 0, yPx: 0 };
    }
    const srcW = Math.max(1, session.sourceAsset?.width ?? safeW);
    const srcH = Math.max(1, session.sourceAsset?.height ?? safeH);
    const u = det.xPx / srcW;
    const v = det.yPx / srcH;
    const zoom = session.cropTransform?.zoom ?? 1;
    const panX = session.cropTransform?.panX ?? 0;
    const panY = session.cropTransform?.panY ?? 0;
    const uZoomed = (u - 0.5) * zoom + 0.5 + panX;
    const vZoomed = (v - 0.5) * zoom + 0.5 + panY;
    const visible = uZoomed >= 0 && uZoomed <= 1 && vZoomed >= 0 && vZoomed <= 1;
    return {
      visible,
      xPct: uZoomed * 100,
      yPct: vZoomed * 100,
      xPx: Math.round(uZoomed * safeW),
      yPx: Math.round(vZoomed * safeH),
    };
  }

  const overlays = session.spatialView?.activeOverlays;
  if (finding.evidenceClass === 'observed' && overlays?.observedMarkers === false) {
    return { visible: false, xPct: 0, yPct: 0, xPx: 0, yPx: 0 };
  }
  if (finding.evidenceClass === 'predicted' && overlays?.predictedEphemerides === false) {
    return { visible: false, xPct: 0, yPct: 0, xPx: 0, yPx: 0 };
  }

  let worldPoint: Vec3 | null = resolveFindingParsecPosition(finding);
  if (!worldPoint && finding.skyPosition && mode !== 'catalog_3d') {
    const dir = raDecToUnitVector(finding.skyPosition.raDeg, finding.skyPosition.decDeg);
    worldPoint = [dir[0] * 1000, dir[1] * 1000, dir[2] * 1000];
  }
  if (!worldPoint) {
    return { visible: false, xPct: 0, yPct: 0, xPx: 0, yPx: 0 };
  }

  const proj = projectWorldPointWithCamera(
    worldPoint,
    session.spatialView.observerOriginParsec,
    session.spatialView.cameraQuaternion,
    session.spatialView.fovVerticalDeg,
    safeW / safeH,
    session.spatialView.frustumShiftNdc
  );
  if (!proj.visible || proj.ndcX < -1 || proj.ndcX > 1 || proj.ndcY < -1 || proj.ndcY > 1) {
    return { visible: false, xPct: 0, yPct: 0, xPx: 0, yPx: 0 };
  }

  const u = (proj.ndcX + 1) * 0.5;
  const v = (1 - proj.ndcY) * 0.5;
  return {
    visible: true,
    xPct: u * 100,
    yPct: v * 100,
    xPx: Math.round(u * safeW),
    yPx: Math.round(v * safeH),
  };
}

function renderMarkerGlyph(spec: MarkerVisualSpec): React.ReactNode {
  const r = spec.visibleRadiusPx;
  const stroke = spec.colorHex;
  const dash = spec.strokeDasharray === 'none' ? undefined : spec.strokeDasharray;

  switch (spec.shape) {
    case 'solid_dot_tick':
      return (
        <>
          <circle cx="18" cy="18" r={r + 3} fill="none" stroke={stroke} strokeWidth="1.5" />
          <circle cx="18" cy="18" r="2.4" fill={stroke} />
          <line x1="18" y1={18 - (r + 3)} x2="18" y2={18 - (r + 6.5)} stroke={stroke} strokeWidth="1.5" />
        </>
      );
    case 'concentric_ring_elbow':
      return (
        <>
          <circle cx="18" cy="18" r={r} fill="none" stroke={stroke} strokeWidth="1.6" />
          <circle cx="18" cy="18" r={Math.max(2, r - 3.5)} fill="none" stroke={stroke} strokeWidth="1.2" />
          {spec.showElbowConnector && (
            <polyline
              points={`24,12 29,7 34,7`}
              fill="none"
              stroke={stroke}
              strokeWidth="1.5"
            />
          )}
        </>
      );
    case 'dashed_circle':
      return (
        <circle
          cx="18"
          cy="18"
          r={r}
          fill="none"
          stroke={stroke}
          strokeWidth="1.6"
          strokeDasharray={dash}
        />
      );
    case 'bracket_reticle':
      return (
        <rect
          x={18 - r}
          y={18 - r}
          width={r * 2}
          height={r * 2}
          rx="2"
          fill="none"
          stroke={stroke}
          strokeWidth="1.5"
          strokeDasharray={dash}
        />
      );
  }
}

export const CelestialMarkerOverlay: React.FC<CelestialMarkerOverlayProps> = ({
  session,
  selectedFinding,
  onSelectFinding,
  viewportWidthPx = 1440,
  viewportHeightPx = 900,
}) => {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const findings = session.observationSolution?.findings ?? [];
  if (findings.length === 0) return null;

  const selectedId = selectedFinding?.id ?? session.selectedFindingId ?? null;
  const focusedId = session.focusedFindingId ?? null;

  return (
    <div
      className="alb-marker-overlay"
      data-testid="celestial-marker-overlay"
      aria-label="Celestial findings in view"
    >
      {findings.map((finding) => {
        const pos = projectFindingToViewport(
          finding,
          session,
          viewportWidthPx,
          viewportHeightPx
        );
        if (!pos.visible) return null;

        const isSelected = selectedId === finding.id;
        const isFocused = focusedId === finding.id;
        const isHovered = hoveredId === finding.id;
        const spec = getMarkerVisualSpec({
          evidenceClass: finding.evidenceClass,
          title: finding.title,
          isSelected,
          isFocused,
          isHovered,
        });

        return (
          <button
            key={finding.id}
            type="button"
            className={`alb-marker-hit ${isSelected ? 'is-selected' : ''} ${spec.showKeyboardFocusRing ? 'is-focused' : ''}`}
            data-hud-interactive="true"
            data-testid={`celestial-marker-${finding.id}`}
            data-evidence-class={finding.evidenceClass}
            data-marker-shape={spec.shape}
            aria-label={spec.ariaDescription}
            aria-pressed={isSelected}
            style={{
              left: `${pos.xPct.toFixed(2)}%`,
              top: `${pos.yPct.toFixed(2)}%`,
              width: `${spec.hitTargetSizePx}px`,
              height: `${spec.hitTargetSizePx}px`,
            }}
            onMouseEnter={() => setHoveredId(finding.id)}
            onMouseLeave={() => setHoveredId((prev) => (prev === finding.id ? null : prev))}
            onClick={() => onSelectFinding(finding.id, { xPx: pos.xPx, yPx: pos.yPx })}
          >
            <svg className="alb-marker-svg" viewBox="0 0 36 36" aria-hidden="true">
              {renderMarkerGlyph(spec)}
            </svg>
            <span className="alb-marker-label">
              <span className="alb-marker-shortcode" style={{ color: spec.colorHex }}>
                {spec.shortCode}
              </span>
              <span className="alb-marker-title">{finding.title}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
};
