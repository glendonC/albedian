import { useEffect, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';

/** The browser's request for less motion (the same query the phone card reads, components/phoneSheet.ts). */
export const PREFERS_REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

function read(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(PREFERS_REDUCED_MOTION_QUERY).matches;
}

/** Whether the person asks for less motion, kept current when they change the setting. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(read);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const list = window.matchMedia(PREFERS_REDUCED_MOTION_QUERY);
    const onChange = () => setReduced(read());
    onChange();
    list.addEventListener?.('change', onChange);
    return () => list.removeEventListener?.('change', onChange);
  }, []);
  return reduced;
}

/**
 * The session with its reducedMotion as the person asks (REQ-DSA-2.1: Go there and Back to Moment dissolve with no
 * camera travel); the same object when it already is, so keeping it in step costs no render.
 */
export function withReducedMotion(session: ObservationSession, reduced: boolean): ObservationSession {
  return session.reducedMotion === reduced ? session : { ...session, reducedMotion: reduced };
}
