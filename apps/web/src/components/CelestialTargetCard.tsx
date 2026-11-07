import React from 'react';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { getMarkerVisualSpec } from '@albedian/design-system';
import {
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
} from '@albedian/evidence';

export interface CelestialTargetCardProps {
  session: ObservationSession;
  selectedFinding: CelestialFinding | null;
  onRunUtterance: (
    utterance: string,
    modality?: 'voice' | 'text' | 'pointer'
  ) => void;
  onPushToTalk: () => void;
}

/**
 * Moment 05 Apple Maps Place Card inspector for the selected `CelestialFinding`.
 *
 * Scientific honesty rules:
 * - Always displays the authoritative `EvidenceClass` badge and disclosure from `@albedian/evidence`.
 * - Renders only quantities present on `selectedFinding`; absent catalog fields (`spectralType`,
 *   `colorIndexBpRp`, `properMotionRaMasYr`) stay omitted rather than guessed.
 * - Enables `Go there` only when `evidenceClass === 'catalog_matched'` and `canEnter3DSpace` is true.
 */
export const CelestialTargetCard: React.FC<CelestialTargetCardProps> = ({
  session,
  selectedFinding,
  onRunUtterance,
  onPushToTalk,
}) => {
  if (!selectedFinding) return null;

  const badge = getEpistemicBadgeDescriptor(selectedFinding.evidenceClass);
  const spec = getMarkerVisualSpec({
    evidenceClass: selectedFinding.evidenceClass,
    title: selectedFinding.title,
    isSelected: true,
    isFocused: false,
    isHovered: false,
  });
  const distInfo = formatDistanceWithUncertainty(selectedFinding.inferredDistancePc);
  const canEnterSpace =
    selectedFinding.evidenceClass === 'catalog_matched' && distInfo.canEnter3DSpace;
  const navState = session.machines?.navigation ?? 'source';
  const showBackToMoment = navState !== 'source';

  const det = selectedFinding.sourceDetection;
  const cat = selectedFinding.catalogRef;
  const limitations = selectedFinding.evidence?.limitations ?? [];

  return (
    <aside
      className="alb-target-card"
      data-hud-interactive="true"
      data-testid="celestial-target-card"
      aria-label={`Selected target: ${selectedFinding.title}`}
    >
      <div className="alb-target-card-header">
        <div
          className="alb-target-card-badge"
          data-testid="target-card-evidence-badge"
          data-evidence-class={selectedFinding.evidenceClass}
          style={{ borderColor: spec.colorHex, color: spec.colorHex }}
        >
          <span className="alb-target-card-shortcode">{badge.shortCode}</span>
          <span className="alb-target-card-badge-label">{badge.label}</span>
        </div>
        <h2 className="alb-target-card-title" data-testid="target-card-title">
          {selectedFinding.title}
        </h2>
        <div className="alb-target-card-subtitle" data-testid="target-card-subtitle">
          {selectedFinding.subtitle}
        </div>
        <p className="alb-target-card-disclosure" data-testid="target-card-disclosure">
          {badge.disclosure}
        </p>
      </div>

      <div className="alb-target-card-actions">
        <button
          type="button"
          className="alb-target-card-btn-primary"
          data-testid="target-card-go-there"
          disabled={!canEnterSpace}
          onClick={() => onRunUtterance(`Take me to ${selectedFinding.title}`, 'pointer')}
        >
          Go there
        </button>
        <button
          type="button"
          className="alb-target-card-btn-secondary"
          data-testid="target-card-ask"
          onClick={onPushToTalk}
        >
          Ask out loud
        </button>
        {showBackToMoment && (
          <button
            type="button"
            className="alb-target-card-btn-secondary"
            data-testid="target-card-back"
            onClick={() => onRunUtterance('Take me back', 'pointer')}
          >
            Back to moment
          </button>
        )}
      </div>

      <div className="alb-target-card-rows">
        {det && (
          <div className="alb-target-card-row" data-testid="target-card-pixel-row">
            <span className="alb-target-card-row-label">Measured in photo</span>
            <span className="alb-target-card-row-value">
              Centroid ({det.xPx.toFixed(1)}, {det.yPx.toFixed(1)}) px · SNR {det.snr.toFixed(1)} · FWHM {det.fwhmPx.toFixed(2)} px
              {selectedFinding.evidence.pixelResidualPx !== undefined
                ? ` · Held-out WCS residual ${selectedFinding.evidence.pixelResidualPx.toFixed(2)} px`
                : ''}
            </span>
          </div>
        )}

        <div className="alb-target-card-row" data-testid="target-card-distance-row">
          <span className="alb-target-card-row-label">Distance</span>
          <span className="alb-target-card-row-value">
            {distInfo.canEnter3DSpace
              ? `${distInfo.lightYearText} · ${distInfo.parsecText}`
              : 'Distance Unconstrained'}
          </span>
          <span className="alb-target-card-row-meta">{distInfo.uncertaintyText}</span>
        </div>

        {cat && (
          <div className="alb-target-card-row" data-testid="target-card-catalog-row">
            <span className="alb-target-card-row-label">
              {cat.provider} ({cat.releaseVersion}) · {cat.catalogSourceId}
            </span>
            <span className="alb-target-card-row-value">
              {cat.constellation} · Mag {cat.apparentMagnitude.toFixed(2)}
            </span>
            {cat.spectralType && (
              <span
                className="alb-target-card-row-meta"
                data-testid="target-card-spectral-type"
              >
                Spectral type: {cat.spectralType}
              </span>
            )}
            {cat.colorIndexBpRp !== undefined && (
              <span className="alb-target-card-row-meta" data-testid="target-card-bprp">
                BP-RP: {cat.colorIndexBpRp.toFixed(2)}
              </span>
            )}
            {cat.properMotionRaMasYr !== undefined &&
              cat.properMotionDecMasYr !== undefined && (
                <span
                  className="alb-target-card-row-meta"
                  data-testid="target-card-proper-motion"
                >
                  Proper motion: ({cat.properMotionRaMasYr.toFixed(2)},{' '}
                  {cat.properMotionDecMasYr.toFixed(2)}) mas/yr
                </span>
              )}
            <span className="alb-target-card-row-meta" data-testid="target-card-citation">
              {cat.citation}
            </span>
          </div>
        )}

        {limitations.length > 0 && (
          <div className="alb-target-card-row" data-testid="target-card-limitations">
            <span className="alb-target-card-row-label">Limits &amp; provenance</span>
            <ul className="alb-target-card-limits-list">
              {limitations.map((lim) => (
                <li key={lim}>{lim}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </aside>
  );
};
