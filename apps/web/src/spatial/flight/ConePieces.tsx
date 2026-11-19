import React from 'react';
import { EvidenceTag } from '../../look/index.js';
import { coneReadout, type ConeGeometry } from './coneView.js';

/**
 * D28 (decided 19 Nov, UIUX-cone-READY.md): the way into your photo's view and its readout. The pill sits right of
 * D26's craft pill, 10 px apart; pressed it fills (ink background, black text). The readout takes the There screen's
 * right column: the Simulated tag, "Your photo's view" at 52 px, two numbers, and the nearest star's line at 13 px.
 */

const INK = 'var(--look-ink, #f4f3ef)';

/** The bottom left row: the craft pill (none in the cone view) and the pill into your photo's view. */
export const FlightPills: React.FC<{ craftPill: React.ReactNode; cone: boolean; onCone: () => void }> = ({ craftPill, cone, onCone }) => (
  <div className="flight-pills" data-testid="flight-pills" data-hud-interactive="true" style={{ position: 'absolute', left: 36, bottom: 32, display: 'flex', alignItems: 'flex-end', gap: 10, pointerEvents: 'auto' }}>
    {!cone && craftPill}
    <button
      type="button"
      className="look-second look-size-compact"
      data-testid="cone-pill"
      aria-pressed={cone}
      onClick={onCone}
      style={cone ? { background: INK, color: '#000', borderColor: INK } : undefined}
    >
      Your photo&apos;s view
    </button>
  </div>
);

export const ConeReadout: React.FC<{ geometry: ConeGeometry }> = ({ geometry }) => {
  const r = coneReadout(geometry);
  return (
    <section className="flight-readout" data-testid="cone-readout" aria-label="Your photo's view">
      <EvidenceTag evidenceClass="simulated" />
      <h1 style={{ fontSize: 52 }}>Your photo&apos;s view</h1>
      <div className="flight-readout__stats">
        <div className="flight-readout__stat">
          <b>{r.inside}</b>
          <span>of your stars inside</span>
        </div>
        {r.farthestLy !== null && (
          <div className="flight-readout__stat">
            <b>
              {r.farthestLy}
              <small>ly</small>
            </b>
            <span>to the farthest</span>
          </div>
        )}
      </div>
      {r.nearestLine && <p style={{ margin: '16px 0 0', fontSize: 13, color: 'rgba(244, 243, 239, 0.66)' }}>{r.nearestLine}</p>}
    </section>
  );
};
