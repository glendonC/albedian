import React from 'react';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  formatDistanceWithUncertainty,
} from '@albedian/evidence';
import { AstrophysicsAndRadiometryCard } from './AstrophysicsAndRadiometryCard.js';

export interface ContextualInspectorProps {
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
  showIsometricGrid?: boolean;
  showGeodesicRays?: boolean;
  showExplodedTeardown?: boolean;
  onToggleIsometricGrid?: () => void;
  onToggleGeodesicRays?: () => void;
  onToggleExplodedTeardown?: () => void;
  onSelectVariant?: (variantId: string) => void;
  onToggleCompare?: () => void;
  onStartGuidedTour?: () => void;
}

/**
 * Right Field-Simulator Telemetry Column (`04 ASTROMETRIC ATTITUDE`, `05 ASSEMBLIES`,
 * `06 DISPLAY & TEARDOWN`, `07 SUBSYSTEMS & H-R`) inspired by @elvar_official's Mellis HB-7
 * and @InCartersEyes's geodesic deflection UI.
 */
export const ContextualInspector: React.FC<ContextualInspectorProps> = ({
  session,
  selectedFinding,
  onSelectFinding,
  onEnterSpace,
  onOrbitViewpoint,
  onSetEpochYear,
  onReturnToMoment,
  showIsometricGrid = true,
  showGeodesicRays = true,
  showExplodedTeardown = false,
  onToggleIsometricGrid,
  onToggleGeodesicRays,
  onToggleExplodedTeardown,
  onSelectVariant,
  onToggleCompare,
  onStartGuidedTour,
}) => {
  const activeFinding = selectedFinding ?? session.observationSolution.findings[0] ?? null;
  const distInfo = activeFinding
    ? formatDistanceWithUncertainty(activeFinding.inferredDistancePc)
    : null;

  const raDeg = activeFinding?.skyPosition.raDeg ?? 88.79;
  const decDeg = activeFinding?.skyPosition.decDeg ?? 7.41;
  const altDeg = activeFinding?.skyPosition.altDeg ?? 46.8;
  const azDeg = activeFinding?.skyPosition.azDeg ?? 124.2;
  const isEpoch1026 = session.spatialView.simulationEpochISO.startsWith('1026');
  const isSpace3D = session.machines.navigation === 'space';

  return (
    <aside
      aria-label="Observation & Scientific Inspector"
      style={{
        position: 'absolute',
        top: 62,
        right: 14,
        bottom: 84,
        width: 284,
        zIndex: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        pointerEvents: 'auto',
        overflowY: 'auto',
      }}
    >
      {/* PANEL 04: ASTROMETRIC ATTITUDE & CIRCULAR HORIZON ROSE */}
      <section
        className="albedian-glass-panel"
        style={{
          padding: '10px 12px',
          borderRadius: 8,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 2, height: 10, background: '#FF4522', display: 'inline-block' }} />
            <span
              style={{
                fontSize: 9.5,
                fontWeight: 600,
                letterSpacing: '0.1em',
                fontFamily: '"JetBrains Mono", monospace',
                color: '#F8FAFC',
              }}
            >
              ASTROMETRY
            </span>
          </div>
          <span
            style={{
              fontSize: 9.5,
              fontFamily: '"JetBrains Mono", monospace',
              color: 'rgba(148, 163, 184, 0.7)',
            }}
          >
            04
          </span>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '84px 1fr',
            gap: 10,
            alignItems: 'center',
          }}
        >
          {/* Circular Attitude / Horizon Pitch-Roll Rose */}
          <svg width={82} height={82} viewBox="-42 -42 84 84">
            <circle
              cx={0}
              cy={0}
              r={36}
              fill="rgba(2, 4, 9, 0.55)"
              stroke="rgba(148, 163, 184, 0.28)"
              strokeWidth={1}
            />
            <circle
              cx={0}
              cy={0}
              r={26}
              fill="none"
              stroke="rgba(148, 163, 184, 0.16)"
              strokeDasharray="2 3"
            />
            <line x1={-36} y1={0} x2={36} y2={0} stroke="rgba(148, 163, 184, 0.25)" />
            <line x1={0} y1={-36} x2={0} y2={36} stroke="rgba(148, 163, 184, 0.25)" />
            {/* Rotated attitude bar */}
            <g transform={`rotate(${((azDeg % 45) - 22.5).toFixed(1)})`}>
              <line x1={-22} y1={0} x2={22} y2={0} stroke="#FF4522" strokeWidth={2} />
              <circle cx={0} cy={0} r={3} fill="#FF4522" />
            </g>
          </svg>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'auto 1fr',
              rowGap: 4,
              columnGap: 8,
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 9.5,
            }}
          >
            <span style={{ color: 'rgba(148, 163, 184, 0.72)' }}>TARGET</span>
            <span style={{ color: '#F8FAFC', fontWeight: 600, textAlign: 'right' }}>
              {activeFinding?.title.split('(')[0]?.trim().toUpperCase() ?? 'FIELD'}
            </span>
            <span style={{ color: 'rgba(148, 163, 184, 0.72)' }}>RA / DEC</span>
            <span style={{ color: '#F8FAFC', fontWeight: 600, textAlign: 'right' }}>
              {raDeg.toFixed(2)}° / {decDeg >= 0 ? `+${decDeg.toFixed(1)}` : decDeg.toFixed(1)}°
            </span>
            <span style={{ color: 'rgba(148, 163, 184, 0.72)' }}>ALT / AZ</span>
            <span style={{ color: '#F8FAFC', fontWeight: 600, textAlign: 'right' }}>
              +{altDeg.toFixed(1)}° / {azDeg.toFixed(1)}°
            </span>
            <span style={{ color: 'rgba(148, 163, 184, 0.72)' }}>DISTANCE</span>
            <span style={{ color: '#38BDF8', fontWeight: 600, textAlign: 'right' }}>
              {distInfo ? distInfo.primaryLabel.split('(')[0]?.trim() : '1.28 LT-SEC'}
            </span>
          </div>
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: 8,
            paddingTop: 6,
            borderTop: '1px solid rgba(148, 163, 184, 0.12)',
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: 8.5,
          }}
        >
          <span style={{ color: 'rgba(148, 163, 184, 0.75)' }}>
            HOLD <strong style={{ color: '#F8FAFC' }}>WCS LOCKED</strong>
          </span>
          <span style={{ color: 'rgba(148, 163, 184, 0.75)' }}>
            LIGHT FLIGHT{' '}
            <strong style={{ color: '#FF4522' }}>
              {distInfo?.secondaryLightYearsLabel.split('(')[0]?.trim() ?? '1.28 s'}
            </strong>
          </span>
        </div>
      </section>

      {/* PANEL 05: ASSEMBLIES & SPATIAL LAUNCH */}
      <section
        className="albedian-glass-panel"
        style={{
          padding: '10px 12px',
          borderRadius: 8,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 7,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 2, height: 10, background: '#FF4522', display: 'inline-block' }} />
            <span
              style={{
                fontSize: 9.5,
                fontWeight: 600,
                letterSpacing: '0.1em',
                fontFamily: '"JetBrains Mono", monospace',
                color: '#F8FAFC',
              }}
            >
              ASSEMBLIES
            </span>
          </div>
          <span
            style={{
              fontSize: 9.5,
              fontFamily: '"JetBrains Mono", monospace',
              color: 'rgba(148, 163, 184, 0.7)',
            }}
          >
            05
          </span>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          <button
            type="button"
            onClick={() => onSelectVariant?.('variant_original')}
            style={{
              background:
                session.activeVariantId === 'variant_original'
                  ? '#FF4522'
                  : 'rgba(15, 23, 38, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            Original Recording
          </button>
          <button
            type="button"
            onClick={() => onSelectVariant?.('variant_natural')}
            style={{
              background:
                session.activeVariantId === 'variant_natural'
                  ? '#FF4522'
                  : 'rgba(15, 23, 38, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            Natural Tone
          </button>
          <button
            type="button"
            onClick={() => onSelectVariant?.('variant_temporal')}
            style={{
              background:
                session.activeVariantId === 'variant_temporal'
                  ? '#FF4522'
                  : 'rgba(15, 23, 38, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            Motion-Compensated
          </button>
          <button
            type="button"
            onClick={onToggleCompare}
            style={{
              background:
                session.machines.navigation === 'comparison'
                  ? '#38BDF8'
                  : 'rgba(15, 23, 38, 0.65)',
              color: session.machines.navigation === 'comparison' ? '#020617' : '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            Compare A/B
          </button>
        </div>
      </section>

      {/* PANEL 06: DISPLAY, HOLOGRAM & EXPLODED TEARDOWN */}
      <section
        className="albedian-glass-panel"
        style={{
          padding: '10px 12px',
          borderRadius: 8,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 7,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 2, height: 10, background: '#FF4522', display: 'inline-block' }} />
            <span
              style={{
                fontSize: 9.5,
                fontWeight: 600,
                letterSpacing: '0.1em',
                fontFamily: '"JetBrains Mono", monospace',
                color: '#F8FAFC',
              }}
            >
              DISPLAY & HOLOGRAM
            </span>
          </div>
          <span
            style={{
              fontSize: 9.5,
              fontFamily: '"JetBrains Mono", monospace',
              color: 'rgba(148, 163, 184, 0.7)',
            }}
          >
            06
          </span>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          <button
            type="button"
            onClick={onToggleGeodesicRays}
            style={{
              background: showGeodesicRays ? '#FF4522' : 'rgba(15, 23, 38, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            GEODESIC RAYS
          </button>
          <button
            type="button"
            onClick={onToggleExplodedTeardown}
            style={{
              background: showExplodedTeardown ? '#FF4522' : 'rgba(15, 23, 38, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            EXPLODED
          </button>
          <button
            type="button"
            onClick={onToggleIsometricGrid}
            style={{
              background: showIsometricGrid ? '#FF4522' : 'rgba(15, 23, 38, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            ISOMETRIC GRID
          </button>
          <button
            type="button"
            onClick={() => activeFinding && onEnterSpace(activeFinding.id)}
            style={{
              background: isSpace3D ? '#10B981' : 'rgba(56, 189, 248, 0.18)',
              color: '#F8FAFC',
              border: '1px solid rgba(56, 189, 248, 0.45)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            Take Me There (Explore 3D) →
          </button>
          {isSpace3D && activeFinding && (
            <button
              type="button"
              onClick={() => onOrbitViewpoint(activeFinding.id)}
              style={{
                background: 'rgba(167, 139, 250, 0.2)',
                color: '#F8FAFC',
                border: '1px solid rgba(167, 139, 250, 0.48)',
                borderRadius: 3,
                padding: '3.5px 7px',
                fontSize: 8.5,
                fontWeight: 600,
                fontFamily: '"JetBrains Mono", monospace',
                cursor: 'pointer',
              }}
            >
              TURNTABLE ORBIT
            </button>
          )}
          <button
            type="button"
            onClick={() => onSetEpochYear(isEpoch1026 ? 2026 : 1026)}
            style={{
              background: isEpoch1026 ? '#A78BFA' : 'rgba(15, 23, 38, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.24)',
              borderRadius: 3,
              padding: '3.5px 7px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            {isEpoch1026 ? '1026 CE EPOCH' : '2026 CE'}
          </button>
          {session.machines.navigation !== 'source' && (
            <button
              type="button"
              onClick={onReturnToMoment}
              style={{
                background: 'rgba(255, 69, 34, 0.22)',
                color: '#FF4522',
                border: '1px solid #FF4522',
                borderRadius: 3,
                padding: '3.5px 7px',
                fontSize: 8.5,
                fontWeight: 600,
                fontFamily: '"JetBrains Mono", monospace',
                cursor: 'pointer',
              }}
            >
              Back to Moment
            </button>
          )}
          {onStartGuidedTour && (
            <button
              type="button"
              onClick={onStartGuidedTour}
              style={{
                background: 'rgba(16, 185, 129, 0.16)',
                color: '#10B981',
                border: '1px solid rgba(16, 185, 129, 0.42)',
                borderRadius: 3,
                padding: '3.5px 7px',
                fontSize: 8.5,
                fontWeight: 600,
                fontFamily: '"JetBrains Mono", monospace',
                cursor: 'pointer',
              }}
            >
              Guided Sky Tour ▶
            </button>
          )}
        </div>
      </section>

      {/* PANEL 07: STELLAR ASTROPHYSICS, 3D NEIGHBORS & H-R DIAGRAM */}
      <AstrophysicsAndRadiometryCard
        session={session}
        selectedFinding={activeFinding}
        onSelectFinding={onSelectFinding}
      />
    </aside>
  );
};
