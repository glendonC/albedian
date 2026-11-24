import React, { useEffect, useRef } from 'react';
import { EvidenceTag, LookIcon, MainAction, SecondAction } from '../look/index.js';
import type { RevealStatus } from './revealStatus.js';

/**
 * The no-match reveal at phone width (no-match, approved 2026-11-18, iPhone; #ump-photo): the app's sentence under the
 * photo, Show predicted sky centred where Ask out loud sits when a predicted sky can open, and under it the warnings in
 * one list, reached by scrolling the whole screen; the photo moves up with it (the lead's answer 23:50, INTERIM).
 */
export const PhoneNoMatch: React.FC<{
  status: RevealStatus;
  onShowPredictedSky: () => void;
  onAlign?: () => void;
  onRestore?: () => void;
  clip?: boolean;
}> = ({
  status,
  onShowPredictedSky,
  onAlign,
  onRestore,
  clip = false,
}) => {
  const { screen, onScroll } = usePhoneScroll();
  return (
    <div ref={screen} className="reveal-phone-scroll" data-testid="reveal-phone-scroll" onScroll={onScroll}>
      <div
        className={clip ? 'reveal-status is-unmatched is-phone is-clip' : 'reveal-status is-unmatched is-phone'}
        data-testid="reveal-status"
        role="status"
      >
        <div className="reveal-phone-lead">
          {status.lines.map((line) => (
            <p key={line} className="reveal-status__lead">
              {line}
            </p>
          ))}
        </div>
        {status.predictedSky && (
          <div className="reveal-phone-go">
            <MainAction label="Show predicted sky" size="phone" onClick={() => onShowPredictedSky()} />
          </div>
        )}
        {/* Line it up yourself under Show predicted sky, reached by scrolling (D24, UIUX-align-READY.md point 1) */}
        {onAlign && (
          <div className="reveal-phone-go reveal-phone-go--more">
            <SecondAction label="Line it up yourself" size="phone" onClick={() => onAlign()} />
          </div>
        )}
        {/* Restore after the screen's own actions, in the laptop's order (d38-2b; UIUX-nomatch-restore-READY.md, UIUX-align-READY.md point 1) */}
        {onRestore && (
          <div className="reveal-phone-go reveal-phone-go--restore">
            <SecondAction label="Restore" size="phone" onClick={() => onRestore()} />
          </div>
        )}
        {status.warnings.length > 0 && (
          <ul className="reveal-status__warns">
            {status.warnings.map((warning) => (
              <li key={warning}>
                <LookIcon name="info" />
                <span>{warning}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

/** The whole screen scrolls, and the photo (and the rings drawn on it) move up with it. */
function usePhoneScroll() {
  const screen = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.phoneScroll = '';
    return () => {
      delete root.dataset.phoneScroll;
      root.style.removeProperty('--phone-scroll');
    };
  }, []);
  const onScroll = () => {
    document.documentElement.style.setProperty('--phone-scroll', `${screen.current?.scrollTop ?? 0}px`);
  };
  return { screen, onScroll };
}

/**
 * D24's refused state at phone width (UIUX-align-READY.md, drawn 18:01 as alp-refused): on the approved phone no-match
 * frame, the Predicted tag at 606 (the 17:56 tag places, with no entrance), "No stars matched near your alignment." at
 * 646, "Your alignment is kept, drawn as predicted." at 704, "Line it up again" centred at 772 where Solve from here
 * was, then Show predicted sky and Restore under it, reached by scrolling. The kept rings are drawn over the photo.
 */
export const PhoneAlignRefused: React.FC<{
  onAgain: () => void;
  onShowPredictedSky?: () => void;
  onRestore?: () => void;
  clip?: boolean;
  hideTag?: boolean;
}> = ({ onAgain, onShowPredictedSky, onRestore, clip = false, hideTag = false }) => {
  const { screen, onScroll } = usePhoneScroll();
  const words = (
    <>
      {!hideTag && <EvidenceTag evidenceClass="predicted" />}
      <p className="reveal-status__lead">No stars matched near your alignment.</p>
      <p className="align-refused__kept">Your alignment is kept, drawn as predicted.</p>
    </>
  );
  return (
    <div
      ref={screen}
      className="reveal-phone-scroll"
      data-testid="reveal-phone-scroll"
      onScroll={onScroll}
      style={hideTag ? { overflow: 'hidden', pointerEvents: 'none' } : undefined}
    >
      <div
        className={
          clip
            ? 'reveal-status is-unmatched is-phone is-refused is-clip has-predicted'
            : 'reveal-status is-unmatched is-phone is-refused has-predicted'
        }
        data-testid="reveal-status"
        role="status"
      >
        {clip ? <div className="align-refused__block">{words}</div> : words}
        <div className="reveal-phone-go">
          <MainAction label="Line it up again" size="phone" onClick={() => onAgain()} />
        </div>
        {(onShowPredictedSky || onRestore) && (
          <div className="reveal-phone-more">
            {onShowPredictedSky && <SecondAction label="Show predicted sky" size="phone" onClick={() => onShowPredictedSky()} />}
            {onRestore && <SecondAction label="Restore" size="phone" onClick={() => onRestore()} />}
          </div>
        )}
      </div>
    </div>
  );
};
