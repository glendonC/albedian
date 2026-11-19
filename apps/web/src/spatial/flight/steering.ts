import type { SteerInput } from '@albedian/camera-rig';

/**
 * pilot-1e (D26 item 6, with the lead's answer of 08:55: a press on the flight hands over and takes back the steering,
 * Space stays Ask out loud): what the person's hands do while they steer. W and S change the speed while held, the
 * arrow keys turn at a steady rate, a drag turns with the hand and after release keeps its last turn, slowing by the
 * motion record's rule for drags (k10: a factor of 0.8 per sixtieth of a second, settled in about 0.22 s).
 */

/** Where steering starts: one light year a second [proposed: the record names no starting speed]. */
export const STEER_START_SPEED_PC_S = 1 / 3.261563777;
/** The arrow keys' turn rate, radians per second [proposed]. */
export const STEER_KEY_TURN_RAD_S = 0.6;
/** How far a drag turns, radians per CSS pixel [proposed]. */
export const STEER_DRAG_RAD_PER_PX = 0.003;
/** k10: a released drag's turn slows by this factor every sixtieth of a second. */
export const DRAG_DECAY_PER_60TH = 0.8;

/** D26 item 6's lines while steering, the third reworded for a press on the sky [proposed words: the lead decides]. */
/** D30 item 6: the phone's one steering line, under the first sentence (no keys on a touch screen). */
export const PHONE_STEER_LINE = 'Drag: turn';

export const STEER_LINES = ['W and S: faster and slower', 'Drag or arrow keys: turn', 'Press the sky: autopilot again', 'Esc: Back to Moment'] as const;

const TURN_KEYS: Readonly<Record<string, [number, number]>> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, 1],
  ArrowDown: [0, -1],
};

/** The keys held and the drag, read once a frame. */
export class SteerHands {
  private readonly held = new Set<string>();
  private pendingYaw = 0;
  private pendingPitch = 0;
  private lastYawRate = 0;
  private lastPitchRate = 0;
  private residualYaw = 0;
  private residualPitch = 0;
  private dragging = false;

  /** Whether a key is one steering takes (so the page's own use of it waits while steering). */
  static takes(code: string): boolean {
    return code === 'KeyW' || code === 'KeyS' || code in TURN_KEYS;
  }

  keyDown(code: string): void {
    if (SteerHands.takes(code)) this.held.add(code);
  }

  keyUp(code: string): void {
    this.held.delete(code);
  }

  /** A drag of (dx, dy) CSS pixels, `dt` seconds after the last one: the view turns with the hand. */
  drag(dxPx: number, dyPx: number, dt: number): void {
    this.dragging = true;
    this.residualYaw = 0;
    this.residualPitch = 0;
    const yaw = -dxPx * STEER_DRAG_RAD_PER_PX;
    const pitch = dyPx * STEER_DRAG_RAD_PER_PX;
    this.pendingYaw += yaw;
    this.pendingPitch += pitch;
    const step = Math.max(dt, 1 / 240);
    this.lastYawRate = yaw / step;
    this.lastPitchRate = pitch / step;
  }

  /** The hand lets go: its last turn carries on and slows. */
  release(): void {
    if (!this.dragging) return;
    this.dragging = false;
    this.residualYaw = this.lastYawRate;
    this.residualPitch = this.lastPitchRate;
  }

  /** The turn rates and the speed's direction for a frame of `dt` seconds. */
  frame(dt: number): { input: SteerInput; speedDirection: -1 | 0 | 1 } {
    let yaw = 0;
    let pitch = 0;
    for (const code of this.held) {
      const t = TURN_KEYS[code];
      if (t) {
        yaw += t[0] * STEER_KEY_TURN_RAD_S;
        pitch += t[1] * STEER_KEY_TURN_RAD_S;
      }
    }
    if (dt > 0) {
      // What the hand turned since the last frame, applied in this one
      yaw += this.pendingYaw / dt;
      pitch += this.pendingPitch / dt;
    }
    this.pendingYaw = 0;
    this.pendingPitch = 0;
    if (!this.dragging) {
      yaw += this.residualYaw;
      pitch += this.residualPitch;
      const decay = Math.pow(DRAG_DECAY_PER_60TH, dt * 60);
      this.residualYaw *= decay;
      this.residualPitch *= decay;
    }
    const w = this.held.has('KeyW');
    const s = this.held.has('KeyS');
    return { input: { yawRadPerS: yaw, pitchRadPerS: pitch }, speedDirection: w === s ? 0 : w ? 1 : -1 };
  }
}

const THREE_FIGURES = new Intl.NumberFormat('en-US', { minimumSignificantDigits: 3, maximumSignificantDigits: 3 });

/** D26 item 7: the steering speed, under the Simulated tag. */
export function steeringSpeedLine(speedPcPerS: number): string {
  return `this flight: ${THREE_FIGURES.format(speedPcPerS * 3.261563777)} ly each second`;
}
