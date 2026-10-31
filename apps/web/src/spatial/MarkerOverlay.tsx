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
 * `predicted`, `simulated`) via precision astrometric reticles and @elvar_official-style
 * technical leader-line callouts (`■ TITLE / TELEMETRY`).
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
  const placedLabelBoxes: Array<{ x: number; y: number }> = [];

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
          solutionStatus !== 'predicted_only' &&
          solutionStatus !== 'approximate'
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

        const candidateLabel =
          isSelected ||
          isHovered ||
          isFocused ||
          (activeOverlays.catalogMatchedLabels &&
            (finding.evidenceClass === 'catalog_matched' ||
              finding.evidenceClass === 'observed' ||
              (solutionStatus === 'approximate' && Boolean(finding.sourceDetection))));

        const collidesWithExisting =
          !isSelected &&
          !isHovered &&
          !isFocused &&
          placedLabelBoxes.some(
            (b) => Math.abs(b.x - screenX) < 165 && Math.abs(b.y - screenY) < 38
          );

        const showLabel = candidateLabel && !collidesWithExisting;
        if (showLabel) {
          placedLabelBoxes.push({ x: screenX, y: screenY });
        }

        const cleanName = finding.title.split('(')[0]?.trim().toUpperCase() || finding.title.toUpperCase();
        const subMeta = isSpace3D && distancePc > 0
          ? `${distancePc.toFixed(1)} PC · ${spec.shortCode}`
          : finding.inferredDistancePc
            ? `${finding.inferredDistancePc.median.toFixed(1)} PC · ${finding.catalogRef?.bayerDesignation?.toUpperCase() ?? spec.shortCode}`
            : finding.id.includes('moon')
              ? '1.28 LIGHT-SEC · OBS BEACON'
              : `${spec.shortCode} · ${finding.sourceDetection ? `SNR ${finding.sourceDetection.snr.toFixed(1)}` : 'EPHEMERIS'}`;

        const accentColor = isSelected
          ? '#FF4522'
          : finding.evidenceClass === 'observed'
            ? '#FF4522'
            : spec.colorHex;

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
              zIndex: isSelected ? 12 : 9,
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
              {/* Outer precision reticle ring */}
              <circle
                cx={0}
                cy={0}
                r={isSelected ? spec.visibleRadiusPx + 3 : spec.visibleRadiusPx}
                fill="none"
                stroke={accentColor}
                strokeWidth={isSelected ? 1.6 : 1.0}
                strokeDasharray={isSelected ? undefined : spec.strokeDasharray}
                opacity={isSelected ? 0.95 : 0.72}
              />

              {/* Selected target corner bracket ticks */}
              {isSelected && (
                <g stroke="#FF4522" strokeWidth={1.3} fill="none">
                  <path d="M -13 -7 L -13 -13 L -7 -13" />
                  <path d="M 7 -13 L 13 -13 L 13 -7" />
                  <path d="M -13 7 L -13 13 L -7 13" />
                  <path d="M 7 13 L 13 13 L 13 7" />
                </g>
              )}

              {/* Crosshair ticks */}
              <g stroke={accentColor} strokeWidth={1.0} opacity={0.85}>
                <line x1={0} y1={-spec.visibleRadiusPx - 4} x2={0} y2={-spec.visibleRadiusPx - 1} />
                <line x1={0} y1={spec.visibleRadiusPx + 1} x2={0} y2={spec.visibleRadiusPx + 4} />
                <line x1={-spec.visibleRadiusPx - 4} y1={0} x2={-spec.visibleRadiusPx - 1} y2={0} />
                <line x1={spec.visibleRadiusPx + 1} y1={0} x2={spec.visibleRadiusPx + 4} y2={0} />
              </g>

              {showLabel && (
                <g>
                  {/* Technical leader line with horizontal shelf */}
                  <polyline
                    points={`${spec.visibleRadiusPx * 0.75},${-spec.visibleRadiusPx * 0.75} ${
                      spec.visibleRadiusPx + 10
                    },${-spec.visibleRadiusPx - 9} ${spec.visibleRadiusPx + 36},${
                      -spec.visibleRadiusPx - 9
                    }`}
                    fill="none"
                    stroke={accentColor}
                    strokeWidth={0.95}
                    opacity={0.78}
                  />
                  {/* Square indicator bullet (`■`) */}
                  <rect
                    x={spec.visibleRadiusPx + 14}
                    y={-spec.visibleRadiusPx - 20}
                    width={4.5}
                    height={4.5}
                    fill={isSelected ? '#FF4522' : accentColor}
                  />
                  {/* Primary uppercase title */}
                  <text
                    x={spec.visibleRadiusPx + 22}
                    y={-spec.visibleRadiusPx - 15}
                    fill="#F8FAFC"
                    fontSize={10}
                    fontWeight={600}
                    letterSpacing="0.05em"
                    fontFamily='"JetBrains Mono", monospace'
                  >
                    {cleanName}
                  </text>
                  {/* Sub-telemetry readout */}
                  <text
                    x={spec.visibleRadiusPx + 22}
                    y={-spec.visibleRadiusPx - 4}
                    fill="rgba(148, 163, 184, 0.88)"
                    fontSize={8.5}
                    fontWeight={500}
                    letterSpacing="0.03em"
                    fontFamily='"JetBrains Mono", monospace'
                  >
                    {subMeta}
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
