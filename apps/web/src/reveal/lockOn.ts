import { placeMarks, type FrameRect } from './AlignLayer.js';
import type { EarlyPose, PlacedTarget } from './earlyPose.js';
import type { Box } from './predictedSkyMarks.js';
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

/** The same early marks, however often their pose is read again: their ids and places. */
const keyOfMarks = (marks: readonly PlacedTarget[]) => marks.map((m) => `${m.id}@${m.u.toFixed(6)},${m.v.toFixed(6)}`).join('|');
// The places the working screen's early marks kept clear of when it last drew them (UIUX-keepclear-READY.md place 5)
let workingClear: { key: string; clear: readonly Box[] } | null = null;

/** Said by the working screen each time it draws its early marks: the places they kept clear of. */
export function rememberWorkingClear(marks: readonly PlacedTarget[], clear: readonly Box[]): void {
  workingClear = { key: keyOfMarks(marks), clear };
}

/**
 * The early marks as the working screen drew them over this frame (placeMarks, names where they fit, clear of the
 * working column as it was), each sent to the reveal mark of the same catalog object (else the same finding), never to
 * a deep-sky symbol; the rest leave. An early mark the working screen did not draw (under its column) neither travels
 * nor leaves: its matched mark comes in at its solve place as the reveal's marks do (UIUX-keepclear-READY.md item 6;
 * MOTION-READY-web.md section 6, the 400 ms row). `fontPx`: the names' size the working screen drew them at (13 px, 11 px
 * on the phone's wait), so the same names stand on the same sides.
 */
export function lockOnPlan(early: EarlyPose, frame: FrameRect, marks: readonly RevealMark[], fontPx = 13): LockOnPlan {
  const clear = workingClear !== null && workingClear.key === keyOfMarks(early.marks) ? workingClear.clear : [];
  const drawn = placeMarks(early.marks, frame, fontPx, clear);
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
