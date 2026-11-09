/**
 * The sky view's look pitch limit: just short of straight up and down, so the camera (Euler order
 * YXZ) can face the zenith region, or before a place is known the Magellanic Clouds and the south
 * celestial pole, without flipping over the top.
 */
export const MAX_LOOK_PITCH_RAD = Math.PI / 2 - 0.01;

export function clampLookPitch(pitch: number): number {
  return Math.max(-MAX_LOOK_PITCH_RAD, Math.min(MAX_LOOK_PITCH_RAD, pitch));
}
