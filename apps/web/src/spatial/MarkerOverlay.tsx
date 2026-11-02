import React from 'react';
import { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { getMarkerVisualSpec } from '@albedian/design-system';

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
  suppressSelectedLabel?: boolean;
  solutionStatus: ObservationSession['observationSolution']['status'];
  activeOverlays: ObservationSession['spatialView']['activeOverlays'];
  containerRef: React.RefObject<HTMLDivElement | null>;
  onSelectFinding: (findingId: string, pointerEvent?: { xPx: number; yPx: number }) => void;
  onHoverFinding: (findingId: string | null) => void;
  onFocusFinding: (findingId: string | null) => void;
}

export const MarkerOverlay: React.FC<MarkerOverlayProps> = ({
  projectedMarkers,
  selectedFindingId,
  hoveredFindingId,
  focusedFindingId,
  isSpace3D,
  suppressSelectedLabel = false,
  solutionStatus,
  activeOverlays,
  containerRef,
  onSelectFinding,
  onHoverFinding,
  onFocusFinding,
}) => {
  const placedLabelBoxes: Array<{ x: number; y: number }> = [];
  const viewportWidth = containerRef.current?.clientWidth ?? 1440;
  const viewportHeight = containerRef.current?.clientHeight ?? 900;

  const selectedMarker = projectedMarkers.find(
    (m) => m.finding.id === selectedFindingId && m.visible
  );
  if (selectedMarker) {
    placedLabelBoxes.push({ x: selectedMarker.screenX, y: selectedMarker.screenY });
  }

  let ambientLabelsPlaced = 0;

  return (
    <>
      {projectedMarkers.map(({ finding, screenX, screenY, distancePc, visible }) => {
        if (!visible) return null;
        if (
          screenY < 76 ||
          screenY > viewportHeight - 84 ||
          screenX < 40 ||
          screenX > viewportWidth - 348
        ) {
          return null;
        }

        const isSelected = finding.id === selectedFindingId;
        const isHovered = finding.id === hoveredFindingId;
        const isFocused = finding.id === focusedFindingId;

        const isUncatalogedFieldBlob =
          finding.evidenceClass === 'observed' &&
          !finding.catalogRef &&
          !finding.id.includes('moon');

        if (isUncatalogedFieldBlob && !isSelected && !isHovered && !isFocused) {
          return null;
        }

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
          (ambientLabelsPlaced < 5 &&
            (finding.evidenceClass === 'catalog_matched' ||
              finding.id.includes('moon') ||
              (solutionStatus === 'approximate' && Boolean(finding.catalogRef))));

        const collidesWithExisting =
          !isSelected &&
          !isHovered &&
          !isFocused &&
          placedLabelBoxes.some(
            (b) => Math.abs(b.x - screenX) < 160 && Math.abs(b.y - screenY) < 46
          );

        const showLabel =
          candidateLabel && !collidesWithExisting && !(isSelected && suppressSelectedLabel);
        if (showLabel && !isSelected) {
          placedLabelBoxes.push({ x: screenX, y: screenY });
          ambientLabelsPlaced += 1;
        }

        const cleanName = finding.title.split('(')[0]?.trim() || finding.title;
        const subMeta = finding.inferredDistancePc
          ? `${Math.round(finding.inferredDistancePc.median * 3.26156).toLocaleString()} ly`
          : finding.id.includes('moon')
            ? '1.3 light-sec'
            : isSpace3D && distancePc > 0
              ? `${Math.round(distancePc * 3.26156)} ly`
              : '';

        const ringColor = isSelected ? '#7CC4DF' : 'rgba(245, 247, 250, 0.72)';

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
              width: 36,
              height: 36,
              transform: 'translate(-50%, -50%)',
              background: 'transparent',
              border: 'none',
              borderRadius: '50%',
              padding: 0,
              cursor: 'pointer',
              zIndex: isSelected ? 14 : 10,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <svg
              width={36}
              height={36}
              viewBox="-18 -18 36 36"
              style={{ overflow: 'visible' }}
            >
              {/* Delicate Target Reticle Ring (No Heavy Boxes) */}
              <circle
                cx={0}
                cy={0}
                r={isSelected ? 10 : 6}
                fill="none"
                stroke={ringColor}
                strokeWidth={isSelected ? 1.6 : 1.1}
                opacity={isSelected ? 1 : 0.75}
              />
              {isSelected && (
                <circle
                  cx={0}
                  cy={0}
                  r={15}
                  fill="none"
                  stroke="#7CC4DF"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                  opacity={0.65}
                />
              )}

              {/* Clean, Borderless Sky Typography */}
              {showLabel && (
                <text
                  x={isSelected ? 18 : 11}
                  y={4}
                  fill="#F5F7FA"
                  fontSize={isSelected ? 13 : 12}
                  fontWeight={isSelected ? 600 : 500}
                  fontFamily='"Inter", -apple-system, sans-serif'
                  style={{
                    filter: 'drop-shadow(0px 1px 4px rgba(0, 0, 0, 0.95))',
                  }}
                >
                  {cleanName}
                  {subMeta && (
                    <tspan fill="#94B8C8" fontWeight={400} fontSize={11}>
                      {' · '}
                      {subMeta}
                    </tspan>
                  )}
                </text>
              )}
            </svg>
          </button>
        );
      })}
    </>
  );
};
