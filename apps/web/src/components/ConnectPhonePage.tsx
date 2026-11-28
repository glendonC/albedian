import React, { useEffect, useMemo, useRef } from 'react';
import { encodeQr } from '../../../../scripts/qr.mjs';
import { IconButton } from '../look/index.js';
import { phoneCodeGeometry } from './phoneAddress.js';

/** Q3's one line under the code, Expo's sentence in its page form (UIUX-phone-code-READY.md Q6). */
export const PHONE_CODE_LINE = 'Scan the QR code to open Albedian on your phone.';

/**
 * phone-code-home-one (UIUX-phone-code-READY.md Q3): the laptop's phone address as a code, in the Credits page's frame:
 * Back at the top left, the title "Connect phone" in the Credits title's style, the code on a white square with its
 * quiet zone inside it, the line, and the address, selectable, for typing. It leaves as Credits leaves (home.css:
 * the frame out, its words out); its code arrives with the words, as the Credits page's rows do [inference].
 */
export const ConnectPhonePage: React.FC<{ address: string; onBack: () => void; leaving?: boolean }> = ({ address, onBack, leaving = false }) => {
  const back = useRef(onBack);
  back.current = leaving ? () => {} : onBack;
  const page = useRef<HTMLElement>(null);
  useEffect(() => {
    page.current?.focus();
  }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      back.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
  // The encoder's code at level L (Q1), one 1 px unit per CSS pixel, each dark module a whole square
  const drawn = useMemo(() => {
    const code = encodeQr(address);
    const { modulePx, sidePx, quietPx } = phoneCodeGeometry(code.size);
    const d = code.modules
      .flatMap((row, y) => row.flatMap((on, x) => (on ? [`M${quietPx + x * modulePx} ${quietPx + y * modulePx}h${modulePx}v${modulePx}h-${modulePx}z`] : [])))
      .join('');
    return { sidePx, d };
  }, [address]);
  return (
    <section
      className={leaving ? 'credits-page is-leaving' : 'credits-page'}
      data-testid="connect-phone-page"
      aria-labelledby="connect-phone-title"
      ref={page}
      tabIndex={-1}
      inert={leaving}
    >
      <IconButton label="Back" icon="arrow-left" onClick={() => back.current()} />
      <h1 id="connect-phone-title" className="credits-title">
        Connect phone
      </h1>
      <div className="phone-code">
        <svg
          className="phone-code__square"
          width={drawn.sidePx}
          height={drawn.sidePx}
          viewBox={`0 0 ${drawn.sidePx} ${drawn.sidePx}`}
          shapeRendering="crispEdges"
          aria-hidden="true"
          focusable="false"
        >
          <rect width={drawn.sidePx} height={drawn.sidePx} rx="24" fill="#ffffff" />
          <path d={drawn.d} fill="#000000" />
        </svg>
        <p className="phone-code__line">{PHONE_CODE_LINE}</p>
        <p className="phone-code__address">{address}</p>
      </div>
    </section>
  );
};
