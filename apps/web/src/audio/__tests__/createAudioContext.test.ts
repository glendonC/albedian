import { describe, expect, it } from 'vitest';
import { glideParam, setParamNow } from '../engine/createAudioContext';

/**
 * An AudioParam timeline that follows the Web Audio specification's rules for the three calls the
 * engine makes: cancelScheduledValues(t) removes every event at or after t; setValueAtTime(v, t)
 * holds v from t; setTargetAtTime(v, t, tau) moves exponentially toward v from the value the
 * timeline has at t. Without events the parameter reads its default (a gain's is 1). Checked against
 * Chrome on an OfflineAudioContext (AUDIO_ENGINE.md section 10, item 0).
 */
class TimelineParam {
  private events: { type: 'set' | 'target'; time: number; value: number; tau: number }[] = [];
  constructor(private readonly restingValue: number) {}

  cancelScheduledValues(time: number): this {
    this.events = this.events.filter((e) => e.time < time);
    return this;
  }

  setValueAtTime(value: number, time: number): this {
    this.insert({ type: 'set', time, value, tau: 0 });
    return this;
  }

  setTargetAtTime(value: number, time: number, tau: number): this {
    this.insert({ type: 'target', time, value, tau });
    return this;
  }

  /** The value at `time`: the last event at or before it decides, a target starting from the value before it. */
  valueAt(time: number): number {
    let value = this.restingValue;
    let from = { time: Number.NEGATIVE_INFINITY, value };
    let target: { value: number; tau: number; time: number } | null = null;
    for (const e of this.events) {
      if (e.time > time) break;
      const startValue = target ? target.value + (from.value - target.value) * Math.exp(-(e.time - from.time) / target.tau) : from.value;
      if (e.type === 'set') {
        from = { time: e.time, value: e.value };
        target = null;
      } else {
        from = { time: e.time, value: startValue };
        target = { value: e.value, tau: e.tau, time: e.time };
      }
    }
    value = target ? target.value + (from.value - target.value) * Math.exp(-(time - from.time) / target.tau) : from.value;
    return value;
  }

  private insert(event: { type: 'set' | 'target'; time: number; value: number; tau: number }): void {
    const index = this.events.findIndex((e) => e.time > event.time);
    if (index < 0) this.events.push(event);
    else this.events.splice(index, 0, event);
  }
}

/** A fresh gain (default 1) as the model and as the AudioParam the helpers take. */
const gain = () => {
  const model = new TimelineParam(1);
  return { model, param: model as unknown as AudioParam };
};

describe('parameter glides start where the engine set them', () => {
  it('starts a glide from a value set at the same instant, not from the default', () => {
    // The voice envelope (VoicePool assign), the uncertainty layer, the bed level and the master fade
    const { model, param } = gain();
    setParamNow(param, 0, 0);
    glideParam(param, 1, 0, 0.08);
    expect(model.valueAt(0)).toBe(0);
    expect(model.valueAt(0.08)).toBeCloseTo(1 - Math.exp(-1), 9);
  });

  it('keeps a silent bed silent: set to 0, then glided to 0, never sounds', () => {
    const { model, param } = gain();
    setParamNow(param, 0, 2.5);
    glideParam(param, 0, 2.5, 0.35);
    for (const t of [2.5, 2.6, 3, 5]) expect(model.valueAt(t)).toBe(0);
  });

  it('replaces later events with a new glide and continues from the current value', () => {
    const { model, param } = gain();
    setParamNow(param, 0.2, 0);
    glideParam(param, 1, 0, 0.1);
    model.setValueAtTime(0.5, 3);
    const at1 = model.valueAt(1);
    glideParam(param, 0, 1, 0.1);
    expect(model.valueAt(1)).toBeCloseTo(at1, 12);
    expect(model.valueAt(3)).toBeLessThan(0.01);
  });
});
