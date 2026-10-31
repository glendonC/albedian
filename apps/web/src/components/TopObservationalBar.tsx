import React from 'react';
import { ObservationSession } from '@albedian/contracts';
import { DiscoveredMediaItem } from '../services/apiClient.js';

export interface TopObservationalBarProps {
  session: ObservationSession;
  discoveredMedia: DiscoveredMediaItem[];
  activeDiscoveredId: string | null;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onSelectSaharaCalibrated: () => void;
  onSelectOvercastNegative: () => void;
  onSelectDiscoveredMedia: (item: DiscoveredMediaItem) => void;
  onFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onToggleCompareAB: () => void;
  onShowSky: () => void;
  onReturnToMoment: () => void;
  onToggleReducedMotion: () => void;
  onExportWithProvenance: () => void;
}

/**
 * Top Observational Header Bar (`Reveal`, `Register`, `Explore` scene switcher & provenance controls).
 */
export const TopObservationalBar: React.FC<TopObservationalBarProps> = ({
  session,
  discoveredMedia,
  activeDiscoveredId,
  fileInputRef,
  onSelectSaharaCalibrated,
  onSelectOvercastNegative,
  onSelectDiscoveredMedia,
  onFileUpload,
  onToggleCompareAB,
  onShowSky,
  onReturnToMoment,
  onToggleReducedMotion,
  onExportWithProvenance,
}) => {
  return (
    <header className="alb-topbar">
      <div className="alb-brand">
        <span className="alb-brand-title">ALBEDIAN</span>
        <span className="alb-brand-tagline">See (Beyond) the Night Sky.</span>
        <span
          className="alb-tabular"
          style={{
            fontSize: 12,
            color: 'var(--alb-accent-observation)',
            marginLeft: 10,
            paddingLeft: 10,
            borderLeft: '1px solid var(--alb-border-subtle)',
          }}
        >
          {session.title}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <button
          type="button"
          className={`alb-btn ${
            !activeDiscoveredId && session.observationSolution.status === 'calibrated'
              ? 'alb-btn-active'
              : ''
          }`}
          onClick={onSelectSaharaCalibrated}
        >
          Sahara Orion (Calibrated)
        </button>
        <button
          type="button"
          className={`alb-btn ${
            !activeDiscoveredId && session.observationSolution.status !== 'calibrated'
              ? 'alb-btn-active'
              : ''
          }`}
          onClick={onSelectOvercastNegative}
        >
          Overcast Negative (Predicted Only)
        </button>

        {discoveredMedia.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`alb-btn ${activeDiscoveredId === item.id ? 'alb-btn-active' : ''}`}
            onClick={() => onSelectDiscoveredMedia(item)}
          >
            {item.title}
          </button>
        ))}

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,video/*"
          style={{ display: 'none' }}
          onChange={onFileUpload}
        />
        <button
          type="button"
          className="alb-btn"
          onClick={() => fileInputRef.current?.click()}
        >
          Import Media
        </button>

        <button
          type="button"
          className={`alb-btn ${
            session.machines.navigation === 'comparison' ? 'alb-btn-active' : ''
          }`}
          onClick={onToggleCompareAB}
        >
          Compare A/B
        </button>

        <button
          type="button"
          className={`alb-btn ${
            session.machines.navigation === 'sky_registered' ? 'alb-btn-active' : ''
          }`}
          onClick={onShowSky}
        >
          {session.observationSolution.status === 'calibrated'
            ? 'Registered Sky'
            : 'Predicted Sky'}
        </button>

        {session.machines.navigation !== 'source' && (
          <button
            type="button"
            className="alb-btn alb-btn-primary"
            onClick={onReturnToMoment}
          >
            Back to Moment
          </button>
        )}

        <button
          type="button"
          className={`alb-btn ${session.reducedMotion ? 'alb-btn-active' : ''}`}
          onClick={onToggleReducedMotion}
        >
          {session.reducedMotion ? 'Reduced Motion: On' : 'Reduced Motion: Off'}
        </button>

        <button
          type="button"
          className="alb-btn alb-btn-primary"
          onClick={onExportWithProvenance}
        >
          Export
        </button>
      </div>
    </header>
  );
};
