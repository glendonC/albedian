import { describe, expect, it } from 'vitest';
import { DRAG_BANK_TAU_S, DRAG_DECAY_PER_60TH, DRAG_FULL_BANK_PX_S, STEER_DRAG_RAD_PER_PX, STEER_KEY_TURN_RAD_S, STEER_LINES, SteerHands, steeringSpeedLine } from '../steering.js';

describe('SteerHands: what the hands do while steering (D26 item 6)', () => {
  it('takes W, S and the arrows, and leaves Space to Ask out loud', () => {
    for (const code of ['KeyW', 'KeyS', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']) expect(SteerHands.takes(code)).toBe(true);
    for (const code of ['Space', 'Escape', 'KeyA', 'KeyD', 'Slash']) expect(SteerHands.takes(code)).toBe(false);
  });

  it('turns at a steady rate while an arrow is held, and changes speed while W or S is', () => {
    const hands = new SteerHands();
    hands.keyDown('ArrowRight');
    hands.keyDown('KeyW');
    expect(hands.frame(1 / 60)).toEqual({ input: { yawRadPerS: STEER_KEY_TURN_RAD_S, pitchRadPerS: 0 }, dragInput: { yawRadPerS: 0, pitchRadPerS: 0 }, speedDirection: 1, turnShare: 1, dragHeld: false });
    hands.keyDown('KeyS');
    expect(hands.frame(1 / 60).speedDirection).toBe(0);
    hands.keyUp('KeyW');
    hands.keyUp('ArrowRight');
    hands.keyDown('ArrowUp');
    expect(hands.frame(1 / 60)).toEqual({ input: { yawRadPerS: 0, pitchRadPerS: STEER_KEY_TURN_RAD_S }, dragInput: { yawRadPerS: 0, pitchRadPerS: 0 }, speedDirection: -1, turnShare: 0, dragHeld: false });
  });

  // phone-orbit-one: in a flight to a star the drag goes round the star and the keys turn, so the drag's part is its own
  it('gives the drag\'s own part of the turn apart from the keys\', its carry after the release with it', () => {
    const hands = new SteerHands();
    const dt = 1 / 60;
    hands.keyDown('ArrowLeft');
    hands.drag(-60, 30, dt);
    const f = hands.frame(dt);
    expect(f.dragInput.yawRadPerS * dt).toBeCloseTo(60 * STEER_DRAG_RAD_PER_PX, 12);
    expect(f.dragInput.pitchRadPerS * dt).toBeCloseTo(30 * STEER_DRAG_RAD_PER_PX, 12);
    // the whole turn is the keys' and the drag's together
    expect(f.input.yawRadPerS).toBeCloseTo(f.dragInput.yawRadPerS - STEER_KEY_TURN_RAD_S, 9);
    hands.release();
    const carry = hands.frame(dt);
    expect(carry.dragInput.yawRadPerS).toBeCloseTo((60 * STEER_DRAG_RAD_PER_PX) / dt, 9);
    expect(carry.input.yawRadPerS - carry.dragInput.yawRadPerS).toBeCloseTo(-STEER_KEY_TURN_RAD_S, 9);
    // Under Reduce Motion no carry, in the drag's part as in the whole
    const reduced = new SteerHands(() => true);
    reduced.drag(-60, 0, dt);
    reduced.frame(dt);
    reduced.release();
    expect(reduced.frame(dt).dragInput).toEqual({ yawRadPerS: 0, pitchRadPerS: 0 });
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

  // MOTION-READY-web.md section seven, DECIDED-BY-LEAD 22 Nov 21:50: under Reduce Motion a released steering drag stops
  // at once with no carry, as section six A steps the wheel's glide; the drag itself and the arrow keys are the hand
  it('under Reduce Motion follows the drag exactly, then stops at once when the hand lets go', () => {
    const hands = new SteerHands(() => true);
    const dt = 1 / 60;
    hands.drag(-10, 4, dt);
    const f = hands.frame(dt);
    expect(f.input.yawRadPerS * dt).toBeCloseTo(10 * STEER_DRAG_RAD_PER_PX, 12);
    expect(f.input.pitchRadPerS * dt).toBeCloseTo(4 * STEER_DRAG_RAD_PER_PX, 12);
    hands.release();
    expect(hands.frame(dt).input).toEqual({ yawRadPerS: 0, pitchRadPerS: 0 });
    expect(hands.frame(dt).input).toEqual({ yawRadPerS: 0, pitchRadPerS: 0 });
    hands.keyDown('ArrowLeft');
    expect(hands.frame(dt).input.yawRadPerS).toBe(-STEER_KEY_TURN_RAD_S);
  });

  it('ends a glide already going when Reduce Motion comes on, for good', () => {
    let reduced = false;
    const hands = new SteerHands(() => reduced);
    const dt = 1 / 60;
    hands.drag(-10, 0, dt);
    hands.frame(dt);
    hands.release();
    expect(hands.frame(dt).input.yawRadPerS).toBeCloseTo((10 * STEER_DRAG_RAD_PER_PX) / dt, 9);
    reduced = true;
    expect(hands.frame(dt).input.yawRadPerS).toBe(0);
    reduced = false;
    expect(hands.frame(dt).input.yawRadPerS).toBe(0);
  });
});

// craft-alive-drag-one (DECIDED-BY-LEAD 25 Nov 06:55, MOTION's craft-alive-phone report): a drag banks the craft by its
// own speed, fully at about 600 px/s, followed on a short time constant while the finger is down; the keys unchanged.
describe('the turn\'s share of a full bank (craft-alive-drag-one)', () => {
  /** A drag across at `pxPerS` for `seconds`, one move a frame; the share read at its end. */
  const dragAt = (pxPerS: number, seconds: number, hands = new SteerHands(), dt = 1 / 60) => {
    let share = 0;
    for (let t = 0; t < seconds - 1e-9; t += dt) {
      hands.drag(pxPerS * dt, 0, dt);
      share = hands.frame(dt).turnShare;
    }
    return share;
  };

  it('is MOTION\'s numbers: full at 600 px/s, 1.8 radians a second of turn, followed on 0.08 s', () => {
    expect(DRAG_FULL_BANK_PX_S).toBe(600);
    expect(DRAG_FULL_BANK_PX_S * STEER_DRAG_RAD_PER_PX).toBeCloseTo(1.8, 12);
    expect(DRAG_BANK_TAU_S).toBe(0.08);
  });

  it('is the keys\' share of their turn rate, as before', () => {
    const hands = new SteerHands();
    hands.keyDown('ArrowLeft');
    expect(hands.frame(1 / 60)).toMatchObject({ turnShare: -1, dragHeld: false });
    hands.keyDown('ArrowRight');
    expect(hands.frame(1 / 60).turnShare).toBe(0);
  });

  it('is a held drag\'s own speed over 600 px/s: a third at 200 px/s, two thirds at 400, more than full in a flick', () => {
    // Dragging the sky right turns the view left, as the left arrow does
    expect(dragAt(200, 1)).toBeCloseTo(-1 / 3, 3);
    expect(dragAt(400, 1)).toBeCloseTo(-2 / 3, 3);
    expect(dragAt(DRAG_FULL_BANK_PX_S, 1)).toBeCloseTo(-1, 3);
    expect(dragAt(-DRAG_FULL_BANK_PX_S, 1)).toBeCloseTo(1, 3);
    expect(dragAt(1333, 1)).toBeLessThan(-2);
    // An up and down drag pitches and does not bank
    const hands = new SteerHands();
    hands.drag(0, 10, 1 / 60);
    expect(hands.frame(1 / 60)).toMatchObject({ turnShare: 0, dragHeld: true });
  });

  it('follows the drag\'s speed on its short time constant while the finger is down', () => {
    expect(dragAt(DRAG_FULL_BANK_PX_S, DRAG_BANK_TAU_S, new SteerHands(), 1 / 600)).toBeCloseTo(-(1 - Math.exp(-1)), 2);
  });

  it('holds through a frame with no move in it, rather than dropping to nothing', () => {
    // A phone's moves need not land one to a frame: here one every other frame, at 600 px/s
    const hands = new SteerHands();
    const dt = 1 / 60;
    const shares: number[] = [];
    for (let i = 0; i < 60; i++) {
      if (i % 2 === 0) hands.drag(DRAG_FULL_BANK_PX_S * 2 * dt, 0, 2 * dt);
      shares.push(hands.frame(dt).turnShare);
    }
    // The last half second stays near a full bank, though each frame's own turn is twice it, then none
    for (const s of shares.slice(30)) expect(Math.abs(s + 1)).toBeLessThan(0.15);
  });

  it('comes back to nothing while the finger is held still, and a new drag starts from nothing', () => {
    const hands = new SteerHands();
    dragAt(DRAG_FULL_BANK_PX_S, 0.5, hands);
    let f = hands.frame(1 / 60);
    for (let i = 0; i < 29; i++) f = hands.frame(1 / 60);
    expect(f.dragHeld).toBe(true);
    expect(Math.abs(f.turnShare)).toBeLessThan(0.01);
    const fresh = new SteerHands();
    dragAt(DRAG_FULL_BANK_PX_S, 0.5, fresh);
    fresh.release();
    fresh.drag(-DRAG_FULL_BANK_PX_S / 60, 0, 1 / 60);
    expect(fresh.frame(1 / 60).turnShare).toBeCloseTo(1 - Math.exp(-1 / 60 / DRAG_BANK_TAU_S), 9);
  });

  it('after the release follows the drag\'s carry down by k10, on the drag\'s own scale', () => {
    const hands = new SteerHands();
    const dt = 1 / 60;
    dragAt(DRAG_FULL_BANK_PX_S, 0.5, hands);
    hands.release();
    const f = hands.frame(dt);
    expect(f.dragHeld).toBe(false);
    // The last move's own speed: 10 px in a sixtieth of a second, 600 px/s
    expect(f.turnShare).toBeCloseTo(-1, 9);
    expect(hands.frame(dt).turnShare).toBeCloseTo(-DRAG_DECAY_PER_60TH, 9);
  });

  it('adds a key held while dragging, and has no carry under Reduce Motion', () => {
    const hands = new SteerHands();
    hands.keyDown('ArrowLeft');
    expect(dragAt(DRAG_FULL_BANK_PX_S, 1, hands)).toBeCloseTo(-2, 3);
    const reduced = new SteerHands(() => true);
    dragAt(DRAG_FULL_BANK_PX_S, 0.5, reduced);
    reduced.release();
    expect(reduced.frame(1 / 60).turnShare).toBe(0);
  });
});

describe('the words while steering', () => {
  it('lists the four lines and the flight\'s own speed', () => {
    expect(STEER_LINES).toEqual(['W and S: faster and slower', 'Drag or arrow keys: turn', 'Press the sky: autopilot again', 'Esc: Back to Moment']);
    expect(steeringSpeedLine(1 / 3.261563777)).toBe('this flight: 1.00 ly each second');
    expect(steeringSpeedLine(10)).toBe('this flight: 32.6 ly each second');
  });
});
