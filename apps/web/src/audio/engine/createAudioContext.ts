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

export function glideParam(param: AudioParam, value: number, now: number, timeConstant: number): void {
  param.cancelScheduledValues(now);
  param.setTargetAtTime(value, now, timeConstant);
}
