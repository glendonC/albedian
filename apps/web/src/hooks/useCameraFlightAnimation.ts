import { useCallback, useEffect, useRef } from 'react';
import { ObservationSession } from '@albedian/contracts';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { computeStarAnchorNdc } from '@albedian/observation';

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

      const endOrigin: [number, number, number] = [
        Number((targetParsec[0] * 0.45).toFixed(3)),
        Number((targetParsec[1] * 0.45).toFixed(3)),
        Number((targetParsec[2] * 0.45).toFixed(3)),
      ];

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
          lastStatusBanner: `Catalog Geometry · Distance Estimates: Arrived in 3D neighborhood of ${target.title}.`,
        }));
        return;
      }

      const startOrigin = [...session.spatialView.observerOriginParsec] as [number, number, number];
      const startTime = performance.now();
      const phaseADurationMs = 620;
      const phaseBDurationMs = 1550;
      const totalDurationMs = phaseADurationMs + phaseBDurationMs;

      const step = (now: number) => {
        const elapsed = now - startTime;
        if (elapsed < phaseADurationMs) {
          const pA = Math.min(1, elapsed / phaseADurationMs);
          const easeA = pA < 0.5 ? 2 * pA * pA : 1 - Math.pow(-2 * pA + 2, 2) / 2;
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
            },
            lastStatusBanner: `Phase A (Anchor Lock ≤ 0.5 px): Registering ${target.title} against WCS sky…`,
          }));
          flightRafRef.current = window.requestAnimationFrame(step);
        } else if (elapsed < totalDurationMs) {
          const pB = Math.min(1, (elapsed - phaseADurationMs) / phaseBDurationMs);
          const easeB = 1 - Math.pow(1 - pB, 3);
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
            lastStatusBanner: `Phase B (3D Parsec Flight): Flying through Gaia DR3 catalog geometry toward ${target.title} (${(easeB * 100).toFixed(0)}%)…`,
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
            lastStatusBanner: `Catalog Geometry · Distance Estimates: Arrived in 3D neighborhood of ${target.title}. Drag to orbit or scroll to dolly.`,
          }));
          flightRafRef.current = null;
        }
      };

      flightRafRef.current = window.requestAnimationFrame(step);
    },
    [session, setSession]
  );

  return { animatePhaseAToPhaseBSpaceFlight };
}
