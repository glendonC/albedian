import React, { useState } from 'react';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import {
  projectWorldPointWithCamera,
  raDecToUnitVector,
  resolveFindingParsecPosition,
  type Vec3,
} from '@albedian/camera-rig';
import { getMarkerVisualSpec, type MarkerVisualSpec } from '@albedian/design-system';
import { linedUpProjection, shownAtFrame } from './poseProjection.js';
import { sourcePixelToViewport, useViewportSize } from './sourcePhotoRect.js';

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
  /** For a predicted finding lined up by a pose: its uncertainty ring's radius on screen, in CSS pixels. */
  ringPx?: number;
}

const hidden = (): ProjectedMarkerPosition => ({ visible: false, xPct: 0, yPct: 0, xPx: 0, yPx: 0 });

/**
 * Projects a `CelestialFinding` onto the viewport (`source_2d` pixel coordinates or
 * `registered_sky` / `catalog_3d` camera projection).
 *
 * Scientific honesty rules:
 * - In `source_2d`, a finding with a `sourceDetection` is placed on the pixel it was measured at. A predicted
 *   finding is placed only through a pose estimate (poseProjection.ts), with the predicted layer on, at the nominal
 *   WCS projection with its uncertainty ring; without a pose estimate nothing predicted or simulated is drawn over
 *   the photo (REQ-SKY-6.6). On a clip, each shows only while the clip is not playing and the session's clock,
 *   whose frame the stage shows, is within one frame (at least 0.05 s) of the frame it was measured or lined up on
 *   (P-SKY-9, poseProjection.ts shownAtFrame).
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
    const lined = det ? null : linedUpProjection(finding, session);
    const stored = det ?? lined;
    if (!stored) return hidden();
    const frameTimeSec = det ? det.frameTimeSec : session.observationSolution!.frameTimeSec;
    if (!shownAtFrame(session, frameTimeSec)) return hidden();
    // The same rectangle SourcePhotoStage draws the photo in: shape kept, letterboxed, then zoomed and panned
    const srcW = Math.max(1, session.sourceAsset?.width ?? safeW);
    const srcH = Math.max(1, session.sourceAsset?.height ?? safeH);
    const toViewport = (xPx: number, yPx: number) =>
      sourcePixelToViewport(
        xPx,
        yPx,
        srcW,
        srcH,
        safeW,
        safeH,
        session.cropTransform,
        session.sourceAsset?.displayOrientation
      );
    const p = toViewport(stored.xPx, stored.yPx);
    const visible = p.xPx >= 0 && p.xPx <= safeW && p.yPx >= 0 && p.yPx <= safeH;
    const position: ProjectedMarkerPosition = {
      visible,
      xPct: (p.xPx / safeW) * 100,
      yPct: (p.yPx / safeH) * 100,
      xPx: Math.round(p.xPx),
      yPx: Math.round(p.yPx),
    };
    if (lined) {
      // The ring's radius through the same mapping: a turn keeps lengths, so one offset point measures the scale
      const edge = toViewport(lined.xPx + lined.ringPx, lined.yPx);
      position.ringPx = Math.hypot(edge.xPx - p.xPx, edge.yPx - p.yPx);
    }
    return position;
  }

  const overlays = session.spatialView?.activeOverlays;
  if (finding.evidenceClass === 'observed' && overlays?.observedMarkers === false) {
    return hidden();
  }
  if (finding.evidenceClass === 'predicted' && overlays?.predictedEphemerides === false) {
    return hidden();
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

/**
 * How many names fit on screen without burying the photo: about 8 across a phone, 20 across a laptop.
 */
export function markerLabelBudget(viewportWidthPx: number): number {
  if (viewportWidthPx < 600) return 8;
  if (viewportWidthPx < 1100) return 14;
  return 20;
}

/** A proper name (Sirius, Hatsya), not only a catalog number (HIP 23685, Gaia DR3 ...). */
function hasProperName(finding: CelestialFinding): boolean {
  const ref = finding.catalogRef;
  return !!ref && ref.commonName !== ref.catalogSourceId;
}

/**
 * The findings that get a text label, up to the budget: predicted and simulated objects first (a
 * planet, a modeled object: few, and each names one thing), then catalog-matched stars with a
 * proper name, brightest catalog magnitude first. The selected and focused findings are labelled
 * whatever they are. Every other finding stays on screen and pickable, unlabelled.
 */
export function chooseLabelledFindingIds(
  findingsInView: readonly CelestialFinding[],
  viewportWidthPx: number,
  alwaysLabelledIds: ReadonlyArray<string | null>
): Set<string> {
  const objects = findingsInView.filter(
    (f) => f.evidenceClass === 'predicted' || f.evidenceClass === 'simulated'
  );
  const namedStars = findingsInView
    .filter((f) => f.evidenceClass === 'catalog_matched' && hasProperName(f))
    // A catalog that gives no magnitude ranks the finding after every one that gives one
    .sort((a, b) => (a.catalogRef!.apparentMagnitude ?? Infinity) - (b.catalogRef!.apparentMagnitude ?? Infinity));
  const ranked = [...objects, ...namedStars];
  const ids = new Set(ranked.slice(0, markerLabelBudget(viewportWidthPx)).map((f) => f.id));
  for (const id of alwaysLabelledIds) {
    if (id && findingsInView.some((f) => f.id === id)) ids.add(id);
  }
  return ids;
}

export const CelestialMarkerOverlay: React.FC<CelestialMarkerOverlayProps> = ({
  session,
  selectedFinding,
  onSelectFinding,
  viewportWidthPx,
  viewportHeightPx,
}) => {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  // The overlay covers the window, so the window's size is the frame markers are placed in
  const windowSize = useViewportSize();
  const viewW = viewportWidthPx ?? windowSize.widthPx;
  const viewH = viewportHeightPx ?? windowSize.heightPx;
  const findings = session.observationSolution?.findings ?? [];
  if (findings.length === 0) return null;

  const selectedId = selectedFinding?.id ?? session.selectedFindingId ?? null;
  const focusedId = session.focusedFindingId ?? null;

  const inView = findings
    .map((finding) => ({ finding, pos: projectFindingToViewport(finding, session, viewW, viewH) }))
    .filter(({ pos }) => pos.visible);
  const labelledIds = chooseLabelledFindingIds(
    inView.map(({ finding }) => finding),
    viewW,
    [selectedId, focusedId]
  );
  // Unlabelled markers first, so the labelled ones draw on top of them
  const ordered = [
    ...inView.filter(({ finding }) => !labelledIds.has(finding.id)),
    ...inView.filter(({ finding }) => labelledIds.has(finding.id)),
  ];

  return (
    <div
      className="alb-marker-overlay"
      data-testid="celestial-marker-overlay"
      aria-label="Celestial findings in view"
    >
      {/* Each lined-up prediction's uncertainty ring, faint and dashed, under every marker */}
      {ordered.map(({ finding, pos }) => {
        if (pos.ringPx === undefined) return null;
        const spec = getMarkerVisualSpec({
          evidenceClass: finding.evidenceClass,
          title: finding.title,
          isSelected: false,
          isFocused: false,
          isHovered: false,
        });
        const r = pos.ringPx;
        return (
          <svg
            key={`ring-${finding.id}`}
            className="alb-marker-ring"
            data-testid={`celestial-marker-ring-${finding.id}`}
            data-evidence-class={finding.evidenceClass}
            data-ring-radius-px={r.toFixed(2)}
            aria-hidden="true"
            width={2 * r + 2}
            height={2 * r + 2}
            viewBox={`${-r - 1} ${-r - 1} ${2 * r + 2} ${2 * r + 2}`}
            style={{
              position: 'absolute',
              left: `${pos.xPct.toFixed(2)}%`,
              top: `${pos.yPct.toFixed(2)}%`,
              transform: 'translate(-50%, -50%)',
              overflow: 'visible',
              pointerEvents: 'none',
            }}
          >
            <circle
              cx="0"
              cy="0"
              r={r}
              fill="none"
              stroke={spec.colorHex}
              strokeOpacity={0.45}
              strokeWidth="1"
              strokeDasharray="2 4"
            />
          </svg>
        );
      })}
      {ordered.map(({ finding, pos }) => {
        const isLabelled = labelledIds.has(finding.id);

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
            className={`alb-marker-hit ${isSelected ? 'is-selected' : ''} ${spec.showKeyboardFocusRing ? 'is-focused' : ''}${isLabelled ? '' : ' is-quiet'}`}
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
            <svg
              className="alb-marker-svg"
              viewBox="0 0 36 36"
              aria-hidden="true"
              style={isLabelled ? undefined : { width: '12px', height: '12px' }}
            >
              {renderMarkerGlyph(spec)}
            </svg>
            {isLabelled && (
              <span className="alb-marker-label">
                <span className="alb-marker-shortcode" style={{ color: spec.colorHex }}>
                  {spec.shortCode}
                </span>
                <span className="alb-marker-title">{finding.title}</span>
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
};
