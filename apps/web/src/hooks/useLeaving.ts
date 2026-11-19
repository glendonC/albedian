import { useEffect, useState } from 'react';
import type { MotionTokens } from '@albedian/design-system';
import { REDUCED_MOTION_QUERY } from '../components/phoneSheet.js';

/** Whether the person asked for reduced motion; false where the browser cannot say. */
export const reducedMotion = (): boolean =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches;

/**
 * How long a card or block takes to leave (the motion record, MOTION-READY-web.md "Any card or block"): its lines go
 * last first, one stagger apart, in words out, and its frame goes with the last line in frame out. Under reduced
 * motion the stagger is 0 and both are the 270 ms dissolve.
 */
export function blockLeaveMs(lines: number, m: MotionTokens): number {
  return m.staggerMs * Math.max(0, lines - 1) + Math.max(m.wordsOutMs, m.frameOutMs);
}

/**
 * Keeps a block mounted while it leaves: open, it is mounted at once; closed, it stays mounted and leaving for leaveMs,
 * then unmounts. Opened again while leaving, it is taken back without unmounting.
 */
export function useLeaving(open: boolean, leaveMs: number): { mounted: boolean; leaving: boolean } {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return undefined;
    }
    const timer = setTimeout(() => setMounted(false), leaveMs);
    return () => clearTimeout(timer);
  }, [open, leaveMs]);
  return { mounted: open || mounted, leaving: !open && mounted };
}
