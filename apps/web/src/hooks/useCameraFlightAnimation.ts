import { useCallback, useEffect, useRef } from 'react';
import { ObservationSession } from '@albedian/contracts';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { arrivalDistanceText, computeStarAnchorNdc, flightEndOrigin } from '@albedian/observation';
import { MOTION_TOKENS } from '@albedian/design-system';

/**
 * Each half of the flight (Phase A, then Phase B) lasts the motion record's camera half, 750 ms: NASA's Eyes flies to
 * a body in 1.5 s as two such halves [verified: eyes.nasa.gov/apps/solar-system/app.js, goToCelestialObject; the
 * MOTION record, .lead/uiux/motion/MOTION-APPROVED.md].
 */
export const FLIGHT_HALF_MS = MOTION_TOKENS.cameraHalfMs;

/** Both halves ease as NASA's Eyes' camera does: 1 - (1 - t)^4, an ease-out quart, clamped to the half. */
export const flightEase = (t: number): number => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 4);

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
      }
    };
  }, []);

  const animatePhaseAToPhaseBSpaceFlight = useCallback(
    (targetFindingId: string) => {
      const findings = session.observationSolution?.findings ?? [];
      // Only the finding asked for: an id that names none flies nowhere
      const target = findings.find((f) => f.id === targetFindingId);
      if (!target) return;

      // The flight needs a measured 3D position: no direction or distance is assumed
      const targetParsec = resolveFindingParsecPosition(target);
      if (!targetParsec) return;

      if (flightRafRef.current) {
        window.cancelAnimationFrame(flightRafRef.current);
      }

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
      const phaseADurationMs = FLIGHT_HALF_MS;
      const phaseBDurationMs = FLIGHT_HALF_MS;
      const totalDurationMs = phaseADurationMs + phaseBDurationMs;

      const step = (now: number) => {
        const elapsed = now - startTime;
        if (elapsed < phaseADurationMs) {
          const pA = Math.min(1, elapsed / phaseADurationMs);
          const easeA = flightEase(pA);
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
          flightRafRef.current = window.requestAnimationFrame(step);
        } else if (elapsed < totalDurationMs) {
          const pB = Math.min(1, (elapsed - phaseADurationMs) / phaseBDurationMs);
          const easeB = flightEase(pB);
          const curOrigin: [number, number, number] = [
            Number((startOrigin[0] + (endOrigin[0] - startOrigin[0]) * easeB).toFixed(3)),
            Number((startOrigin[1] + (endOrigin[1] - startOrigin[1]) * easeB).toFixed(3)),
            Number((startOrigin[2] + (endOrigin[2] - startOrigin[2]) * easeB).toFixed(3)),
          ];
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
          flightRafRef.current = window.requestAnimationFrame(step);
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
        }
      };

      // The first frame now, so the panel never shows the state the command left (the camera already at
      // the flight's end, one light year short of the star, before it has flown)
      step(startTime);
    },
    [session, setSession]
  );

  return { animatePhaseAToPhaseBSpaceFlight };
}
