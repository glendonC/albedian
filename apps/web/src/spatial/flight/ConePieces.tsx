import React from 'react';
import { EvidenceTag } from '../../look/index.js';
import { CONE_LINES, coneReadout, type ConeGeometry } from './coneView.js';
import { PhoneTagRow } from './PilotPieces.js';

/**
 * D28 (decided 19 Nov, UIUX-cone-READY.md): the way into your photo's view and its readout. The pill sits right of
 * D26's craft pill, 10 px apart; pressed it fills (ink background, black text). The readout takes the There screen's
 * right column: the Simulated tag, "Your photo's view" at 52 px, two numbers, and the nearest star's line at 13 px.
 */

const INK = 'var(--look-ink, #f4f3ef)';

/** The bottom left row: the craft pill (none in the cone view) and the pill into your photo's view. */
export const FlightPills: React.FC<{ craftPill: React.ReactNode; cone: boolean; onCone: () => void; phone?: boolean; showCone?: boolean }> = ({ craftPill, cone, onCone, phone = false, showCone = true }) => (
  <div
    className={phone ? 'flight-pills is-phone' : 'flight-pills'}
    data-testid="flight-pills"
    data-hud-interactive="true"
    // D30 item 3 on the phone: one row at top 712, left 24, 10 apart, 44 px high
    style={phone ? { position: 'absolute', left: 24, right: 24, top: 712, display: 'flex', gap: 10, pointerEvents: 'auto' } : { position: 'absolute', left: 36, bottom: 32, display: 'flex', alignItems: 'flex-end', gap: 10, pointerEvents: 'auto' }}
  >
    {/* On the laptop the craft pill leaves in the cone view (D28); D30's phone row keeps both */}
    {(!cone || phone) && craftPill}
    {showCone && <button
      type="button"
      className={phone ? 'look-second look-size-phone' : 'look-second look-size-compact'}
      data-testid="cone-pill"
      aria-pressed={cone}
      onClick={onCone}
      style={cone ? { background: INK, color: '#000', borderColor: INK } : undefined}
    >
      Your photo&apos;s view
    </button>}
  </div>
);

export interface ConeReadoutProps {
  geometry: ConeGeometry;
  phone?: boolean;
  answerOpen?: boolean;
  hidden?: boolean;
  onDetails?: () => void;
}

export const ConeReadout = React.forwardRef<HTMLElement, ConeReadoutProps>(function ConeReadout(
  { geometry, phone = false, answerOpen = false, hidden = false, onDetails },
  ref,
) {
  const r = coneReadout(geometry);
  if (phone) {
    // D30 item 7, with UIUX-tagphone-READY.md item B when an answer is open: in the flight readout's place, the tag and
    // the circle, then the first sentence; the view's name, two numbers and nearest line hide while an answer is open
    return (
      <section ref={ref} className="flight-readout is-phone" data-testid="cone-readout" aria-label="Your photo's view" style={hidden ? { visibility: 'hidden' } : undefined}>
        <PhoneTagRow open={false} onToggle={() => onDetails?.()} />
        <p className="flight-readout__note">{CONE_LINES.spec}</p>
        {!answerOpen && (
          <>
            <p data-testid="cone-title" style={{ margin: '0 0 10px', fontSize: 15, color: INK }}>
              Your photo&apos;s view
            </p>
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
            {r.nearestLine && <p style={{ margin: '12px 0 0', fontSize: 13, color: 'rgba(244, 243, 239, 0.66)' }}>{r.nearestLine}</p>}
          </>
        )}
      </section>
    );
  }
  return (
    <section ref={ref} className="flight-readout" data-testid="cone-readout" aria-label="Your photo's view">
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
});
