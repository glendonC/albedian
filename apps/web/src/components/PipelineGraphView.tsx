import React from 'react';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import type {
  ObserverKinematics,
  RadiometryAuditSummary,
} from '../spatial/astrophysicsEngine.js';

export interface PipelineGraphViewProps {
  session: ObservationSession;
  selectedFinding: CelestialFinding | null;
  kinematics: ObserverKinematics;
  radiometry: RadiometryAuditSummary;
  activeRegimeLabel: string;
}

export const PipelineGraphView: React.FC<PipelineGraphViewProps> = ({
  session,
  selectedFinding,
  kinematics,
  radiometry,
  activeRegimeLabel,
}) => {
  return (
    <section className="alb-node-modal">
      <svg
        aria-hidden="true"
        viewBox="0 0 1000 500"
        preserveAspectRatio="none"
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
          zIndex: 0,
        }}
      >
        <path
          d="M 410 155 C 455 155, 455 235, 500 235"
          fill="none"
          stroke="rgba(110, 231, 183, 0.55)"
          strokeWidth="2"
          strokeDasharray="5 4"
        />
        <path
          d="M 410 255 C 455 255, 455 255, 500 255"
          fill="none"
          stroke="rgba(96, 165, 250, 0.7)"
          strokeWidth="2.2"
        />
        <path
          d="M 410 355 C 455 355, 455 275, 500 275"
          fill="none"
          stroke="rgba(252, 211, 77, 0.55)"
          strokeWidth="2"
          strokeDasharray="5 4"
        />
        <circle cx="410" cy="155" r="4" fill="#6ee7b7" />
        <circle cx="410" cy="255" r="4" fill="#60a5fa" />
        <circle cx="410" cy="355" r="4" fill="#fcd34d" />
        <circle cx="500" cy="235" r="4" fill="#6ee7b7" />
        <circle cx="500" cy="255" r="4.5" fill="#60a5fa" />
        <circle cx="500" cy="275" r="4" fill="#fcd34d" />
      </svg>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, position: 'relative', zIndex: 1 }}>
        <div className="alb-node-card">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginBottom: 8,
              fontSize: 12,
            }}
          >
            <span style={{ color: '#b4b6bf', fontWeight: 500 }}>
              ⌄ Sensor & Glare Separation
            </span>
            <span
              className="alb-badge"
              style={{
                backgroundColor: 'rgba(16, 185, 129, 0.15)',
                color: '#6ee7b7',
              }}
            >
              Linear Radiance
            </span>
          </div>
          <div style={{ fontSize: 12, color: '#ffffff', fontWeight: 500 }}>
            {radiometry.lunarPhaseName} ({radiometry.illuminationPct}%) Mie Veil Subtraction
          </div>
          <div style={{ fontSize: 12, color: '#81838f', marginTop: 4 }}>
            {radiometry.sensorSummary}
          </div>
        </div>

        <div className="alb-node-card">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginBottom: 8,
              fontSize: 12,
            }}
          >
            <span style={{ color: '#b4b6bf', fontWeight: 500 }}>
              ⌄ Horizon + Ephemeris Sky Lock
            </span>
            <span
              className="alb-badge"
              style={{
                backgroundColor: 'rgba(59, 130, 246, 0.15)',
                color: '#93c5fd',
              }}
            >
              JPL DE440 + WCS
            </span>
          </div>
          <div style={{ fontSize: 12, color: '#ffffff', fontWeight: 500 }}>
            Origin: {kinematics.formattedCoordinates} ({kinematics.provenanceNote})
          </div>
          <div style={{ fontSize: 12, color: '#81838f', marginTop: 4 }}>
            Locks celestial frustum across {session.observationSolution.findings.length}{' '}
            co-registered loci even when single frames contain zero point-source stars.
          </div>
        </div>

        <div className="alb-node-card">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginBottom: 8,
              fontSize: 12,
            }}
          >
            <span style={{ color: '#b4b6bf', fontWeight: 500 }}>
              ⌄ SparkJS 3D Gaussian Splat Runtime
            </span>
            <span
              className="alb-badge"
              style={{
                backgroundColor: 'rgba(245, 158, 11, 0.15)',
                color: '#fcd34d',
              }}
            >
              @sparkjsdev/spark v2.4
            </span>
          </div>
          <div style={{ fontSize: 12, color: '#ffffff', fontWeight: 500 }}>
            Two-Pass Disk-Clipped Gaussian Splat Portals
          </div>
          <div style={{ fontSize: 12, color: '#81838f', marginTop: 4 }}>
            Connects Earth Capture, Cislunar Space, O’Neill et al. (2024) Local Bubble Chimney,
            and Deep-Sky Nebula splats in WebGL2 & Quest 3 WebXR.
          </div>
        </div>
      </div>

      <div
        className="alb-node-card"
        style={{
          position: 'relative',
          zIndex: 1,
          border: '1px solid rgba(96, 165, 250, 0.3)',
          padding: 24,
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <span style={{ color: '#93c5fd', fontWeight: 500 }}>
            ⌄ Observation Continuity Contract
          </span>
          <span style={{ color: '#81838f' }}>
            SHA-256 {session.sourceAsset.sha256.slice(0, 12)}…
          </span>
        </div>

        <div style={{ fontSize: 15, fontWeight: 600, color: '#ffffff' }}>
          Earth-to-Cosmos Continuity & Reversible Checkpoints
        </div>

        <p style={{ fontSize: 13, color: '#b4b6bf', lineHeight: 1.55, margin: 0 }}>
          Every finding and splat volume is computed dynamically from your capture metadata,
          ephemeris solver, and Gaia DR3 / Bailer-Jones priors without hardcoded star tables.
          Crossing any spatial portal saves an immutable CameraCheckpoint so returning restores
          your exact source frame and audio state.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 12 }}>
          <div
            style={{
              backgroundColor: '#181a20',
              padding: 12,
              borderRadius: 10,
              border: '1px solid rgba(255, 255, 255, 0.06)',
            }}
          >
            <div style={{ color: '#81838f' }}>Active Spatial Scale</div>
            <div style={{ color: '#ffffff', fontWeight: 500, marginTop: 4 }}>
              {activeRegimeLabel}
            </div>
          </div>
          <div
            style={{
              backgroundColor: '#181a20',
              padding: 12,
              borderRadius: 10,
              border: '1px solid rgba(255, 255, 255, 0.06)',
            }}
          >
            <div style={{ color: '#81838f' }}>Selected Celestial Target</div>
            <div style={{ color: '#ffffff', fontWeight: 500, marginTop: 4 }}>
              {selectedFinding?.title ?? 'None'}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
