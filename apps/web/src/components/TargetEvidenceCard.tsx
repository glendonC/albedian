import React from 'react';
import type { CelestialFinding } from '@albedian/contracts';
import type { StellarAstrophysicsSummary } from '../spatial/astrophysicsEngine.js';

export interface TargetEvidenceCardProps {
  finding: CelestialFinding;
  photonContext: {
    lightTravelText: string;
    departureEpochLabel: string;
    historicalMilestone: string;
  };
  stellarPhysics: StellarAstrophysicsSummary | null;
  portalActionLabel: string;
  onTriggerPortal: () => void;
}

export const TargetEvidenceCard: React.FC<TargetEvidenceCardProps> = ({
  finding,
  photonContext,
  stellarPhysics,
  portalActionLabel,
  onTriggerPortal,
}) => {
  const shortTitle = finding.title.split('(')[0]?.trim() || finding.title;

  return (
    <aside className="alb-glass-pill alb-right-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span
          className="alb-badge"
          style={{
            backgroundColor: 'rgba(245, 158, 11, 0.18)',
            color: '#fcd34d',
            whiteSpace: 'nowrap',
          }}
        >
          {finding.evidenceClass === 'observed' ? 'Observed Sensor Lock' : 'Catalog + WCS Verified'}
        </span>
        <span style={{ fontSize: 12, color: '#9ca3af', whiteSpace: 'nowrap' }}>
          RA {finding.skyPosition.raDeg.toFixed(1)}° · Dec{' '}
          {finding.skyPosition.decDeg.toFixed(1)}°
        </span>
      </div>

      <div>
        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#ffffff', margin: 0 }}>
          {shortTitle}
        </h2>
        <p style={{ fontSize: 12, color: '#9ca3af', marginTop: 3, marginBottom: 0 }}>
          {stellarPhysics
            ? `${stellarPhysics.spectralClass} · ${stellarPhysics.evolutionaryStage}`
            : finding.subtitle}
        </p>
      </div>

      <div
        style={{
          padding: 12,
          borderRadius: 12,
          backgroundColor: 'rgba(255, 255, 255, 0.04)',
          border: '1px solid rgba(255, 255, 255, 0.06)',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 12, color: '#9ca3af' }}>Light-Travel</span>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#ffffff' }}>
            {photonContext.lightTravelText}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 12, color: '#9ca3af' }}>Photon Departure</span>
          <span style={{ fontSize: 12, fontWeight: 500, color: '#93c5fd' }}>
            {photonContext.departureEpochLabel}
          </span>
        </div>

        <p style={{ fontSize: 12, color: '#9ca3af', margin: '4px 0 0 0', lineHeight: 1.45 }}>
          {stellarPhysics
            ? stellarPhysics.systemArchitectureNote
            : photonContext.historicalMilestone}
        </p>
      </div>

      <button type="button" className="alb-primary-btn" onClick={onTriggerPortal}>
        {portalActionLabel}
      </button>
    </aside>
  );
};
