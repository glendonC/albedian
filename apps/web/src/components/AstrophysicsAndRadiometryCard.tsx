import React from 'react';
import { CelestialFinding, ObservationSession } from '@albedian/contracts';
import {
  compute3DNearestStellarNeighbors,
  computeFindingAstrophysics,
} from '../spatial/astrophysicsEngine.js';

export interface AstrophysicsAndRadiometryCardProps {
  session: ObservationSession;
  selectedFinding: CelestialFinding | null;
  onSelectFinding?: (findingId: string) => void;
}

/**
 * Panel 07: Stellar Subsystems, 3D Nearest Neighbors & Interactive SVG Hertzsprung–Russell Diagram.
 */
export const AstrophysicsAndRadiometryCard: React.FC<AstrophysicsAndRadiometryCardProps> = ({
  session,
  selectedFinding,
  onSelectFinding,
}) => {
  const activeFinding = selectedFinding ?? session.observationSolution.findings[0] ?? null;
  const astro = activeFinding ? computeFindingAstrophysics(activeFinding) : null;
  const neighbors3D = activeFinding ? compute3DNearestStellarNeighbors(activeFinding, 3) : [];

  // Map (Teff, Luminosity) to SVG H-R diagram coordinates (x: 30,000K left -> 2,500K right; y: 10^6 top -> 10^-2 bottom)
  const teff = astro?.teffK ?? 5778;
  const lum = Math.max(0.01, astro?.luminositySolar ?? 1.0);
  const hrX = Math.max(
    14,
    Math.min(236, ((Math.log10(30000) - Math.log10(Math.max(2500, teff))) / (Math.log10(30000) - Math.log10(2500))) * 222 + 14)
  );
  const hrY = Math.max(
    10,
    Math.min(54, ((5.8 - Math.log10(lum)) / 7.5) * 44 + 10)
  );

  const subsystemRows = astro
    ? [
        {
          idx: '01',
          name: 'SURFACE TEFF',
          sub: `${astro.spectralClass} · BLACKBODY`,
          badge: `${astro.teffK.toLocaleString()} K`,
          color: '#FF4522',
        },
        {
          idx: '02',
          name: 'BOLOMETRIC LUM',
          sub: astro.evolutionaryStage.toUpperCase(),
          badge: `${astro.luminositySolar >= 100 ? Math.round(astro.luminositySolar).toLocaleString() : astro.luminositySolar} L☉`,
          color: '#10B981',
        },
        {
          idx: '03',
          name: 'STELLAR RADIUS',
          sub: 'STEFAN-BOLTZMANN',
          badge: `${astro.radiusSolar} R☉`,
          color: '#38BDF8',
        },
      ]
    : [
        {
          idx: '01',
          name: 'EPHEMERIS BODY',
          sub: 'SOLAR SYSTEM LOCK',
          badge: '1.28 LIGHT-SEC',
          color: '#FF4522',
        },
        {
          idx: '02',
          name: 'ILLUMINATION',
          sub: 'WAXING GIBBOUS',
          badge: '79.1% PHASE',
          color: '#10B981',
        },
      ];

  return (
    <section
      className="albedian-glass-panel"
      style={{
        padding: '10px 12px',
        borderRadius: 8,
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
            SUBSYSTEMS & H-R
          </span>
        </div>
        <span
          style={{
            fontSize: 9.5,
            fontFamily: '"JetBrains Mono", monospace',
            color: 'rgba(148, 163, 184, 0.7)',
          }}
        >
          07
        </span>
      </div>

      {/* Interactive Hertzsprung-Russell (H-R) Diagram Mini-Plot */}
      <div
        style={{
          background: 'rgba(2, 4, 9, 0.58)',
          border: '1px solid rgba(148, 163, 184, 0.14)',
          borderRadius: 4,
          padding: '4px 6px',
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 8,
            fontFamily: '"JetBrains Mono", monospace',
            color: 'rgba(148, 163, 184, 0.72)',
            marginBottom: 2,
          }}
        >
          <span>H-R DIAGRAM (TEFF ↔ L/L☉)</span>
          <span style={{ color: '#FF4522' }}>
            ● {activeFinding?.title.split('(')[0]?.trim().toUpperCase()}
          </span>
        </div>
        <svg width="100%" height={62} viewBox="0 0 250 62">
          {/* Grid lines */}
          <line x1={14} y1={14} x2={236} y2={14} stroke="rgba(148, 163, 184, 0.1)" strokeDasharray="2 2" />
          <line x1={14} y1={32} x2={236} y2={32} stroke="rgba(148, 163, 184, 0.1)" strokeDasharray="2 2" />
          <line x1={14} y1={50} x2={236} y2={50} stroke="rgba(148, 163, 184, 0.1)" strokeDasharray="2 2" />
          {/* Main Sequence S-curve */}
          <path
            d="M 22 10 Q 95 28, 145 38 T 228 56"
            fill="none"
            stroke="rgba(56, 189, 248, 0.35)"
            strokeWidth={3.5}
            strokeLinecap="round"
          />
          {/* Supergiant branch */}
          <path
            d="M 45 12 Q 135 11, 222 16"
            fill="none"
            stroke="rgba(251, 191, 36, 0.32)"
            strokeWidth={2}
            strokeDasharray="3 3"
          />
          {/* Selected Star Locus on H-R Diagram */}
          <circle cx={hrX} cy={hrY} r={6.5} fill="rgba(255, 69, 34, 0.25)" stroke="#FF4522" strokeWidth={1.2} />
          <circle cx={hrX} cy={hrY} r={2.5} fill="#FF4522" />
          <text x={16} y={59} fill="rgba(148,163,184,0.6)" fontSize={7.5} fontFamily='"JetBrains Mono", monospace'>
            30,000K (O/B)
          </text>
          <text x={188} y={59} fill="rgba(148,163,184,0.6)" fontSize={7.5} fontFamily='"JetBrains Mono", monospace'>
            3,000K (M)
          </text>
        </svg>
      </div>

      {/* Numbered Astrophysical & 3D Neighbor Subsystems (01-06) */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {subsystemRows.map((row) => (
          <div
            key={row.idx}
            style={{
              display: 'grid',
              gridTemplateColumns: '16px 1fr auto',
              alignItems: 'center',
              gap: 6,
              fontFamily: '"JetBrains Mono", monospace',
            }}
          >
            <span style={{ fontSize: 8.5, color: 'rgba(148, 163, 184, 0.65)' }}>{row.idx}</span>
            <div>
              <div style={{ fontSize: 9, fontWeight: 600, color: '#F8FAFC' }}>{row.name}</div>
              <div style={{ fontSize: 7.5, color: 'rgba(148, 163, 184, 0.68)' }}>{row.sub}</div>
            </div>
            <span
              style={{
                background: `${row.color}22`,
                color: row.color,
                border: `1px solid ${row.color}55`,
                borderRadius: 3,
                padding: '1.5px 5px',
                fontSize: 8,
                fontWeight: 600,
              }}
            >
              {row.badge}
            </span>
          </div>
        ))}

        {neighbors3D.map((nb, idx) => {
          const matchingFinding = session.observationSolution.findings.find((f) =>
            f.title.toLowerCase().includes(nb.name.toLowerCase())
          );
          const rowNum = String(subsystemRows.length + idx + 1).padStart(2, '0');
          return (
            <div
              key={nb.id}
              onClick={() => {
                if (matchingFinding && onSelectFinding) {
                  onSelectFinding(matchingFinding.id);
                }
              }}
              style={{
                display: 'grid',
                gridTemplateColumns: '16px 1fr auto',
                alignItems: 'center',
                gap: 6,
                fontFamily: '"JetBrains Mono", monospace',
                cursor: matchingFinding ? 'pointer' : 'default',
              }}
            >
              <span style={{ fontSize: 8.5, color: 'rgba(148, 163, 184, 0.65)' }}>{rowNum}</span>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, color: '#E2E8F0' }}>
                  ↔ {nb.name.toUpperCase()}
                </div>
                <div style={{ fontSize: 7.5, color: 'rgba(148, 163, 184, 0.68)' }}>
                  3D NEIGHBOR · {nb.constellation.toUpperCase()}
                </div>
              </div>
              <span
                style={{
                  background: 'rgba(16, 185, 129, 0.16)',
                  color: '#10B981',
                  border: '1px solid rgba(16, 185, 129, 0.42)',
                  borderRadius: 3,
                  padding: '1.5px 5px',
                  fontSize: 8,
                  fontWeight: 600,
                }}
              >
                {nb.distanceFromTargetPc} PC
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
};
