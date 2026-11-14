/**
 * Bounded voice pool selection with target priority and incumbent hysteresis.
 */

import type { VoicePlan } from './planningTypes.js';

export const DEFAULT_VOICE_CAPACITY = 8;
export const DEFAULT_SELECTION_HYSTERESIS_DB = 3;

function rankBefore(a: VoicePlan, b: VoicePlan): boolean {
  if (a.isTarget !== b.isTarget) return a.isTarget;
  return a.levelDb > b.levelDb;
}

function compareIdsAscending(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * Chooses which planned voices sound. Targets come first, then the loudest (brightest at the
 * listener), with incumbents favoured by `hysteresisDb` so voices do not thrash when two
 * objects are nearly equal. Duplicate ids keep their loudest plan. Deterministic for any
 * input order.
 */
export function selectVoices(
  plans: readonly VoicePlan[],
  incumbents: ReadonlySet<string>,
  capacity: number = DEFAULT_VOICE_CAPACITY,
  hysteresisDb: number = DEFAULT_SELECTION_HYSTERESIS_DB
): VoicePlan[] {
  const limit = Math.max(0, Math.floor(capacity));
  const byId = new Map<string, VoicePlan>();
  for (const plan of plans) {
    const existing = byId.get(plan.id);
    if (!existing || rankBefore(plan, existing)) byId.set(plan.id, plan);
  }
  const score = (plan: VoicePlan) => plan.levelDb + (incumbents.has(plan.id) ? hysteresisDb : 0);
  return [...byId.values()]
    .sort((a, b) => {
      if (a.isTarget !== b.isTarget) return a.isTarget ? -1 : 1;
      const diff = score(b) - score(a);
      if (diff !== 0) return diff;
      return compareIdsAscending(a.id, b.id);
    })
    .slice(0, limit);
}
