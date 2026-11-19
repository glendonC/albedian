import React, { useEffect, useRef } from 'react';
import type { CelestialFinding } from '@albedian/contracts';
import { EvidenceGlyph, EvidenceTag, IconButton, LookIcon, MainAction, SecondAction } from '../look/index.js';
import type { DeepSkyRow } from './deepSky.js';
import { starCardFacts } from './starCardFacts.js';
import '../styles/motion.css';

export interface StarCardProps {
  finding: CelestialFinding;
  /** Whether Original pixels is pressed: the photo whole, with no fade, no marks and no corners. */
  originalPixels: boolean;
  onToggleOriginalPixels: () => void;
  /** Go there, pressed (or G): once per press. */
  onGoThere: () => void;
  /** The close button, Esc, or a tap on empty sky. */
  onClose: () => void;
  /** At phone width (reveal, approved 2026-11-12, iPhone 3; #p-pick): the same card on its own dark sheet at the foot. */
  phone?: boolean;
  /** For a deep-sky object, its OpenNGC row, for its type in words (D23). */
  deepSky?: DeepSkyRow | null;
  /** Let go: its lines are leaving (the motion record); it hears no keys or taps and takes no input meanwhile. */
  leaving?: boolean;
  /**
   * Opened from a name in D33's predicted sky (ruled 13:25): no Original pixels, since no photo is on screen; Go there
   * only where the star can be flown to; with neither, no action row.
   */
  inSky?: boolean;
}

/** A pointer that went down and up within this distance is a tap, not a drag. */
const TAP_SLOP_PX = 6;
const typing = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  const tag = el?.tagName?.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable === true;
};

/**
 * The picked star's card (reveal, approved 2026-11-12, laptop screen 3) in the right column: its evidence tag and a
 * close button, its name, its designation and constellation, its numbers each with the glyph of its own class, the
 * catalog row, Go there (only for a star the app can fly to) and Original pixels, and at the foot its limitations,
 * always in view. Esc and a tap on empty sky close it; G goes there. Every word is the finding's (starCardFacts.ts).
 */
export const StarCard: React.FC<StarCardProps> = ({ finding, originalPixels, onToggleOriginalPixels, onGoThere, onClose, phone = false, deepSky = null, leaving = false, inSky = false }) => {
  const facts = starCardFacts(finding, deepSky);
  const close = useRef(onClose);
  close.current = onClose;
  const goThere = useRef(onGoThere);
  goThere.current = onGoThere;
  const canGoThere = facts.canGoThere;

  useEffect(() => {
    if (leaving) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || typing(event.target)) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close.current();
        return;
      }
      if (!canGoThere || event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
      if (event.key !== 'g' && event.key !== 'G') return;
      // A focused button keeps its own keys
      if ((event.target as HTMLElement | null)?.tagName?.toLowerCase() === 'button') return;
      event.preventDefault();
      goThere.current();
    };
    let down: { x: number; y: number } | null = null;
    const onDown = (event: PointerEvent) => {
      down = { x: event.clientX, y: event.clientY };
    };
    const onUp = (event: PointerEvent) => {
      const start = down;
      down = null;
      if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > TAP_SLOP_PX) return;
      const target = event.target as Element | null;
      if (target?.closest?.('[data-hud-interactive="true"] button, [data-hud-interactive="true"] a, .reveal-card, button, a, input, textarea, select')) return;
      close.current();
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('pointerdown', onDown);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('pointerup', onUp);
    };
  }, [canGoThere, leaving]);

  return (
    <section className={`${phone ? 'reveal-card is-phone' : 'reveal-card'}${leaving ? ' is-leaving' : ''}`} data-testid="star-card" aria-label={facts.title} inert={leaving}>
      <div className="reveal-card__top">
        <EvidenceTag evidenceClass={facts.evidenceClass} />
        <IconButton label="Close" icon="x" onClick={onClose} />
      </div>
      {/* Under the tag and close, the card's words; at phone width they scroll inside the card (D18, INTERIM) */}
      <div className="reveal-card__scroll">
        <div className="reveal-card__in">
          <h1 className="reveal-card__name">{facts.title}</h1>
          {facts.subLine !== null && <p className="reveal-card__sub">{facts.subLine}</p>}
          {facts.numbers.length > 0 && (
            <div className="reveal-card__numbers">
              {facts.numbers.map((number) => (
                <div key={number.label} className="reveal-card__number" data-evidence-class={number.evidenceClass}>
                  <b>
                    {number.value}
                    <small>{number.unit}</small>
                  </b>
                  <span className="reveal-card__label">
                    <EvidenceGlyph evidenceClass={number.evidenceClass} />
                    {number.label}
                  </span>
                </div>
              ))}
            </div>
          )}
          {facts.sourceLine !== null && <p className="reveal-card__source">{facts.sourceLine}</p>}
          {(!inSky || canGoThere) && (
            <div className="reveal-card__acts">
              {canGoThere && <MainAction label="Go there" onClick={onGoThere} />}
              {!inSky && <SecondAction label="Original pixels" aria-pressed={originalPixels} onClick={onToggleOriginalPixels} />}
            </div>
          )}
          {facts.notes.length > 0 && (
            <ul className="reveal-card__notes">
              {facts.notes.map((note) => (
                <li key={note}>
                  <LookIcon name="info" />
                  <span>{note}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
};
