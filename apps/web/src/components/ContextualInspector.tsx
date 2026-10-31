import React from 'react';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
} from '@albedian/evidence';

interface ContextualInspectorProps {
  session: ObservationSession;
  selectedFinding: CelestialFinding | null;
  sightlineBehindItems: Array<{
    finding: CelestialFinding;
    angularSeparationDeg: number;
    distanceFromObserverPc: number;
  }>;
  onSelectFinding: (findingId: string) => void;
  onEnterSpace: (findingId: string) => void;
  onOrbitViewpoint: (findingId: string) => void;
  onSetEpochYear: (yearCE: number) => void;
  onReturnToMoment: () => void;
  onOpenProcessingGraph: () => void;
}

/**
 * Progressive-disclosure Contextual Inspector (Section D3.3 of Spatial Experience Research).
 * Shows real astrometric measurements, Bailer-Jones distance intervals, epistemic provenance,
 * and spatial exploration controls without cluttering the viewport.
 */
export const ContextualInspector: React.FC<ContextualInspectorProps> = ({
  session,
  selectedFinding,
  sightlineBehindItems,
  onSelectFinding,
  onEnterSpace,
  onOrbitViewpoint,
  onSetEpochYear,
  onReturnToMoment,
  onOpenProcessingGraph,
}) => {
  const sol = session.observationSolution;
  const badge = selectedFinding
    ? getEpistemicBadgeDescriptor(selectedFinding.evidenceClass)
    : null;
  const distInfo = selectedFinding
    ? formatDistanceWithUncertainty(selectedFinding.inferredDistancePc)
    : null;

  return (
    <aside className="alb-inspector" aria-label="Observation & Scientific Inspector">
      {/* Section 1: Observation Calibration Summary */}
      <div
        style={{
          paddingBottom: 14,
          borderBottom: '1px solid var(--alb-border-subtle)',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span
            style={{
              fontSize: 11,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: 'var(--alb-text-muted)',
            }}
          >
            Observation Solution
          </span>
          <span
            className={`alb-badge alb-badge-${
              sol.status === 'calibrated' ? 'catalog_matched' : 'predicted'
            }`}
          >
            {sol.status === 'calibrated'
              ? 'WCS Calibrated'
              : sol.status === 'predicted_only'
                ? 'Predicted Sky Only'
                : 'Unresolved'}
          </span>
        </div>

        <div className="alb-tabular" style={{ fontSize: 12, color: 'var(--alb-text-secondary)' }}>
          {sol.status === 'calibrated' ? (
            <>
              {sol.quality.matchedInlierCount} Matches · {sol.quality.heldOutInliers} Held-Out
              Inliers · Residual {sol.quality.medianResidualPx.toFixed(2)} px
            </>
          ) : (
            <>
              {sol.quality.detectedCandidateCount} Detections · Direct Pixel Overlay Disabled
            </>
          )}
        </div>

        <div className="alb-tabular" style={{ fontSize: 11, color: 'var(--alb-text-muted)' }}>
          Catalog: {sol.solver.catalogRelease} · Epoch:{' '}
          {session.spatialView.simulationEpochISO}
        </div>
      </div>

      {/* Section 2: Selected Celestial Finding Inspector */}
      {selectedFinding && badge && distInfo ? (
        <div
          style={{
            paddingBottom: 14,
            borderBottom: '1px solid var(--alb-border-subtle)',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <div style={{ fontSize: 19, fontWeight: 600 }}>{selectedFinding.title}</div>
              <div style={{ fontSize: 12, color: 'var(--alb-text-secondary)', marginTop: 2 }}>
                {selectedFinding.subtitle}
              </div>
            </div>
            <span className={`alb-badge alb-badge-${selectedFinding.evidenceClass}`}>
              [{badge.shortCode}] {badge.label}
            </span>
          </div>

          <div
            className="alb-tabular"
            style={{
              padding: 10,
              backgroundColor: 'var(--alb-space-0)',
              border: '1px solid var(--alb-border-subtle)',
              borderRadius: 4,
              fontSize: 12,
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--alb-text-muted)' }}>ICRS (RA, Dec)</span>
              <span>
                {selectedFinding.skyPosition.raDeg.toFixed(4)}°,{' '}
                {selectedFinding.skyPosition.decDeg.toFixed(4)}°
              </span>
            </div>

            {selectedFinding.sourceDetection && (
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--alb-text-muted)' }}>Source Pixel (SNR)</span>
                <span>
                  ({selectedFinding.sourceDetection.xPx.toFixed(1)},{' '}
                  {selectedFinding.sourceDetection.yPx.toFixed(1)}) · SNR{' '}
                  {selectedFinding.sourceDetection.snr.toFixed(1)}
                </span>
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--alb-text-muted)' }}>Distance (Parsec)</span>
              <span style={{ color: 'var(--alb-accent-observation)' }}>
                {distInfo.parsecText}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--alb-text-muted)' }}>Light Travel</span>
              <span>{distInfo.lightYearText}</span>
            </div>

            <div style={{ fontSize: 11, color: 'var(--alb-text-muted)', marginTop: 2 }}>
              {distInfo.uncertaintyText}
            </div>
          </div>

          <div style={{ fontSize: 11, color: 'var(--alb-text-secondary)', lineHeight: 1.4 }}>
            {badge.disclosure}{' '}
            {selectedFinding.catalogRef ? `Source: ${selectedFinding.catalogRef.citation}.` : ''}
          </div>

          {/* Spatial Exploration Actions for Selected Object */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
            <button
              type="button"
              className="alb-btn alb-btn-primary"
              disabled={!distInfo.canEnter3DSpace}
              onClick={() => onEnterSpace(selectedFinding.id)}
              title={
                distInfo.canEnter3DSpace
                  ? 'Transition from recorded observation into catalog-backed 3D space'
                  : '3D travel requires a catalog-matched object with a validated distance estimate'
              }
            >
              Take Me There (Explore 3D) →
            </button>

            {session.machines.navigation === 'space' && (
              <button
                type="button"
                className="alb-btn"
                onClick={() => onOrbitViewpoint(selectedFinding.id)}
              >
                Alternate Viewpoint (Orbit)
              </button>
            )}
          </div>
        </div>
      ) : (
        <div
          style={{
            padding: 12,
            backgroundColor: 'var(--alb-space-0)',
            border: '1px solid var(--alb-border-subtle)',
            borderRadius: 4,
            fontSize: 12,
            color: 'var(--alb-text-secondary)',
          }}
        >
          Select any star marker in the viewport or list below to inspect its pixel measurements,
          WCS match residual, and Bailer-Jones distance interval.
        </div>
      )}

      {/* Section 3: Temporal & Sightline Controls */}
      <div
        style={{
          paddingBottom: 14,
          borderBottom: '1px solid var(--alb-border-subtle)',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--alb-text-secondary)' }}>
          Epoch & Spatial Perspective
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button
            type="button"
            className={`alb-btn ${
              session.spatialView.simulationEpochISO.startsWith('2026')
                ? 'alb-btn-active'
                : ''
            }`}
            onClick={() => onSetEpochYear(2026)}
          >
            Observation · 2026 CE
          </button>
          <button
            type="button"
            className={`alb-btn ${
              session.spatialView.simulationEpochISO.startsWith('1026')
                ? 'alb-btn-active'
                : ''
            }`}
            onClick={() => onSetEpochYear(1026)}
          >
            Modeled Sky · 1026 CE
          </button>
          {session.machines.navigation !== 'source' && (
            <button
              type="button"
              className="alb-btn alb-btn-primary"
              onClick={onReturnToMoment}
            >
              ← Back to Moment
            </button>
          )}
        </div>

        {/* Sightline Cone Results ("What's behind it?") */}
        {session.spatialView.activeOverlays.sightlineCone &&
          sightlineBehindItems.length > 0 && (
            <div
              className="alb-tabular"
              style={{
                marginTop: 6,
                padding: 8,
                backgroundColor: 'var(--alb-space-0)',
                border: '1px solid var(--alb-border-subtle)',
                borderRadius: 4,
                fontSize: 11,
              }}
            >
              <div style={{ color: 'var(--alb-accent-catalog)', marginBottom: 4 }}>
                Sightline Cone Depth Order (from Observer):
              </div>
              {sightlineBehindItems.slice(0, 5).map((item) => (
                <div
                  key={item.finding.id}
                  style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}
                >
                  <span>{item.finding.title}</span>
                  <span>
                    {item.distanceFromObserverPc.toFixed(1)} pc (Δθ {item.angularSeparationDeg.toFixed(1)}°)
                  </span>
                </div>
              ))}
            </div>
          )}
      </div>

      {/* Section 4: Accessible Findings Directory */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--alb-text-secondary)' }}>
            Scene Findings ({sol.findings.length})
          </span>
          <button
            type="button"
            className="alb-btn"
            style={{ fontSize: 11, minHeight: 26, padding: '2px 8px' }}
            onClick={onOpenProcessingGraph}
          >
            Processing Graph
          </button>
        </div>

        <div
          role="listbox"
          aria-label="Celestial Findings List"
          style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
        >
          {sol.findings.map((f) => {
            const fb = getEpistemicBadgeDescriptor(f.evidenceClass);
            const isSel = f.id === session.selectedFindingId;
            return (
              <button
                key={f.id}
                type="button"
                role="option"
                aria-selected={isSel}
                className={`alb-btn ${isSel ? 'alb-btn-active' : ''}`}
                style={{
                  justifyContent: 'space-between',
                  width: '100%',
                  textAlign: 'left',
                  padding: '6px 10px',
                }}
                onClick={() => onSelectFinding(f.id)}
              >
                <span style={{ fontSize: 12, fontWeight: isSel ? 600 : 400 }}>{f.title}</span>
                <span
                  className={`alb-badge alb-badge-${f.evidenceClass}`}
                  style={{ fontSize: 10 }}
                >
                  {fb.shortCode}
                  {f.inferredDistancePc ? ` · ${f.inferredDistancePc.median.toFixed(0)} pc` : ''}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </aside>
  );
};
