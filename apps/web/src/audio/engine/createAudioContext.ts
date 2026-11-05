export type AudioContextFactory = () => AudioContext | null;

/**
 * Creates the browser AudioContext. The engine calls this only from `start()`, and `start()`
 * must only be called from a user-gesture handler, so no context exists before a gesture.
 */
export const createBrowserAudioContext: AudioContextFactory = () => {
  if (typeof window === 'undefined') return null;
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    return new Ctor({ latencyHint: 'interactive' });
  } catch {
    return null;
  }
};

/** Sets an AudioParam now, then glides toward later values. */
export function setParamNow(param: AudioParam, value: number, now: number): void {
  param.cancelScheduledValues(now);
  param.setValueAtTime(value, now);
}

/**
 * Less than a sample at any rate the engine runs at: a glide cancels only what comes after its own
 * start, so a value set at that same instant (setParamNow on a new node) is where it starts.
 * Cancelling at `now` itself would delete that value, and the glide would start from the
 * parameter's default instead (a gain's 1): the bed's full-level opening hiss and the skipped voice
 * attacks found on 2026-11-04 (AUDIO_ENGINE.md section 10, item 0).
 */
const AFTER_START_SEC = 1e-6;

export function glideParam(param: AudioParam, value: number, now: number, timeConstant: number): void {
  param.cancelScheduledValues(now + AFTER_START_SEC);
  param.setTargetAtTime(value, now, timeConstant);
}
