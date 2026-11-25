import { useCallback, useEffect, useRef } from 'react';
import { ObservationSession } from '@albedian/contracts';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { arrivalDistanceText, computeStarAnchorNdc, flightEndOrigin, returnCheckpointOf } from '@albedian/observation';
import { MOTION_TOKENS } from '@albedian/design-system';

/**
 * The flight is one move each way (flight-anim-one, DECIDED-BY-LEAD 24 Nov; MOTION-READY-web.md section 2): one progress
 * over the two camera halves, 1.5 s, as NASA's Eyes flies to a body [verified: eyes.nasa.gov/apps/solar-system/app.js,
 * goToCelestialObject], on the easing MapLibre's flyTo uses [verified: maplibre-gl-js src/util/util.ts defaultEasing,
 * bezier(0.25, 0.1, 0.25, 1), read 24 Nov]. The photo becomes the registered sky on the first half of the progress with
 * the camera still; the travel runs on the second half in log distance (van Wijk and Nuij, "Smooth and efficient zooming
 * and panning", 2003: scale is felt in its log).
 */
export const FLIGHT_MS = 2 * MOTION_TOKENS.cameraHalfMs;

/** cubic-bezier(0.25, 0.1, 0.25, 1) at x in [0, 1], by Newton's method on x(t) then bisection where that stalls. */
export function flightCurve(x: number): number {
  const t0 = Math.min(1, Math.max(0, x));
  if (t0 === 0 || t0 === 1) return t0;
  const bx = (t: number) => 3 * (1 - t) ** 2 * t * 0.25 + 3 * (1 - t) * t ** 2 * 0.25 + t ** 3;
  const by = (t: number) => 3 * (1 - t) ** 2 * t * 0.1 + 3 * (1 - t) * t ** 2 + t ** 3;
  // dx/dt with both x control points at 0.25: 3 (0.25)(1 - t)^2 + 3 (1 - 0.25) t^2
  const dx = (t: number) => 0.75 * (1 - t) ** 2 + 2.25 * t ** 2;
  let t = t0;
  for (let i = 0; i < 8; i++) {
    const err = bx(t) - t0;
    if (Math.abs(err) < 1e-7) return by(t);
    const d = dx(t);
    if (Math.abs(d) < 1e-6) break;
    t = Math.min(1, Math.max(0, t - err / d));
  }
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    if (bx(mid) < t0) lo = mid;
    else hi = mid;
  }
  return by((lo + hi) / 2);
}

/** When the travel starts: where the curve reaches half its progress (440 ms of the 1500), solved once from the curve. */
export const FLIGHT_TRAVEL_START_MS = (() => {
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 50; i++) {
    const mid = (lo + hi) / 2;
    if (flightCurve(mid) < 0.5) lo = mid;
    else hi = mid;
  }
  return ((lo + hi) / 2) * FLIGHT_MS;
})();

/**
 * The travel's remainder, whole milliseconds (1060): the there screen mounts as the travel starts, so its words that
 * wait for the camera to rest (Back to Moment, the ask controls; AskBar) wait this long after it, to 1500 ms after the press.
 */
export const FLIGHT_REST_MS = Math.round(FLIGHT_MS - FLIGHT_TRAVEL_START_MS);

/** The camera on the line from `from` to `to`, its distance to the star moving in log distance with q (0 at from, 1 at to). */
function logDistanceOrigin(from: readonly number[], to: readonly number[], star: readonly number[], q: number): [number, number, number] {
  if (q <= 0) return [from[0]!, from[1]!, from[2]!];
  if (q >= 1) return [to[0]!, to[1]!, to[2]!];
  const d0 = Math.hypot(from[0]! - star[0]!, from[1]! - star[1]!, from[2]! - star[2]!);
  const d1 = Math.hypot(to[0]! - star[0]!, to[1]! - star[1]!, to[2]! - star[2]!);
  if (d0 <= 0 || d1 <= 0) return [0, 1, 2].map((i) => Number((from[i]! + (to[i]! - from[i]!) * q).toFixed(3))) as [number, number, number];
  // The point on the segment whose distance to the star is d0 (d1 / d0)^q, as a share of the segment
  const share = (d0 - d0 * Math.pow(d1 / d0, q)) / (d0 - d1 || 1);
  return [0, 1, 2].map((i) => Number((from[i]! + (to[i]! - from[i]!) * share).toFixed(4))) as [number, number, number];
}

/**
 * The one camera flight on screen, there or back, whichever hook started it: the app's flight there and the reveal's
 * return share the camera, so a new one stops the one before (a press of Back to Moment while still flying there).
 */
const camera: { raf: number | null; returning: boolean } = { raf: null, returning: false };

/**
 * Smooth 60fps `requestAnimationFrame` camera flight hook for Phase A (off-axis anchor lock)
 * and Phase B (3D parsec starfield flight), respecting `session.reducedMotion`.
 */
export function useCameraFlightAnimation(
  session: ObservationSession,
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>
) {
  const flightRafRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (flightRafRef.current) {
        window.cancelAnimationFrame(flightRafRef.current);
        if (camera.raf === flightRafRef.current) {
          camera.raf = null;
          camera.returning = false;
        }
      }
    };
  }, []);
  const frame = (step: FrameRequestCallback) => {
    flightRafRef.current = window.requestAnimationFrame(step);
    camera.raf = flightRafRef.current;
  };

  const animatePhaseAToPhaseBSpaceFlight = useCallback(
    (targetFindingId: string) => {
      const findings = session.observationSolution?.findings ?? [];
      // Only the finding asked for: an id that names none flies nowhere
      const target = findings.find((f) => f.id === targetFindingId);
      if (!target) return;

      // The flight needs a measured 3D position: no direction or distance is assumed
      const targetParsec = resolveFindingParsecPosition(target);
      if (!targetParsec) return;

      if (camera.raf) window.cancelAnimationFrame(camera.raf);
      camera.raf = null;
      camera.returning = false;

      // One light year short of the star, on the line from where the camera is now
      const startOrigin = [...session.spatialView.observerOriginParsec] as [number, number, number];
      const endOrigin = flightEndOrigin(startOrigin, targetParsec);
      // One light year by design, or how far it already was when the camera started closer (the reducer says the same)
      const arrival = arrivalDistanceText(startOrigin, targetParsec);

      // The same anchor the reducer locks on selection: the detection as the upright photo shows it
      const anchorNdc = computeStarAnchorNdc(target, session.sourceAsset);

      if (session.reducedMotion) {
        setSession((prev) => ({
          ...prev,
          selectedFindingId: target.id,
          machines: { ...prev.machines, navigation: 'space', exploration: 'space_3d' },
          spatialView: {
            ...prev.spatialView,
            mode: 'catalog_3d',
            transitionPhase: 'at_space',
            transitionProgress: 1.0,
            observerOriginParsec: endOrigin,
            fovVerticalDeg: 45.0,
            frustumShiftNdc: [0, 0],
            activeOverlays: {
              ...prev.spatialView.activeOverlays,
              distanceVectors: true,
              earthDirectionVector: true,
              constellationLines: true,
            },
          },
          lastStatusBanner: `Catalog Geometry · Distance Estimates: ${arrival} from ${target.title}.`,
        }));
        return;
      }

      const startTime = performance.now();

      const step = (now: number) => {
        const elapsed = now - startTime;
        // One progress for the whole move: the photo to sky on its first half, the travel on its second
        const u = flightCurve(elapsed / FLIGHT_MS);
        if (elapsed < FLIGHT_MS && u < 0.5) {
          const easeA = u / 0.5;
          setSession((prev) => ({
            ...prev,
            selectedFindingId: target.id,
            machines: { ...prev.machines, navigation: 'sky_registered', exploration: 'calibrated_sky' },
            spatialView: {
              ...prev.spatialView,
              mode: 'registered_sky',
              transitionPhase: 'phase_a_photo_to_sky',
              transitionProgress: Number(easeA.toFixed(3)),
              lockedAnchorNdc: anchorNdc,
              // The command may already have moved the camera to the flight's end, one light year short of
              // the star: in this phase it stays where the flight starts
              observerOriginParsec: startOrigin,
            },
            lastStatusBanner: `Phase A: registering ${target.title} against the calibrated sky…`,
          }));
          frame(step);
        } else if (elapsed < FLIGHT_MS) {
          const easeB = (u - 0.5) / 0.5;
          const curOrigin = logDistanceOrigin(startOrigin, endOrigin, targetParsec, easeB);
          setSession((prev) => ({
            ...prev,
            selectedFindingId: target.id,
            machines: { ...prev.machines, navigation: 'space', exploration: 'space_3d' },
            spatialView: {
              ...prev.spatialView,
              mode: 'catalog_3d',
              transitionPhase: 'phase_b_sky_to_space',
              transitionProgress: Number(easeB.toFixed(3)),
              observerOriginParsec: curOrigin,
              fovVerticalDeg: Number((37.8 + (45.0 - 37.8) * easeB).toFixed(2)),
              frustumShiftNdc: [
                Number((anchorNdc[0] * (1 - easeB) * 0.25).toFixed(4)),
                Number((anchorNdc[1] * (1 - easeB) * 0.25).toFixed(4)),
              ],
              activeOverlays: {
                ...prev.spatialView.activeOverlays,
                distanceVectors: true,
                earthDirectionVector: true,
                constellationLines: true,
              },
            },
            lastStatusBanner: `Phase B: flying toward ${target.title} through catalog geometry…`,
          }));
          frame(step);
        } else {
          setSession((prev) => ({
            ...prev,
            selectedFindingId: target.id,
            machines: { ...prev.machines, navigation: 'space', exploration: 'space_3d' },
            spatialView: {
              ...prev.spatialView,
              mode: 'catalog_3d',
              transitionPhase: 'at_space',
              transitionProgress: 1.0,
              observerOriginParsec: endOrigin,
              fovVerticalDeg: 45.0,
              frustumShiftNdc: [0, 0],
            },
            lastStatusBanner: `Catalog Geometry · Distance Estimates: ${arrival} from ${target.title}.`,
          }));
          flightRafRef.current = null;
          camera.raf = null;
        }
      };

      // The first frame now, so the panel never shows the state the command left (the camera already at
      // the flight's end, one light year short of the star, before it has flown)
      step(startTime);
    },
    [session, setSession]
  );

  /**
   * Back to Moment (the motion record, "Go there", Back to Moment; one move since flight-anim-one): the flight's one curve
   * reversed over the same 1.5 s, u = 1 - curve(t): the travel back first (u 1 to 0.5), the camera from where it is to
   * where the flight began (the moment's own camera) in log distance to the star, then the registered sky back to the
   * photo (u 0.5 to 0); then `restore`, the reducer's source.return, which restores the CameraCheckpoint exactly as
   * before. Reduced motion: `restore` at once, nothing travels. A press while it flies back does nothing.
   */
  const animateReturnToMoment = useCallback(
    (restore: () => void) => {
      if (camera.returning) return;
      if (camera.raf) window.cancelAnimationFrame(camera.raf);
      camera.raf = null;
      if (session.reducedMotion) {
        restore();
        return;
      }
      camera.returning = true;
      const from = [...session.spatialView.observerOriginParsec] as [number, number, number];
      const home = [...(returnCheckpointOf(session)?.spatialView.observerOriginParsec ?? from)] as [number, number, number];
      const anchorNdc = (session.spatialView.lockedAnchorNdc ?? [0, 0]) as [number, number];
      // The star flown to, for the travel back in log distance; without a measured position the camera returns in a line
      const picked = session.observationSolution?.findings.find((f) => f.id === session.selectedFindingId);
      const star = picked ? resolveFindingParsecPosition(picked) : null;
      const startTime = performance.now();
      const step = (now: number) => {
        const elapsed = now - startTime;
        // u is the place on the flight there, from its end (1) back to its start (0)
        const u = 1 - flightCurve(elapsed / FLIGHT_MS);
        if (elapsed < FLIGHT_MS && u >= 0.5) {
          const q = (u - 0.5) / 0.5;
          const origin: [number, number, number] =
            q >= 1
              ? from
              : star
                ? logDistanceOrigin(home, from, star, q)
                : ([0, 1, 2].map((i) => Number((home[i]! + (from[i]! - home[i]!) * q).toFixed(3))) as [number, number, number]);
          setSession((prev) => ({
            ...prev,
            machines: { ...prev.machines, navigation: 'space', exploration: 'space_3d' },
            spatialView: {
              ...prev.spatialView,
              mode: 'catalog_3d',
              transitionPhase: 'phase_b_sky_to_space',
              transitionProgress: Number(q.toFixed(3)),
              observerOriginParsec: origin,
              fovVerticalDeg: Number((37.8 + (45.0 - 37.8) * q).toFixed(2)),
              frustumShiftNdc: [Number((anchorNdc[0] * (1 - q) * 0.25).toFixed(4)), Number((anchorNdc[1] * (1 - q) * 0.25).toFixed(4))],
            },
          }));
          frame(step);
        } else if (elapsed < FLIGHT_MS) {
          setSession((prev) => ({
            ...prev,
            machines: { ...prev.machines, navigation: 'sky_registered', exploration: 'calibrated_sky' },
            spatialView: {
              ...prev.spatialView,
              mode: 'registered_sky',
              transitionPhase: 'phase_a_photo_to_sky',
              transitionProgress: Number((u / 0.5).toFixed(3)),
              lockedAnchorNdc: anchorNdc,
              observerOriginParsec: home,
            },
          }));
          frame(step);
        } else {
          flightRafRef.current = null;
          camera.raf = null;
          camera.returning = false;
          restore();
        }
      };
      step(startTime);
    },
    [session, setSession]
  );

  return { animatePhaseAToPhaseBSpaceFlight, animateReturnToMoment };
}
