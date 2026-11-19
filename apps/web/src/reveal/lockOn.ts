import { placeMarks, type FrameRect } from './AlignLayer.js';
import type { EarlyPose } from './earlyPose.js';
import type { RevealMark } from './revealMarks.js';

/**
 * The lock-on (D25 point 2; MOTION-READY-web.md section 6, decided by the lead 07:51): when the solve's check passes,
 * each early mark the solve places travels from its pose place to its solve place, all together, and its matched mark
 * takes its place at rest; an early mark the solve does not place leaves. Only the two real places are used: a mark
 * never stands where neither the pose nor the solve put it.
 */

export interface LockOnTravel {
  id: string;
  name: string | null;
  nameOnLeft: boolean;
  fromXPx: number;
  fromYPx: number;
  toXPx: number;
  toYPx: number;
}

export interface LockOnLeave {
  id: string;
  name: string | null;
  nameOnLeft: boolean;
  xPx: number;
  yPx: number;
}

export interface LockOnPlan {
  travelling: LockOnTravel[];
  leaving: LockOnLeave[];
  /** The reveal's marks an early mark travels to: each comes in at rest, as its early mark goes. */
  lockedIds: Set<string>;
}

/**
 * The early marks as the working screen drew them over this frame (placeMarks, names where they fit), each sent to the
 * reveal mark of the same catalog object (else the same finding), never to a deep-sky symbol; the rest leave.
 */
export function lockOnPlan(early: EarlyPose, frame: FrameRect, marks: readonly RevealMark[]): LockOnPlan {
  const drawn = placeMarks(early.marks, frame, 13);
  const keyOf = new Map(early.marks.map((m) => [m.id, m.key]));
  const targets = marks.filter((mark) => mark.deepSky === null);
  const plan: LockOnPlan = { travelling: [], leaving: [], lockedIds: new Set() };
  for (const mark of drawn) {
    const key = keyOf.get(mark.id);
    const target =
      (key !== undefined ? targets.find((t) => t.finding.catalogRef?.catalogSourceId === key) : undefined) ??
      targets.find((t) => t.finding.id === mark.id);
    if (target && !plan.lockedIds.has(target.finding.id)) {
      plan.lockedIds.add(target.finding.id);
      plan.travelling.push({ id: mark.id, name: mark.name, nameOnLeft: mark.nameOnLeft, fromXPx: mark.xPx, fromYPx: mark.yPx, toXPx: target.xPx, toYPx: target.yPx });
    } else {
      plan.leaving.push({ id: mark.id, name: mark.name, nameOnLeft: mark.nameOnLeft, xPx: mark.xPx, yPx: mark.yPx });
    }
  }
  return plan;
}
