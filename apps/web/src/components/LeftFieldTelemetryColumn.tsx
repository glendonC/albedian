import React from 'react';
import { ObservationSession } from '@albedian/contracts';
import { computeObservationRadiometryAudit } from '../spatial/astrophysicsEngine.js';

export interface LeftFieldTelemetryColumnProps {
  session: ObservationSession;
  onOpenGraphModal: () => void;
  onSelectFinding: (findingId: string) => void;
}

/**
 * Left Field-Simulator Telemetry Column (`01 PHOTON FLUX`, `02 RADIOMETRY`, `03 SUBSYSTEMS`)
 * inspired by @elvar_official's Mellis HB-7 Field Simulator & NASA JPL instrumentation.
 */
export const LeftFieldTelemetryColumn: React.FC<LeftFieldTelemetryColumnProps> = ({
  session,
  onOpenGraphModal,
  onSelectFinding,
}) => {
  const selectedFinding =
    session.observationSolution.findings.find((f) => f.id === session.selectedFindingId) ??
    session.observationSolution.findings[0];

  const snr = selectedFinding?.sourceDetection?.snr ?? 84.2;
  const residualPx =
    selectedFinding?.evidence.pixelResidualPx ?? session.observationSolution.quality.medianResidualPx;
  const radiometry = computeObservationRadiometryAudit(session);

  // Generate sinusoidal + scintillation SVG path for Panel 01
  const wavePoints: string[] = [];
  const freq = session.activeVariantId === 'variant_original' ? 6.5 : 3.8;
  const amp = session.activeVariantId === 'variant_original' ? 14 : 9.5;
  for (let x = 0; x <= 220; x += 3) {
    const t = x / 220;
    const y =
      22 +
      Math.sin(t * Math.PI * 2 * freq + session.currentSourceTimeSec * 2.5) *
        amp *
        (0.65 + 0.35 * Math.cos(t * Math.PI * 3));
    wavePoints.push(`${x === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`);
  }

  const skyDarknessRatio = Math.max(
    0.25,
    Math.min(1, (radiometry.skyBrightnessMagArcsec2 - 16.5) / 5.5)
  );
  const filledBars = Math.round(skyDarknessRatio * 14);

  const subsystems = [
    {
      code: 'L1',
      name: 'DECODE_ROOT',
      load: '100%',
      pct: 100,
      status: 'LOCKED',
      color: '#10B981',
    },
    {
      code: 'L2',
      name: 'SILHOUETTE_MASK',
      load: '96%',
      pct: 96,
      status: 'PROTECTED',
      color: '#10B981',
    },
    {
      code: 'L3',
      name: 'LUNAR_VEIL_SUB',
      load: radiometry.illuminationPct > 50 ? '88%' : '94%',
      pct: radiometry.illuminationPct > 50 ? 88 : 94,
      status: 'ACTIVE',
      color: '#FF4522',
    },
    {
      code: 'L4',
      name: 'STAR_PSF_GUARD',
      load: '99%',
      pct: 99,
      status: '99% PSF',
      color: '#10B981',
    },
    {
      code: 'L5',
      name: 'COHERENT_STACK',
      load: session.activeVariantId === 'variant_temporal' ? '95%' : '78%',
      pct: session.activeVariantId === 'variant_temporal' ? 95 : 78,
      status: session.activeVariantId === 'variant_temporal' ? '9-FRAME' : 'SINGLE',
      color: session.activeVariantId === 'variant_temporal' ? '#10B981' : '#38BDF8',
    },
    {
      code: 'L6',
      name: 'WCS_SOLVER',
      load:
        session.observationSolution.status === 'calibrated'
          ? '98%'
          : session.observationSolution.status === 'approximate'
            ? '82%'
            : '40%',
      pct:
        session.observationSolution.status === 'calibrated'
          ? 98
          : session.observationSolution.status === 'approximate'
            ? 82
            : 40,
      status:
        session.observationSolution.status === 'calibrated'
          ? 'VERIFIED'
          : session.observationSolution.status === 'approximate'
            ? 'BEACON'
            : 'PREDICT',
      color:
        session.observationSolution.status === 'calibrated'
          ? '#10B981'
          : '#FF4522',
    },
  ];

  return (
    <aside
      aria-label="Field Simulator Left Telemetry"
      style={{
        position: 'absolute',
        top: 62,
        left: 14,
        bottom: 84,
        width: 256,
        zIndex: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        pointerEvents: 'auto',
      }}
    >
      {/* PANEL 01: PHOTON FLUX & SCINTILLATION WAVEFORM */}
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
            marginBottom: 6,
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
              PHOTON FLUX
            </span>
          </div>
          <span
            style={{
              fontSize: 9.5,
              fontFamily: '"JetBrains Mono", monospace',
              color: 'rgba(148, 163, 184, 0.7)',
            }}
          >
            01
          </span>
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            marginBottom: 4,
          }}
        >
          <div>
            <div
              style={{
                fontSize: 22,
                fontWeight: 700,
                fontFamily: '"Space Grotesk", sans-serif',
                color: '#F8FAFC',
                lineHeight: 1.05,
              }}
            >
              {snr.toFixed(1)}{' '}
              <span style={{ fontSize: 11, fontWeight: 500, color: '#94A3B8' }}>SNR</span>
            </div>
            <div
              style={{
                fontSize: 8.5,
                fontFamily: '"JetBrains Mono", monospace',
                color: 'rgba(148, 163, 184, 0.72)',
              }}
            >
              APERTURE SIGNAL
            </div>
          </div>

          <div style={{ textAlign: 'right' }}>
            <div
              style={{
                fontSize: 16,
                fontWeight: 600,
                fontFamily: '"Space Grotesk", sans-serif',
                color: '#FF4522',
                lineHeight: 1.1,
              }}
            >
              ±{residualPx.toFixed(2)} px
            </div>
            <div
              style={{
                fontSize: 8.5,
                fontFamily: '"JetBrains Mono", monospace',
                color: 'rgba(148, 163, 184, 0.72)',
              }}
            >
              WCS RESIDUAL
            </div>
          </div>
        </div>

        <svg
          width="100%"
          height={44}
          viewBox="0 0 220 44"
          style={{
            background: 'rgba(2, 4, 9, 0.55)',
            borderRadius: 4,
            border: '1px solid rgba(148, 163, 184, 0.12)',
          }}
        >
          <line x1={0} y1={22} x2={220} y2={22} stroke="rgba(148, 163, 184, 0.18)" strokeDasharray="2 2" />
          <line x1={55} y1={0} x2={55} y2={44} stroke="rgba(148, 163, 184, 0.1)" />
          <line x1={110} y1={0} x2={110} y2={44} stroke="rgba(148, 163, 184, 0.1)" />
          <line x1={165} y1={0} x2={165} y2={44} stroke="rgba(148, 163, 184, 0.1)" />
          <path d={wavePoints.join(' ')} fill="none" stroke="#FF4522" strokeWidth={1.35} />
        </svg>

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginTop: 5,
            fontSize: 8.5,
            fontFamily: '"JetBrains Mono", monospace',
            color: 'rgba(148, 163, 184, 0.75)',
          }}
        >
          <span>RATE 1.0× · 30 FPS</span>
          <span>SHUTTER 1/30 · 48 KHZ</span>
        </div>
      </section>

      {/* PANEL 02: RADIOMETRY & LUNAR SKY FLOOR */}
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
            marginBottom: 6,
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
              RADIOMETRY
            </span>
          </div>
          <span
            style={{
              fontSize: 9.5,
              fontFamily: '"JetBrains Mono", monospace',
              color: 'rgba(148, 163, 184, 0.7)',
            }}
          >
            02
          </span>
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 8,
          }}
        >
          <div>
            <div
              style={{
                fontSize: 20,
                fontWeight: 700,
                fontFamily: '"Space Grotesk", sans-serif',
                color: '#F8FAFC',
                lineHeight: 1.05,
              }}
            >
              {radiometry.skyBrightnessMagArcsec2.toFixed(2)}
            </div>
            <div
              style={{
                fontSize: 8.5,
                fontFamily: '"JetBrains Mono", monospace',
                color: 'rgba(148, 163, 184, 0.72)',
              }}
            >
              MAG / ARCSEC²
            </div>
          </div>

          {/* 14-segment LED block bar */}
          <div style={{ display: 'flex', gap: 2.5, alignItems: 'center' }}>
            {Array.from({ length: 14 }, (_, i) => (
              <span
                key={i}
                style={{
                  width: 4.5,
                  height: 14,
                  borderRadius: 1,
                  background:
                    i < filledBars
                      ? '#10B981'
                      : 'rgba(148, 163, 184, 0.18)',
                }}
              />
            ))}
          </div>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 6,
            paddingTop: 6,
            borderTop: '1px solid rgba(148, 163, 184, 0.12)',
            fontFamily: '"JetBrains Mono", monospace',
          }}
        >
          <div>
            <div style={{ fontSize: 8, color: 'rgba(148, 163, 184, 0.68)' }}>MOON PHASE</div>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: '#F8FAFC' }}>
              {radiometry.illuminationPct.toFixed(1)}%
            </div>
          </div>
          <div>
            <div style={{ fontSize: 8, color: 'rgba(148, 163, 184, 0.68)' }}>1F LIMIT</div>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: '#F8FAFC' }}>
              +{radiometry.singleFrameLimitMag.toFixed(2)}m
            </div>
          </div>
          <div>
            <div style={{ fontSize: 8, color: 'rgba(148, 163, 184, 0.68)' }}>9F STACK</div>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: '#10B981' }}>
              +{radiometry.coherentStackLimitMag.toFixed(2)}m
            </div>
          </div>
        </div>
      </section>

      {/* PANEL 03: PIPELINE SUBSYSTEMS & SCENE TARGETS */}
      <section
        className="albedian-glass-panel"
        style={{
          padding: '10px 12px',
          borderRadius: 8,
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          overflowY: 'auto',
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
              DAG SUBSYSTEMS
            </span>
          </div>
          <button
            type="button"
            onClick={onOpenGraphModal}
            style={{
              background: 'rgba(255, 69, 34, 0.16)',
              color: '#FF4522',
              border: '1px solid rgba(255, 69, 34, 0.42)',
              borderRadius: 3,
              padding: '1px 6px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            Processing Graph
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {subsystems.map((sub) => (
            <div
              key={sub.code}
              style={{
                display: 'grid',
                gridTemplateColumns: '18px 1fr 48px',
                alignItems: 'center',
                gap: 6,
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: 9,
              }}
            >
              <span style={{ color: 'rgba(148, 163, 184, 0.8)' }}>{sub.code}</span>
              <div>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 8.5,
                    marginBottom: 2,
                  }}
                >
                  <span style={{ color: '#E2E8F0' }}>{sub.name}</span>
                  <span style={{ color: 'rgba(148, 163, 184, 0.75)' }}>{sub.load}</span>
                </div>
                <div
                  style={{
                    height: 3,
                    background: 'rgba(148, 163, 184, 0.16)',
                    borderRadius: 2,
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      width: `${sub.pct}%`,
                      height: '100%',
                      background: sub.color,
                    }}
                  />
                </div>
              </div>
              <span
                style={{
                  background: `${sub.color}22`,
                  color: sub.color,
                  border: `1px solid ${sub.color}55`,
                  borderRadius: 2,
                  padding: '1px 3px',
                  fontSize: 7.5,
                  fontWeight: 600,
                  textAlign: 'center',
                }}
              >
                {sub.status}
              </span>
            </div>
          ))}
        </div>

        {/* Quick Target Lock List */}
        <div
          style={{
            marginTop: 10,
            paddingTop: 8,
            borderTop: '1px solid rgba(148, 163, 184, 0.14)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          <div
            style={{
              fontSize: 8.5,
              fontFamily: '"JetBrains Mono", monospace',
              color: 'rgba(148, 163, 184, 0.72)',
              letterSpacing: '0.08em',
            }}
          >
            PRIMARY LOCUS TARGETS ({session.observationSolution.findings.length})
          </div>
          {session.observationSolution.findings.slice(0, 4).map((f) => {
            const active = f.id === session.selectedFindingId;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => onSelectFinding(f.id)}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  background: active ? 'rgba(255, 69, 34, 0.18)' : 'rgba(15, 23, 38, 0.45)',
                  border: `1px solid ${active ? '#FF4522' : 'rgba(148, 163, 184, 0.14)'}`,
                  borderRadius: 4,
                  padding: '4px 6px',
                  color: '#F8FAFC',
                  fontSize: 9.5,
                  fontFamily: '"JetBrains Mono", monospace',
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {f.title.split('(')[0]?.trim()}
                </span>
                <span style={{ color: active ? '#FF4522' : '#38BDF8', fontSize: 8.5 }}>
                  {f.inferredDistancePc ? `${f.inferredDistancePc.median.toFixed(0)} pc` : 'LOCK'}
                </span>
              </button>
            );
          })}
        </div>
      </section>
    </aside>
  );
};
