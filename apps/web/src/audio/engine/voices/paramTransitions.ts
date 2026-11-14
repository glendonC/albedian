import { glideParam, setParamNow } from '../createAudioContext.js';
import { PARAM_TIME_CONSTANT_SEC, type VoiceGenerators } from './voiceTypes.js';

/** Sets an AudioParam immediately on a clean slot or glides exponentially when live audio is present. */
export function applyParam(
  param: AudioParam,
  value: number,
  now: number,
  immediate: boolean,
  timeConstantSec: number = PARAM_TIME_CONSTANT_SEC
): void {
  if (immediate) {
    setParamNow(param, value, now);
  } else {
    glideParam(param, value, now, timeConstantSec);
  }
}

/** Schedules a source node to stop, ignoring repeat stops, and optionally wires an ended cleanup callback. */
export function stopScheduledSource(
  node: AudioScheduledSourceNode,
  when: number,
  onEnded?: () => void
): void {
  try {
    node.stop(when);
  } catch {
    // already stopped
  }
  if (onEnded) node.onended = onEnded;
}

/** Stops every active generator in a voice bundle at `when`, disconnecting its envelope once ended. */
export function stopVoiceGenerators(generators: VoiceGenerators, when: number): void {
  const nodes: AudioScheduledSourceNode[] = [];
  if (generators.star) nodes.push(generators.star.osc);
  if (generators.uncertainty) nodes.push(generators.uncertainty.lower, generators.uncertainty.upper);
  if (generators.pulsar) nodes.push(generators.pulsar.noise, generators.pulsar.envelopeOsc);
  nodes.forEach((node, i) =>
    stopScheduledSource(node, when, i === 0 ? () => generators.envelope.disconnect() : undefined)
  );
}
