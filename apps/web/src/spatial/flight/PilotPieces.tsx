import React, { useState } from 'react';
import { LookIcon } from '../../look/index.js';
import { CRAFTS, type Craft, type CraftId } from './crafts.js';
import { CRAFT_CHOICES, CRAFT_PICKER_FOOT, pilotReadout } from './pilotReadout.js';

/**
 * D26 (decided 19 Nov, UIUX-pilot-READY.md) on the There screen: the craft picker (item 3) and the real trip under the
 * readout's two distances (items 4 and 5). Sizes, places and words are the record's; the pill and its circle are the
 * app's outlined second action. Movement: none of its own until MOTION's record names one.
 */

/** Phosphor's caret-up (MIT, credited with the app's icons), for the pill's circle. */
const CARET_UP = 'M213.66,165.66a8,8,0,0,1-11.32,0L128,91.31,53.66,165.66a8,8,0,0,1-11.32-11.32l80-80a8,8,0,0,1,11.32,0l80,80A8,8,0,0,1,213.66,165.66Z';

const INK = 'var(--look-ink, #f4f3ef)';
const INK_66 = 'rgba(244, 243, 239, 0.66)';
const INK_44 = 'rgba(244, 243, 239, 0.44)';
const HAIR = 'rgba(244, 243, 239, 0.14)';

export const CraftPicker: React.FC<{ craft: Craft | null; onPick: (craft: Craft | null) => void }> = ({ craft, onPick }) => {
  const [open, setOpen] = useState(false);
  const current = CRAFT_CHOICES.find((c) => c.id === (craft?.id ?? null))!;
  return (
    <div className="craft-picker" data-testid="craft-picker" data-hud-interactive="true" style={{ position: 'absolute', left: 36, bottom: 32, pointerEvents: 'auto' }}>
      {open && (
        <div
          role="listbox"
          aria-label="Craft"
          data-testid="craft-picker-panel"
          style={{
            position: 'absolute',
            left: 0,
            bottom: 'calc(100% + 10px)',
            width: 392,
            borderRadius: 18,
            background: 'rgba(16, 16, 18, 0.92)',
            border: `1px solid ${HAIR}`,
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
            padding: '8px 0',
            color: INK,
          }}
        >
          {CRAFT_CHOICES.map((choice) => {
            const chosen = choice.id === (craft?.id ?? null);
            return (
              <button
                key={choice.id ?? 'your-view'}
                type="button"
                role="option"
                aria-selected={chosen}
                data-craft={choice.id ?? 'none'}
                onClick={() => {
                  onPick(choice.id ? CRAFTS[choice.id as CraftId] : null);
                  setOpen(false);
                }}
                style={{ all: 'unset', boxSizing: 'border-box', display: 'flex', alignItems: 'center', gap: 14, width: '100%', height: 52, padding: '0 16px', cursor: 'pointer' }}
              >
                <span style={{ width: 44, height: 44, flex: '0 0 44px', borderRadius: 10, overflow: 'hidden', background: 'rgba(244, 243, 239, 0.06)' }}>
                  {choice.pictureUrl && <img src={choice.pictureUrl} alt="" width={44} height={44} style={{ width: 44, height: 44, objectFit: 'contain', display: 'block' }} />}
                </span>
                <span style={{ flex: 1, minWidth: 0, display: 'grid', gap: 2 }}>
                  <span style={{ fontSize: 15 }}>{choice.name}</span>
                  <span style={{ fontSize: 12, color: INK_44 }}>{choice.detail}</span>
                </span>
                {chosen && (
                  <span style={{ width: 18, height: 18, color: INK }} aria-hidden="true">
                    <LookIcon name="check" />
                  </span>
                )}
              </button>
            );
          })}
          <p style={{ margin: '6px 16px 4px', paddingTop: 8, borderTop: `1px solid ${HAIR}`, fontSize: 11, color: INK_44 }}>{CRAFT_PICKER_FOOT}</p>
        </div>
      )}
      <button type="button" className="look-second look-second--with-circle look-size-compact" aria-expanded={open} aria-haspopup="listbox" onClick={() => setOpen((o) => !o)}>
        {current.name}
        <span className="look-second__circle" aria-hidden="true">
          <svg className="look-i" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false">
            <path d={CARET_UP} />
          </svg>
        </span>
      </button>
    </div>
  );
};

/** Items 4 and 5: under the two distances after a hairline, the craft's real trip to the star, or why it has none. */
export const PilotTrip: React.FC<{ craft: Craft; starName: string; starDistancePc: number; flightSeconds: number | null }> = ({ craft, starName, starDistancePc, flightSeconds }) => {
  const r = pilotReadout(craft, starName, starDistancePc, flightSeconds);
  const [first, ...fine] = r.lines;
  return (
    <div className="pilot-trip" data-testid="pilot-trip" style={{ marginTop: 22, paddingTop: 18, borderTop: `1px solid ${HAIR}`, display: 'grid', gap: 4, maxWidth: 320 }}>
      <span style={{ fontSize: 13, color: INK_66 }}>{r.name}</span>
      {r.trip ? (
        <>
          <b style={{ font: '300 44px/1.05 var(--look-font-sans)', letterSpacing: '-0.035em', color: INK }}>
            {r.trip.value}
            <small style={{ fontSize: 16, marginLeft: 6, color: INK_66, letterSpacing: 0 }}>{r.trip.unit}</small>
          </b>
          {first && <span style={{ fontSize: 13, color: INK_66 }}>{first}</span>}
          {fine.map((line) => (
            <p key={line} style={{ margin: '10px 0 0', fontSize: 12, lineHeight: 1.45, color: INK_44 }}>
              {line}
            </p>
          ))}
        </>
      ) : (
        r.none && <p style={{ margin: '4px 0 0', fontSize: 14, lineHeight: 1.45, color: INK_66 }}>{r.none}</p>
      )}
    </div>
  );
};
