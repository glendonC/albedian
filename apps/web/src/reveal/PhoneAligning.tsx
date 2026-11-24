import React from 'react';
import { EvidenceTag, MainAction } from '../look/index.js';

/**
 * The aligning block at phone width (alp-align): the Predicted tag over the person's rings (UIUX-tagphone-READY.md item
 * C), the sentence at 654, the one line of hints at 700, "Solve from here" centred at 772 at the approved phone size
 * (C30). `leaving`: the same block drawn once more by the working screen at the press (MOTION-READY-web.md section six,
 * DECIDED-BY-LEAD 24 Nov 12:58): its words and button go on words out, last first, and `onGone` is told when the last of
 * them (the sentence) has gone; it draws no tag, since the wait's own Predicted tag stands in the same place from its
 * first frame. Seen only, never pressed.
 */
export const PhoneAligningBlock: React.FC<{ onSolve?: () => void; leaving?: boolean; onGone?: () => void }> = ({ onSolve, leaving = false, onGone }) => (
  <div
    className={leaving ? 'align-phone is-leaving' : 'align-phone'}
    {...(leaving ? { 'data-testid': 'align-phone-leaving', 'aria-hidden': true, inert: true } : { 'data-testid': 'reveal-status', role: 'status' })}
  >
    {!leaving && (
      <span className="align-phone__tag">
        <EvidenceTag evidenceClass="predicted" />
      </span>
    )}
    <p className="align-phone__lead" onAnimationEnd={leaving ? onGone : undefined}>
      Drag the stars onto the dots you see.
    </p>
    {/* align-zoom-1's line under it, in its form (UIUX-alignzoom-READY.md item 7, the lead's words) */}
    <p className="align-phone__help">
      Drag to move. Turn or pinch with two fingers.
      <br />
      Double-tap to see the photo full size.
    </p>
    {(onSolve || leaving) && (
      <div className="align-phone__solve">
        <MainAction label="Solve from here" size="phone" onClick={onSolve ?? (() => {})} />
      </div>
    )}
  </div>
);
