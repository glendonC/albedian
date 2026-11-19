import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { EvidenceTag, LookIcon } from '../../look/index.js';
import { CRAFTS, type Craft, type CraftId } from './crafts.js';
import { CRAFT_CHOICES, CRAFT_PICKER_FOOT, pilotReadout } from './pilotReadout.js';

/**
 * D26 (decided 19 Nov, UIUX-pilot-READY.md) on the There screen: the craft picker (item 3) and the real trip under the
 * readout's two distances (items 4 and 5). Sizes, places and words are the record's; the pill and its circle are the
 * app's outlined second action. Movement: none of its own until MOTION's record names one.
 */

/** Phosphor's caret-up (MIT, credited with the app's icons), for the pill's circle. */
const CARET_UP = 'M213.66,165.66a8,8,0,0,1-11.32,0L128,91.31,53.66,165.66a8,8,0,0,1-11.32-11.32l80-80a8,8,0,0,1,11.32,0l80,80A8,8,0,0,1,213.66,165.66Z';

/** Phosphor's eye (MIT), the phone sheet's "Your view" row (D30 item 5, from the design page's own icon). */
const EYE =
  'M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Zm0-112a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Z';

const INK = 'var(--look-ink, #f4f3ef)';
const INK_66 = 'rgba(244, 243, 239, 0.66)';
const INK_44 = 'rgba(244, 243, 239, 0.44)';
const HAIR = 'rgba(244, 243, 239, 0.14)';

/**
 * The approved phone card sheet (D18; D30 items 4 and 5): left 12, right 12, bottom 30, radius 28, #111112, hairline.
 * It covers Back to Moment as the design page draws it, so it is drawn over the ask bar (z-index 30), outside the
 * reveal's own layer.
 */
const PHONE_SHEET: React.CSSProperties = {
  position: 'fixed',
  left: 12,
  right: 12,
  bottom: 30,
  zIndex: 36,
  boxSizing: 'border-box',
  padding: '14px 14px 12px',
  borderRadius: 28,
  background: '#111112',
  border: `1px solid ${HAIR}`,
  color: INK,
  pointerEvents: 'auto',
};

/** D30 item 3: the phone's pill names Webb "Webb"; the list keeps the full name. */
const PHONE_PILL_NAME: Partial<Record<string, string>> = { jwst: 'Webb' };

const Caret: React.FC<{ down?: boolean; sizePx?: number }> = ({ down = false, sizePx }) => (
  <svg
    className="look-i"
    viewBox="0 0 256 256"
    fill="currentColor"
    aria-hidden="true"
    focusable="false"
    style={{ ...(down ? { transform: 'rotate(180deg)' } : {}), ...(sizePx ? { width: sizePx, height: sizePx } : {}) }}
  >
    <path d={CARET_UP} />
  </svg>
);

/** A phone sheet, drawn into the page's body so it stacks over the ask bar. */
const OverAskBar: React.FC<{ children: React.ReactNode }> = ({ children }) => (typeof document === 'undefined' ? <>{children}</> : createPortal(children, document.body));

export const CraftPicker: React.FC<{ craft: Craft | null; onPick: (craft: Craft | null) => void; phone?: boolean }> = ({ craft, onPick, phone = false }) => {
  const [open, setOpen] = useState(false);
  const current = CRAFT_CHOICES.find((c) => c.id === (craft?.id ?? null))!;
  return (
    <div className="craft-picker" data-testid="craft-picker" data-hud-interactive="true" style={{ position: 'relative', pointerEvents: 'auto' }}>
      {open && phone && (
        <OverAskBar>
        <div role="listbox" aria-label="Craft" data-testid="craft-picker-panel" data-hud-interactive="true" style={PHONE_SHEET}>
          <div style={{ display: 'grid', gap: 2 }}>
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
                  style={{
                    all: 'unset',
                    boxSizing: 'border-box',
                    display: 'grid',
                    gridTemplateColumns: '44px 1fr auto 18px',
                    alignItems: 'center',
                    gap: 10,
                    width: '100%',
                    height: 52,
                    padding: '0 10px 0 6px',
                    borderRadius: 14,
                    fontSize: 15,
                    color: INK,
                    background: chosen ? 'rgba(244, 243, 239, 0.08)' : undefined,
                    cursor: 'pointer',
                  }}
                >
                  {choice.pictureUrl ? (
                    <img src={choice.pictureUrl} alt="" width={44} height={44} style={{ width: 44, height: 44, objectFit: 'contain', borderRadius: 8, background: '#000', display: 'block' }} />
                  ) : (
                    <span style={{ width: 44, height: 44, display: 'grid', placeItems: 'center', color: INK_66 }} aria-hidden="true">
                      <svg viewBox="0 0 256 256" width={18} height={18} fill="currentColor" focusable="false">
                        <path d={EYE} />
                      </svg>
                    </span>
                  )}
                  <span>{choice.name}</span>
                  <span style={{ fontSize: 12, color: INK_44, fontVariantNumeric: 'tabular-nums' }}>{choice.detail}</span>
                  <span style={{ width: 15, height: 15, color: INK }} aria-hidden="true">
                    {chosen && <LookIcon name="check" />}
                  </span>
                </button>
              );
            })}
          </div>
          <p style={{ margin: '8px 10px 4px', fontSize: 11, lineHeight: 1.4, color: INK_44 }}>{CRAFT_PICKER_FOOT}</p>
        </div>
        </OverAskBar>
      )}
      {open && !phone && (
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
      <button
        type="button"
        className={`look-second look-second--with-circle ${phone ? 'look-size-phone' : 'look-size-compact'}`}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((o) => !o)}
      >
        {(phone && current.id && PHONE_PILL_NAME[current.id]) || current.name}
        <span className="look-second__circle" aria-hidden="true">
          <Caret />
        </span>
      </button>
    </div>
  );
};

/** Items 4 and 5: under the two distances after a hairline, the craft's real trip to the star, or why it has none. */
export const PilotTrip: React.FC<{ craft: Craft; starName: string; starDistancePc: number; flightSeconds: number | null; phone?: boolean }> = ({ craft, starName, starDistancePc, flightSeconds, phone = false }) => {
  const r = pilotReadout(craft, starName, starDistancePc, flightSeconds);
  // On the phone the speed's source sits in the details sheet (D30 item 5)
  const [first, ...fine] = phone ? r.lines.filter((line) => line !== r.source) : r.lines;
  return (
    <div
      className="pilot-trip"
      data-testid="pilot-trip"
      style={phone ? { marginTop: 14, paddingTop: 12, borderTop: `1px solid ${HAIR}`, display: 'grid', gap: 3 } : { marginTop: 22, paddingTop: 18, borderTop: `1px solid ${HAIR}`, display: 'grid', gap: 4, maxWidth: 320 }}
    >
      <span style={{ fontSize: 13, color: INK_66 }}>{r.name}</span>
      {r.trip ? (
        <>
          <b style={{ font: phone ? '300 30px/1.05 var(--look-font-sans)' : '300 44px/1.05 var(--look-font-sans)', letterSpacing: '-0.035em', color: INK, fontVariantNumeric: phone ? 'tabular-nums' : undefined }}>
            {r.trip.value}
            <small style={{ fontSize: phone ? 14 : 16, marginLeft: phone ? 5 : 6, color: INK_66, letterSpacing: 0 }}>{r.trip.unit}</small>
          </b>
          {first && <span style={{ fontSize: 13, color: INK_66 }}>{first}</span>}
          {fine.map((line) => (
            <p key={line} style={{ margin: phone ? '4px 0 0' : '10px 0 0', fontSize: 12, lineHeight: 1.45, color: INK_44 }}>
              {line}
            </p>
          ))}
        </>
      ) : (
        r.none && <p style={{ margin: phone ? '2px 0 0' : '4px 0 0', fontSize: 14, lineHeight: 1.45, color: INK_66 }}>{r.none}</p>
      )}
    </div>
  );
};

/**
 * D30 item 2a: the 36 px icon-only circle at the tag row's right (the approved icon circle, its border at 30 percent),
 * a caret up that opens the details; pressed it says so and turns down.
 */
export const DetailsCircle: React.FC<{ open: boolean; onToggle: () => void }> = ({ open, onToggle }) => (
  <button
    type="button"
    className={`look-icon${open ? ' is-on' : ''}`}
    data-testid="details-circle"
    aria-label={open ? 'Fewer details' : 'More details'}
    aria-expanded={open}
    onClick={onToggle}
    style={{ width: 36, height: 36, justifyContent: 'center', borderColor: 'rgba(244, 243, 239, 0.3)', pointerEvents: 'auto' }}
  >
    <Caret down={open} sizePx={15} />
  </button>
);

/** The tag row the phone readout and its details sheet share: the Simulated tag, the details circle at the right. */
export const PhoneTagRow: React.FC<{ open: boolean; onToggle: () => void }> = ({ open, onToggle }) => (
  <div className="flight-readout__tagrow" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 36 }}>
    <EvidenceTag evidenceClass="simulated" />
    <DetailsCircle open={open} onToggle={onToggle} />
  </div>
);

/** D30 item 5: the whole disclosure as the phone card sheet, the tag row on top, each line at 13 px, ink 66 percent. */
export const DetailsSheet: React.FC<{ lines: readonly string[]; onClose: () => void }> = ({ lines, onClose }) => (
  <OverAskBar>
  <div className="details-sheet" data-testid="details-sheet" data-hud-interactive="true" style={{ ...PHONE_SHEET, padding: '14px 18px 20px' }}>
    <PhoneTagRow open onToggle={onClose} />
    <ul style={{ listStyle: 'none', margin: '14px 0 0', padding: 0, display: 'grid', gap: 9 }}>
      {lines.map((line) => (
        <li key={line} style={{ fontSize: 13, lineHeight: 1.45, color: INK_66 }}>
          {line}
        </li>
      ))}
    </ul>
  </div>
  </OverAskBar>
);
