import type { Vec3 } from '../../voicePlanning.js';
import { applyParam } from './paramTransitions.js';
import { POSITION_TIME_CONSTANT_SEC } from './voiceTypes.js';

/** Creates an omnidirectional HRTF PannerNode for directional placement without Web Audio distance rolloff. */
export function createVoicePanner(ctx: BaseAudioContext): PannerNode {
  const panner = ctx.createPanner();
  panner.panningModel = 'HRTF';
  // Distance is rendered by our own gains and filters; the panner only places direction.
  panner.distanceModel = 'inverse';
  panner.refDistance = 1;
  panner.rolloffFactor = 0;
  panner.coneInnerAngle = 360;
  panner.coneOuterAngle = 360;
  return panner;
}

/** Updates a voice panner's listener-local direction via AudioParams or legacy setPosition fallback. */
export function setPannerPosition(
  panner: PannerNode,
  directionListener: Vec3,
  now: number,
  immediate: boolean
): void {
  if (panner.positionX) {
    applyParam(panner.positionX, directionListener[0], now, immediate, POSITION_TIME_CONSTANT_SEC);
    applyParam(panner.positionY, directionListener[1], now, immediate, POSITION_TIME_CONSTANT_SEC);
    applyParam(panner.positionZ, directionListener[2], now, immediate, POSITION_TIME_CONSTANT_SEC);
  } else {
    panner.setPosition(directionListener[0], directionListener[1], directionListener[2]);
  }
}
