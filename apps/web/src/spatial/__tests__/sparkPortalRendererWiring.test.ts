import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { julianDateUtc } from '../bodyOrientation.js';
import { defaultObserverLook, observerAxes, observerFrameQuaternion } from '../observerPose.js';
import { SparkPortalRenderer } from '../SparkPortalRenderer.js';

/**
 * The WebGL renderer's observer wiring, driven through its public methods. Under Node there is no
 * WebGL, so the constructor's own fallback leaves the renderer without a drawing context; the
 * scene graph, the camera's parent and the horizon are built all the same, which is what this
 * test reads.
 */

interface Internals {
  localFrame: THREE.Group;
  lookTarget: { yaw: number; pitch: number; curYaw: number; curPitch: number };
  observerHorizon: THREE.Group | null;
  flightState: { startQuat: THREE.Quaternion; endQuat: THREE.Quaternion; targetRegime: string } | null;
}

// Siding Spring Observatory and a February night: test values, not any recording's
const SITE = { latDeg: -31.277, lonDeg: 149.0661 };
const CAPTURED_AT = '2027-02-06T17:00:00Z';
const FRAME = observerFrameQuaternion(observerAxes(SITE, julianDateUtc(CAPTURED_AT)));
const IDENTITY = new THREE.Quaternion();

function makeRenderer(): { r: SparkPortalRenderer; i: Internals } {
  const container = { clientWidth: 800, clientHeight: 450, appendChild() {} } as unknown as HTMLElement;
  const r = new SparkPortalRenderer(container);
  return { r, i: r as unknown as Internals };
}

const near = (a: THREE.Quaternion, b: THREE.Quaternion) => Math.abs(Math.abs(a.dot(b)) - 1) < 1e-12;
const horizonRow = (r: SparkPortalRenderer) => r.getRenderLayers()?.find((l) => l.kind === 'sky_overlay');

describe('the WebGL renderer places, clears and flies the sky view observer', () => {
  it('mounts the horizon on the camera parent and hides it before a place is known', () => {
    const { r, i } = makeRenderer();
    expect(i.observerHorizon).not.toBeNull();
    expect(i.observerHorizon!.parent).toBe(i.localFrame);
    expect(i.observerHorizon!.visible).toBe(false);
    expect(near(i.localFrame.quaternion, IDENTITY)).toBe(true);
    expect(horizonRow(r)).toBeUndefined();
  });

  it('stands the sky for the capture time, opens on the default look and shows the horizon', () => {
    const { r, i } = makeRenderer();
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, CAPTURED_AT);
    expect(near(i.localFrame.quaternion, FRAME)).toBe(true);
    const look = defaultObserverLook(SITE.latDeg);
    expect(i.lookTarget).toMatchObject({ yaw: look.yaw, pitch: look.pitch, curYaw: look.yaw, curPitch: look.pitch });
    expect(i.observerHorizon!.visible).toBe(true);
    const row = horizonRow(r)!;
    expect(row.evidenceClass).toBe('predicted');
    expect(row.disclosure).toMatch(/at its capture time/);
    // No place, time or figure of the recording reaches the layers' text
    expect(`${row.title} ${row.disclosure}`).not.toMatch(/[0-9]/);
    const text = JSON.stringify(r.getRenderLayers());
    for (const piece of ['31.2', '149.0', '2027', '02-06', '17:00']) expect(text, piece).not.toContain(piece);
  });

  it("stands the sky for the device's time now when the recording has no capture time", () => {
    const { r, i } = makeRenderer();
    const before = Date.now();
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, null);
    const now = observerFrameQuaternion(observerAxes(SITE, before / 86_400_000 + 2440587.5));
    // Within a second of the call: the sky turns 0.004 deg a second
    expect(i.localFrame.quaternion.angleTo(now)).toBeLessThan(1e-3);
    expect(horizonRow(r)!.disclosure).toMatch(/\bnow\b/);
  });

  it('keeps the viewer turned where they are when the same place and time come again', () => {
    const { r, i } = makeRenderer();
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, CAPTURED_AT);
    r.nudgeLook(0.4, -0.2);
    const turned = { ...i.lookTarget };
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, CAPTURED_AT);
    expect(i.lookTarget).toEqual(turned);
  });

  it('forgets the place and time when cleared: celestial north up, no horizon, no horizon layer', () => {
    const { r, i } = makeRenderer();
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, CAPTURED_AT);
    r.clearObserverCoordinates();
    expect(near(i.localFrame.quaternion, IDENTITY)).toBe(true);
    expect(i.observerHorizon!.visible).toBe(false);
    expect(horizonRow(r)).toBeUndefined();
    expect(r.getRenderLayers()!.every((l) => l.evidenceClass === 'simulated')).toBe(true);
    // Clearing twice changes nothing, and the place can come back
    r.clearObserverCoordinates();
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, CAPTURED_AT);
    expect(near(i.localFrame.quaternion, FRAME)).toBe(true);
    expect(i.observerHorizon!.visible).toBe(true);
  });

  it('turns the parent only in the sky view: modelled scenes keep their axes and hide the horizon', () => {
    const { r, i } = makeRenderer();
    r.jumpToRegime('cislunar');
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, CAPTURED_AT);
    expect(near(i.localFrame.quaternion, IDENTITY)).toBe(true);
    expect(i.observerHorizon!.visible).toBe(false);
    r.jumpToRegime('earth_capture');
    expect(near(i.localFrame.quaternion, FRAME)).toBe(true);
    expect(i.observerHorizon!.visible).toBe(true);
    const look = defaultObserverLook(SITE.latDeg);
    expect([i.lookTarget.yaw, i.lookTarget.pitch]).toEqual([look.yaw, look.pitch]);
    r.jumpToRegime('deep_sky');
    expect(near(i.localFrame.quaternion, IDENTITY)).toBe(true);
    expect(i.observerHorizon!.visible).toBe(false);
  });

  it('flies the parent from the observer frame to the modelled axes and back, horizon hidden in flight', () => {
    const { r, i } = makeRenderer();
    r.setObserverCoordinates(SITE.latDeg, SITE.lonDeg, CAPTURED_AT);
    r.stepThroughPortal(1);
    expect(i.flightState!.targetRegime).toBe('cislunar');
    expect(near(i.flightState!.startQuat, FRAME)).toBe(true);
    expect(near(i.flightState!.endQuat, IDENTITY)).toBe(true);
    expect(i.observerHorizon!.visible).toBe(false);
    r.jumpToRegime('cislunar');
    r.stepThroughPortal(-1);
    expect(i.flightState!.targetRegime).toBe('earth_capture');
    expect(near(i.flightState!.startQuat, IDENTITY)).toBe(true);
    expect(near(i.flightState!.endQuat, FRAME)).toBe(true);
    // Without a place the flight back keeps celestial north up
    r.clearObserverCoordinates();
    r.jumpToRegime('cislunar');
    r.stepThroughPortal(-1);
    expect(near(i.flightState!.endQuat, IDENTITY)).toBe(true);
  });
});
