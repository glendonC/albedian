import React from 'react';
import { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { ALBEDIAN_COLORS, getMarkerVisualSpec } from '@albedian/design-system';

export interface ProjectedMarker {
  finding: CelestialFinding;
  screenX: number;
  screenY: number;
  distancePc: number;
  visible: boolean;
}

export interface MarkerOverlayProps {
  projectedMarkers: ProjectedMarker[];
  selectedFindingId: string | null;
  hoveredFindingId: string | null;
  focusedFindingId: string | null;
  isSpace3D: boolean;
  solutionStatus: ObservationSession['observationSolution']['status'];
  activeOverlays: ObservationSession['spatialView']['activeOverlays'];
  containerRef: React.RefObject<HTMLDivElement | null>;
  onSelectFinding: (findingId: string, pointerEvent?: { xPx: number; yPx: number }) => void;
  onHoverFinding: (findingId: string | null) => void;
  onFocusFinding: (findingId: string | null) => void;
}

/**
 * Accessible SVG marker overlay encoding `EvidenceClass` (`observed`, `catalog_matched`,
 * `predicted`, `simulated`) via redundant color, stroke, and reticle shape cues.
 */
export const MarkerOverlay: React.FC<MarkerOverlayProps> = ({
  projectedMarkers,
  selectedFindingId,
  hoveredFindingId,
  focusedFindingId,
  isSpace3D,
  solutionStatus,
  activeOverlays,
  containerRef,
  onSelectFinding,
  onHoverFinding,
  onFocusFinding,
}) => {
  return (
    <>
      {projectedMarkers.map(({ finding, screenX, screenY, distancePc, visible }) => {
        if (!visible) return null;

        if (finding.evidenceClass === 'observed' && !activeOverlays.observedMarkers) {
          return null;
        }
        if (
          finding.evidenceClass === 'predicted' &&
          !activeOverlays.predictedEphemerides &&
          solutionStatus !== 'predicted_only'
        ) {
          return null;
        }

        const isSelected = finding.id === selectedFindingId;
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
          (activeOverlays.catalogMatchedLabels &&
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
                rect ? { xPx: e.clientX - rect.left, yPx: e.clientY - rect.top } : undefined
              );
            }}
            onMouseEnter={() => onHoverFinding(finding.id)}
            onMouseLeave={() => onHoverFinding(null)}
            onFocus={() => onFocusFinding(finding.id)}
            onBlur={() => onFocusFinding(null)}
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
                <circle cx={0} cy={0} r={isSelected ? 3.2 : 2.2} fill={spec.colorHex} />
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
    </>
  );
};
