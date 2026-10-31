import { useCallback, useEffect, useRef, useState } from 'react';
import { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';

/**
 * Deterministic, cancellable Guided Sky Tour director (`Reveal` -> `Register` -> `Explore 3D` -> `Back to Moment`).
 * Works dynamically on any observation session (Sahara Orion, any discovered field video, or imported media).
 */
export function useGuidedSkyTour(
  session: ObservationSession,
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>,
  animatePhaseAToPhaseBSpaceFlight: (findingId: string) => void,
  onPlayChime?: () => void
) {
  const [isTourActive, setIsTourActive] = useState(false);
  const timersRef = useRef<number[]>([]);
  const rafRef = useRef<number | null>(null);
  const sessionRef = useRef(session);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  const stopGuidedTour = useCallback(() => {
    for (const id of timersRef.current) {
      window.clearTimeout(id);
    }
    timersRef.current = [];
    if (rafRef.current) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setIsTourActive(false);
  }, []);

  useEffect(() => {
    return () => stopGuidedTour();
  }, [stopGuidedTour]);

  const startGuidedTour = useCallback(() => {
    stopGuidedTour();
    setIsTourActive(true);
    onPlayChime?.();

    const cur = sessionRef.current;
    // Choose a premier target star with a measured/inferred parsec distance for the 3D flight
    const starTarget =
      cur.observationSolution.findings.find(
        (f) =>
          (f.inferredDistancePc?.median ?? 0) > 1.0 &&
          (f.evidenceClass === 'catalog_matched' || Boolean(f.sourceDetection))
      ) ??
      cur.observationSolution.findings.find((f) => (f.inferredDistancePc?.median ?? 0) > 1.0) ??
      cur.observationSolution.findings[0];

    const beaconTarget =
      cur.observationSolution.findings.find((f) => f.evidenceClass === 'observed') ?? starTarget;

    // Step 1 (0.0s - 2.6s): Enter Compare A/B Wipe on Motion-Compensated Temporal Stack & sweep wipe handle
    setSession((prev) => {
      const cmp = dispatchAlbedianAction(prev, {
        type: 'media.compare',
        requestId: `tour_cmp_${Date.now()}`,
        expectedViewRevision: prev.viewRevision,
        variantAId: 'variant_original',
        variantBId: 'variant_temporal',
        preserveTime: true,
      }).session;
      return {
        ...cmp,
        activeVariantId: 'variant_temporal',
        compareWipePosition: 0.78,
        lastStatusBanner:
          'Guided Sky Tour (1/5 · Reveal): Comparing immutable Original Recording against Motion-Compensated Temporal Stack…',
      };
    });

    const wipeStart = performance.now();
    const wipeDuration = 2300;
    const stepWipe = (now: number) => {
      const t = Math.min(1, (now - wipeStart) / wipeDuration);
      const pos = 0.78 - Math.sin(t * Math.PI) * 0.56;
      setSession((prev) => ({
        ...prev,
        compareWipePosition: Number(pos.toFixed(3)),
      }));
      if (t < 1) {
        rafRef.current = window.requestAnimationFrame(stepWipe);
      }
    };
    rafRef.current = window.requestAnimationFrame(stepWipe);

    // Step 2 (2.6s - 4.8s): Register Sky & highlight primary observed beacon
    const t2 = window.setTimeout(() => {
      onPlayChime?.();
      setSession((prev) => {
        const sky = dispatchAlbedianAction(prev, {
          type: 'sky.show',
          requestId: `tour_sky_${Date.now()}`,
          expectedViewRevision: prev.viewRevision,
          mode: prev.observationSolution.status === 'calibrated' ? 'measured' : 'predicted',
        }).session;
        return {
          ...sky,
          selectedFindingId: beaconTarget?.id ?? sky.selectedFindingId,
          lastStatusBanner: `Guided Sky Tour (2/5 · Register): Locked WCS sky geometry onto ${
            beaconTarget?.title ?? 'field beacon'
          } and surrounding constellations.`,
        };
      });
    }, 2600);

    // Step 3 (4.8s - 6.6s): Select deep-sky / catalog star target for 3D flight
    const t3 = window.setTimeout(() => {
      if (!starTarget) return;
      onPlayChime?.();
      setSession((prev) => {
        const sel = dispatchAlbedianAction(prev, {
          type: 'finding.select',
          requestId: `tour_sel_${Date.now()}`,
          expectedViewRevision: prev.viewRevision,
          findingId: starTarget.id,
        }).session;
        const distPc = starTarget.inferredDistancePc?.median ?? 25.0;
        return {
          ...sel,
          lastStatusBanner: `Guided Sky Tour (3/5 · Target Lock): Selected ${starTarget.title} at ${distPc.toFixed(
            1
          )} parsecs (${(distPc * 3.26156).toFixed(0)} light-years). Preparing 3D flight…`,
        };
      });
    }, 4800);

    // Step 4 (6.6s - 10.6s): Launch 60fps Phase A -> Phase B 3D Parsec Flight
    const t4 = window.setTimeout(() => {
      if (!starTarget) return;
      onPlayChime?.();
      setSession((prev) => {
        const entered = dispatchAlbedianAction(prev, {
          type: 'space.enter',
          requestId: `tour_space_${Date.now()}`,
          expectedViewRevision: prev.viewRevision,
          findingId: starTarget.id,
          distancePolicy: 'measured_or_estimated',
        }).session;
        return entered;
      });
      animatePhaseAToPhaseBSpaceFlight(starTarget.id);
    }, 6600);

    // Step 5 (10.8s): Return to Moment (or leave in 3D space if desired; here we complete the reversible loop)
    const t5 = window.setTimeout(() => {
      onPlayChime?.();
      setSession((prev) => {
        const ret = dispatchAlbedianAction(prev, {
          type: 'source.return',
          requestId: `tour_ret_${Date.now()}`,
          expectedViewRevision: prev.viewRevision,
          restoreAudio: true,
        }).session;
        return {
          ...ret,
          lastStatusBanner:
            'Guided Sky Tour Complete (5/5 · Back to Moment): Restored exact source recording frame, timestamp, and audio state.',
        };
      });
      setIsTourActive(false);
    }, 11200);

    timersRef.current.push(t2, t3, t4, t5);
  }, [animatePhaseAToPhaseBSpaceFlight, onPlayChime, setSession, stopGuidedTour]);

  return {
    isTourActive,
    startGuidedTour,
    stopGuidedTour,
  };
}
