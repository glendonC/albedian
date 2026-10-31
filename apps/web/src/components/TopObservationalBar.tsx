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
  isTourActive?: boolean;
  onToggleGuidedTour?: () => void;
}

/**
 * Field Simulator Top Strip inspired by @elvar_official's Mellis HB-7 header & NASA JPL consoles.
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
  onExportWithProvenance,
}) => {
  const sol = session.observationSolution;
  const isSahara = !activeDiscoveredId && sol.status === 'calibrated';
  const isOvercast = !activeDiscoveredId && sol.status !== 'calibrated';

  const statusText =
    sol.status === 'calibrated'
      ? 'WCS CALIBRATED'
      : sol.status === 'approximate'
        ? 'BEACON + HORIZON LOCK'
        : 'PREDICTED EPHEMERIS';

  return (
    <header
      className="albedian-glass-panel"
      style={{
        position: 'absolute',
        top: 10,
        left: 14,
        right: 14,
        height: 44,
        zIndex: 25,
        borderRadius: 8,
        padding: '0 12px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
      }}
    >
      {/* Left: Vermilion Badge + Editorial Title */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span
          style={{
            background: '#FF4522',
            color: '#FFFFFF',
            fontFamily: '"JetBrains Mono", monospace',
            fontWeight: 700,
            fontSize: 10,
            letterSpacing: '0.08em',
            padding: '4px 8px',
            borderRadius: 999,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
          }}
        >
          <span>✦</span> ALB-26
        </span>

        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span
              style={{
                fontFamily: '"Space Grotesk", sans-serif',
                fontWeight: 700,
                fontSize: 15,
                color: '#F8FAFC',
                letterSpacing: '-0.01em',
              }}
            >
              Albedian
            </span>
            <span
              style={{
                fontFamily: '"Space Grotesk", sans-serif',
                fontWeight: 400,
                fontSize: 14,
                color: '#FF4522',
              }}
            >
              /
            </span>
            <span
              style={{
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: 9.5,
                color: 'rgba(148, 163, 184, 0.85)',
                maxWidth: 220,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {session.title}
            </span>
          </div>
        </div>
      </div>

      {/* Center: Compact Scene Selector Pills */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <button
          type="button"
          onClick={onSelectSaharaCalibrated}
          style={{
            background: isSahara ? '#FF4522' : 'rgba(15, 23, 38, 0.65)',
            color: '#F8FAFC',
            border: `1px solid ${isSahara ? '#FF4522' : 'rgba(148, 163, 184, 0.22)'}`,
            borderRadius: 4,
            padding: '3.5px 8px',
            fontSize: 9,
            fontWeight: 600,
            fontFamily: '"JetBrains Mono", monospace',
            cursor: 'pointer',
          }}
        >
          Sahara Orion
        </button>

        {discoveredMedia.map((item) => {
          const active = activeDiscoveredId === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectDiscoveredMedia(item)}
              style={{
                background: active ? '#FF4522' : 'rgba(15, 23, 38, 0.65)',
                color: '#F8FAFC',
                border: `1px solid ${active ? '#FF4522' : 'rgba(148, 163, 184, 0.22)'}`,
                borderRadius: 4,
                padding: '3.5px 8px',
                fontSize: 9,
                fontWeight: 600,
                fontFamily: '"JetBrains Mono", monospace',
                cursor: 'pointer',
              }}
            >
              {item.title}
            </button>
          );
        })}

        <button
          type="button"
          onClick={onSelectOvercastNegative}
          style={{
            background: isOvercast ? '#FF4522' : 'rgba(15, 23, 38, 0.65)',
            color: '#F8FAFC',
            border: `1px solid ${isOvercast ? '#FF4522' : 'rgba(148, 163, 184, 0.22)'}`,
            borderRadius: 4,
            padding: '3.5px 8px',
            fontSize: 9,
            fontWeight: 600,
            fontFamily: '"JetBrains Mono", monospace',
            cursor: 'pointer',
          }}
        >
          Overcast Negative
        </button>
      </div>

      {/* Right: PSF Tick Gauge, Solver ID, Status Dot & Import/Export */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: 9,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ color: 'rgba(148, 163, 184, 0.7)', fontSize: 8 }}>PSF LOCK</span>
          <div style={{ display: 'flex', gap: 1.5 }}>
            {Array.from({ length: 14 }, (_, i) => (
              <span
                key={i}
                style={{
                  width: 2,
                  height: 8,
                  background: i < 12 ? '#F8FAFC' : 'rgba(148, 163, 184, 0.25)',
                }}
              />
            ))}
          </div>
          <span style={{ color: '#F8FAFC', fontWeight: 600 }}>99%</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: '#FF4522',
              boxShadow: '0 0 8px #FF4522',
            }}
          />
          <span style={{ color: '#F8FAFC', fontWeight: 600 }}>{statusText}</span>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,video/*"
          style={{ display: 'none' }}
          onChange={onFileUpload}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          style={{
            background: 'rgba(15, 23, 38, 0.72)',
            color: '#E2E8F0',
            border: '1px solid rgba(148, 163, 184, 0.25)',
            borderRadius: 4,
            padding: '3px 7px',
            fontSize: 8.5,
            fontFamily: '"JetBrains Mono", monospace',
            cursor: 'pointer',
          }}
        >
          Import Media
        </button>
        <button
          type="button"
          onClick={onExportWithProvenance}
          style={{
            background: 'rgba(56, 189, 248, 0.16)',
            color: '#38BDF8',
            border: '1px solid rgba(56, 189, 248, 0.42)',
            borderRadius: 4,
            padding: '3px 7px',
            fontSize: 8.5,
            fontFamily: '"JetBrains Mono", monospace',
            cursor: 'pointer',
          }}
        >
          Export
        </button>
      </div>
    </header>
  );
};
