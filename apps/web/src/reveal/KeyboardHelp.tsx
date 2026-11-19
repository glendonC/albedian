import React, { useEffect, useRef, useState } from 'react';
import { IconButton } from '../look/index.js';

/** Whether a key went to a field the person is typing in. */
const typing = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  const tag = el?.tagName?.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable === true;
};

/**
 * The keys the app handles, each row a button's own name and its keys (INTERIM: .lead/uiux/UIUX-keys-QUESTION.md). The
 * 3D view's keys move its camera only where that view is on screen, so they are listed only there.
 */
const ROWS: ReadonlyArray<readonly [string, readonly string[]]> = [
  ['Ask out loud', ['Space']],
  ['Type a question', ['/']],
  ['Go there', ['G']],
  ['Back to Moment', ['Esc']],
  ['Close', ['Esc']],
  ['Your photo, Real sky, 3D', ['←', '→']],
];
const ROWS_3D: ReadonlyArray<readonly [string, readonly string[]]> = [
  ['Look around in 3D', ['←', '↑', '↓', '→']],
  ['Move in 3D', ['W', 'A', 'S', 'D']],
];
const LAST: readonly [string, readonly string[]] = ['Keyboard', ['?']];

/**
 * Keyboard help (INTERIM: .lead/uiux/UIUX-keys-QUESTION.md, waiting on the owner; modelled on GitHub's "?" dialog):
 * "?" opens a centred dark card over the screen dimmed to 60 percent; Esc, the close circle or a click outside closes
 * it. It hears Esc before anything else, so closing it leaves the screen beneath as it was. No movement (the lead's
 * rule: none before the MOTION record's yes).
 */
export const KeyboardHelp: React.FC<{ threeD: boolean }> = ({ threeD }) => {
  const [open, setOpen] = useState(false);
  const card = useRef<HTMLElement>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return;
      if (event.key === '?') {
        event.preventDefault();
        setOpen(true);
      } else if (open && event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
      }
    };
    // In the capture phase, so its Esc is heard before the card's, the answer's or Back to Moment's
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [open]);

  useEffect(() => {
    if (open) card.current?.focus();
  }, [open]);

  if (!open) return null;
  const rows = [...ROWS, ...(threeD ? ROWS_3D : []), LAST];
  return (
    <div className="keys-help" data-hud-interactive="true">
      <div className="keys-help__veil" onClick={() => setOpen(false)} />
      <section className="keys-help__card" role="dialog" aria-label="Keyboard" tabIndex={-1} ref={card}>
        <header className="keys-help__head">
          <b className="keys-help__title">Keyboard</b>
          <IconButton label="Close" icon="x" part="keys-help__close" onClick={() => setOpen(false)} />
        </header>
        {rows.map(([label, keys]) => (
          <div key={label} className="keys-help__row">
            <span>{label}</span>
            <span className="keys-help__keys">
              {keys.map((k) => (
                <kbd key={k}>{k}</kbd>
              ))}
            </span>
          </div>
        ))}
      </section>
    </div>
  );
};
