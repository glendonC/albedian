import React, { useEffect, useRef, useState } from 'react';
import { resolveMotionTokens } from '@albedian/design-system';
import { IconButton } from '../look/index.js';
import { blockLeaveMs, reducedMotion, useLeaving } from '../hooks/useLeaving.js';
import '../styles/motion.css';

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
/** The views row, named by the photo view's pill (sample-reveal-one). */
const VIEWS_ROW = 'Your photo, Real sky, 3D';
const ROWS_3D: ReadonlyArray<readonly [string, readonly string[]]> = [
  ['Look around in 3D', ['←', '↑', '↓', '→']],
  ['Move in 3D', ['W', 'A', 'S', 'D']],
];
const LAST: readonly [string, readonly string[]] = ['Keyboard', ['?']];

const TABBABLE =
  'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/**
 * Keyboard help (INTERIM: .lead/uiux/UIUX-keys-QUESTION.md, waiting on the owner; modelled on GitHub's "?" dialog):
 * "?" opens a centred dark card over the screen dimmed to 60 percent; Esc, the close circle or a click outside closes
 * it. It hears Esc before anything else, so closing it leaves the screen beneath as it was. It arrives and leaves as
 * the motion record says (motion.css); while it leaves it is inert and no longer a dialog.
 */
export const KeyboardHelp: React.FC<{
  threeD: boolean;
  /** The photo view's name as its pill gives it (UIUX-sample-reveal-READY.md item 1: "Sample photo" on a sample night). */
  photoView?: string;
}> = ({ threeD, photoView = 'Your photo' }) => {
  const [open, setOpen] = useState(false);
  const card = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      if (open && event.key === 'Tab') {
        const dialog = card.current;
        if (!dialog) return;
        const focusables = Array.from(dialog.querySelectorAll<HTMLElement>(TABBABLE));
        if (focusables.length === 0) {
          event.preventDefault();
          dialog.focus();
          return;
        }
        const first = focusables[0]!;
        const last = focusables[focusables.length - 1]!;
        const active = document.activeElement as HTMLElement | null;
        if (event.shiftKey) {
          if (!active || active === dialog || active === first || !dialog.contains(active)) {
            event.preventDefault();
            last.focus();
          }
        } else if (!active || active === dialog || active === last || !dialog.contains(active)) {
          event.preventDefault();
          first.focus();
        }
        return;
      }
      if (typing(event.target)) return;
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
    if (open) {
      if (!wasOpen.current) {
        const active = document.activeElement;
        opener.current =
          active instanceof HTMLElement &&
          active !== document.body &&
          active !== document.documentElement &&
          !card.current?.contains(active)
            ? active
            : null;
        wasOpen.current = true;
      }
      card.current?.focus();
      return;
    }
    if (wasOpen.current) {
      wasOpen.current = false;
      const target = opener.current;
      opener.current = null;
      if (target && target.isConnected) {
        target.focus();
      } else if (card.current?.contains(document.activeElement)) {
        (document.activeElement as HTMLElement).blur();
      }
    }
  }, [open]);

  const rows = [...ROWS.map(([name, keys]) => [name === VIEWS_ROW ? `${photoView}, Real sky, 3D` : name, keys] as const), ...(threeD ? ROWS_3D : []), LAST];
  // the title and each row are its lines
  const { mounted, leaving } = useLeaving(open, blockLeaveMs(rows.length + 1, resolveMotionTokens(reducedMotion())));
  if (!mounted) return null;
  return (
    <div
      className={leaving ? 'keys-help is-leaving' : 'keys-help'}
      data-hud-interactive="true"
      inert={leaving}
      style={{ '--keys-n': rows.length } as React.CSSProperties}
    >
      <div className="keys-help__veil" onClick={() => setOpen(false)} />
      <section
        className="keys-help__card"
        role={leaving ? undefined : 'dialog'}
        aria-modal={leaving ? undefined : true}
        aria-label="Keyboard"
        tabIndex={-1}
        ref={card}
      >
        <header className="keys-help__head">
          <b className="keys-help__title">Keyboard</b>
          <IconButton label="Close" icon="x" part="keys-help__close" onClick={() => setOpen(false)} />
        </header>
        {rows.map(([label, keys], i) => (
          <div key={label} className="keys-help__row" style={{ '--keys-i': i } as React.CSSProperties}>
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
