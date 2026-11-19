import { describe, expect, it } from 'vitest';
import { DRAG_DECAY_PER_60TH, STEER_DRAG_RAD_PER_PX, STEER_KEY_TURN_RAD_S, STEER_LINES, SteerHands, steeringSpeedLine } from '../steering.js';

describe('SteerHands: what the hands do while steering (D26 item 6)', () => {
  it('takes W, S and the arrows, and leaves Space to Ask out loud', () => {
    for (const code of ['KeyW', 'KeyS', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']) expect(SteerHands.takes(code)).toBe(true);
    for (const code of ['Space', 'Escape', 'KeyA', 'KeyD', 'Slash']) expect(SteerHands.takes(code)).toBe(false);
  });

  it('turns at a steady rate while an arrow is held, and changes speed while W or S is', () => {
    const hands = new SteerHands();
    hands.keyDown('ArrowRight');
    hands.keyDown('KeyW');
    expect(hands.frame(1 / 60)).toEqual({ input: { yawRadPerS: STEER_KEY_TURN_RAD_S, pitchRadPerS: 0 }, speedDirection: 1 });
    hands.keyDown('KeyS');
    expect(hands.frame(1 / 60).speedDirection).toBe(0);
    hands.keyUp('KeyW');
    hands.keyUp('ArrowRight');
    hands.keyDown('ArrowUp');
    expect(hands.frame(1 / 60)).toEqual({ input: { yawRadPerS: 0, pitchRadPerS: STEER_KEY_TURN_RAD_S }, speedDirection: -1 });
  });

  it('turns with a drag exactly as far as the hand moved, then slows by k10 after release', () => {
    const hands = new SteerHands();
    hands.drag(-100, 20, 1 / 60);
    const dt = 1 / 60;
    const f = hands.frame(dt);
    // Turned in this frame: rate times dt is the drag's own angle
    expect(f.input.yawRadPerS * dt).toBeCloseTo(100 * STEER_DRAG_RAD_PER_PX, 12);
    expect(f.input.pitchRadPerS * dt).toBeCloseTo(20 * STEER_DRAG_RAD_PER_PX, 12);
    // Held still: no turn
    expect(hands.frame(dt).input.yawRadPerS).toBe(0);
    hands.drag(-10, 0, 1 / 60);
    hands.frame(dt);
    hands.release();
    const lastRate = (10 * STEER_DRAG_RAD_PER_PX) / (1 / 60);
    expect(hands.frame(dt).input.yawRadPerS).toBeCloseTo(lastRate, 9);
    expect(hands.frame(dt).input.yawRadPerS).toBeCloseTo(lastRate * DRAG_DECAY_PER_60TH, 9);
    // A frame twice as long slows it twice over: the same rule at any frame rate
    const other = new SteerHands();
    other.drag(-10, 0, 1 / 60);
    other.frame(dt);
    other.release();
    other.frame(1 / 30);
    expect(other.frame(1 / 30).input.yawRadPerS).toBeCloseTo(lastRate * DRAG_DECAY_PER_60TH ** 2, 9);
    // About 0.22 s later it has nearly settled, at any frame rate
    let rate = 0;
    for (let i = 0; i < 26; i++) rate = hands.frame(1 / 120).input.yawRadPerS;
    expect(rate).toBeLessThan(lastRate * 0.1);
  });
});

describe('the words while steering', () => {
  it('lists the four lines and the flight\'s own speed', () => {
    expect(STEER_LINES).toEqual(['W and S: faster and slower', 'Drag or arrow keys: turn', 'Press the sky: autopilot again', 'Esc: Back to Moment']);
    expect(steeringSpeedLine(1 / 3.261563777)).toBe('this flight: 1.00 ly each second');
    expect(steeringSpeedLine(10)).toBe('this flight: 32.6 ly each second');
  });
});
