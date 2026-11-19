import React, { useEffect, useRef } from 'react';
import { LookIcon, MainAction } from '../look/index.js';
import type { RevealStatus } from './revealStatus.js';

/**
 * The no-match reveal at phone width (no-match, approved 2026-11-18, iPhone; #ump-photo): the app's sentence under the
 * photo, Show predicted sky centred where Ask out loud sits when a predicted sky can open, and under it the warnings in
 * one list, reached by scrolling the whole screen; the photo moves up with it (the lead's answer 23:50, INTERIM).
 */
export const PhoneNoMatch: React.FC<{ status: RevealStatus; onShowPredictedSky: () => void }> = ({ status, onShowPredictedSky }) => {
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
  return (
    <div ref={screen} className="reveal-phone-scroll" data-testid="reveal-phone-scroll" onScroll={onScroll}>
      <div className="reveal-status is-unmatched is-phone" data-testid="reveal-status" role="status">
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
