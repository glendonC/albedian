import React, { useCallback, useEffect } from 'react';
import type { CameraCheckpoint, ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import {
  getEpistemicBadgeDescriptor,
  PARSEC_TO_LIGHT_YEAR,
} from '@albedian/evidence';

export interface FlightAndRestoreHudProps {
  session: ObservationSession;
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>;
}

const POWERS_OF_TEN_RUNGS = [
  { exp: -3, label: '10^-3 pc' },
  { exp: -2, label: '10^-2 pc' },
  { exp: -1, label: '10^-1 pc' },
  { exp: 0, label: '10^0 pc' },
  { exp: 1, label: '10^1 pc' },
  { exp: 2, label: '10^2 pc' },
] as const;

function resolveScaleRungExp(distPc: number): number {
  if (!Number.isFinite(distPc) || distPc <= 0.003) return -3;
  const rawExp = Math.round(Math.log10(distPc));
  return Math.max(-3, Math.min(2, rawExp));
}

function formatTransitionPhaseLabel(
  phase: ObservationSession['spatialView']['transitionPhase']
): string {
  switch (phase) {
    case 'phase_a_photo_to_sky':
      return 'Phase A: Photo to Calibrated Sky';
    case 'at_registered_sky':
      return 'Registered Sky';
    case 'phase_b_sky_to_space':
      return 'Phase B: Calibrated Sky to 3D Catalog Space';
    case 'at_space':
      return 'Arrived in 3D Catalog Space';
    case 'returning_to_source':
      return 'Returning to Source Recording';
    case 'at_source':
    default:
      return 'Source Recording';
  }
}

/**
 * The moment Back to Moment returns to: the latest checkpoint saved while on the source recording
 * (AGENTS.md: leaving the source saves the moment, and Back to Moment restores it, selection included),
 * else the session's anchor.
 */
export function returnCheckpointOf(session: ObservationSession): CameraCheckpoint | undefined {
  const history = session.navigationHistory;
  if (!history) return undefined;
  for (let i = Math.min(history.cursorIndex, history.checkpoints.length - 1); i >= 0; i--) {
    const checkpoint = history.checkpoints[i];
    if (checkpoint && checkpoint.navigationState === 'source') return checkpoint;
  }
  return history.anchorMomentCheckpoint;
}

/**
 * Moments 06 & 08:
 * - Moment 08 (Outer Wilds Ship Log & CameraCheckpoint Return): Persistent
 *   Back to moment (Esc) button and saved CameraCheckpoint summary pill
 *   whenever the user leaves the original source recording.
 * - Moment 06 (SpaceEngine & 100,000 Stars Smooth Flight): Live distance in
 *   light-years and parsecs, Powers of Ten scale rung, transition phase/stop
 *   control, and explicit simulated-class disclosure naming Gaia DR3 and
 *   Hipparcos catalog geometry and line-of-sight distance priors.
 */
export const FlightAndRestoreHud: React.FC<FlightAndRestoreHudProps> = ({
  session,
  setSession,
}) => {
  const navState = session.machines?.navigation ?? 'source';
  const isDepartedFromSource = navState !== 'source';

  const handleReturnToMoment = useCallback(() => {
    setSession((cur) => {
      const checkpoint = returnCheckpointOf(cur);
      return dispatchAlbedianAction(cur, {
        type: 'source.return',
        requestId: `ui-return-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        checkpointId: checkpoint?.id,
        restoreAudio: true,
      }).session;
    });
  }, [setSession]);

  const handleStopFlight = useCallback(() => {
    setSession((cur) =>
      dispatchAlbedianAction(cur, {
        type: 'navigation.cancel',
        requestId: `ui-cancel-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        transitionId: cur.spatialView?.activeTransitionId,
      }).session
    );
  }, [setSession]);

  useEffect(() => {
    if (!isDepartedFromSource) return undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (
        tag === 'input' ||
        tag === 'textarea' ||
        tag === 'select' ||
        target?.isContentEditable
      ) {
        return;
      }
      event.preventDefault();
      handleReturnToMoment();
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isDepartedFromSource, handleReturnToMoment]);

  if (!isDepartedFromSource) {
    return null;
  }

  const checkpoint = returnCheckpointOf(session);
  const spatialView = session.spatialView;
  const transitionPhase = spatialView?.transitionPhase ?? 'at_source';
  const transitionProgress = spatialView?.transitionProgress ?? 1;

  const isFlightOrSpace =
    navState === 'space' ||
    navState === 'transitioning' ||
    spatialView?.mode === 'catalog_3d' ||
    transitionPhase === 'phase_a_photo_to_sky' ||
    transitionPhase === 'phase_b_sky_to_space';

  const isTransitionActive =
    navState === 'transitioning' ||
    transitionPhase === 'phase_a_photo_to_sky' ||
    transitionPhase === 'phase_b_sky_to_space' ||
    Boolean(spatialView?.activeTransitionId && transitionProgress < 1);

  const origin = spatialView?.observerOriginParsec ?? [0, 0, 0];
  const distPc = Math.hypot(origin[0], origin[1], origin[2]);
  const distLy = distPc * PARSEC_TO_LIGHT_YEAR;
  const activeRungExp = resolveScaleRungExp(distPc);
  const simBadge = getEpistemicBadgeDescriptor('simulated');

  return (
    <div
      className="alb-restore-hud-shell"
      data-testid="flight-and-restore-hud"
      data-hud-interactive="true"
      role="region"
      aria-label="Flight and checkpoint controls"
    >
      <div className="alb-restore-hud-bar">
        <button
          type="button"
          className="alb-restore-hud-btn"
          data-testid="back-to-moment-btn"
          onClick={handleReturnToMoment}
        >
          Back to moment (Esc)
        </button>

        {checkpoint && (
          <div
            className="alb-restore-hud-pill"
            data-testid="checkpoint-summary-pill"
          >
            <span className="alb-restore-hud-pill-label">
              Saved checkpoint: {checkpoint.label}
            </span>
            <span className="alb-restore-hud-pill-meta">
              {checkpoint.sourceTimeSec.toFixed(2)}s · audio{' '}
              {Math.round(checkpoint.audioGain * 100)}% ·{' '}
              {checkpoint.playbackWasPlaying ? 'playing' : 'paused'}
            </span>
          </div>
        )}
      </div>

      {isFlightOrSpace && (
        <div
          className="alb-flight-scale-card"
          data-testid="flight-scale-readout"
        >
          <div className="alb-flight-scale-top">
            <div
              className="alb-flight-scale-distance"
              data-testid="flight-distance-readout"
            >
              {distLy.toFixed(2)} ly ({distPc.toFixed(2)} pc) from Earth
            </div>
            <div
              className="alb-flight-scale-phase"
              data-testid="flight-transition-phase"
            >
              {formatTransitionPhaseLabel(transitionPhase)} ·{' '}
              {Math.round(transitionProgress * 100)}%
            </div>
            {isTransitionActive && (
              <button
                type="button"
                className="alb-flight-stop-btn"
                data-testid="stop-flight-btn"
                onClick={handleStopFlight}
              >
                Stop flight
              </button>
            )}
          </div>

          <div
            className="alb-flight-scale-rungs"
            data-testid="flight-scale-rung"
            aria-label="Powers of Ten parsec scale"
          >
            {POWERS_OF_TEN_RUNGS.map((rung) => {
              const isActive = rung.exp === activeRungExp;
              return (
                <span
                  key={rung.exp}
                  className={`alb-flight-rung-chip ${isActive ? 'is-active' : ''}`}
                  data-rung={rung.exp}
                  aria-current={isActive ? 'step' : undefined}
                >
                  {rung.label}
                </span>
              );
            })}
          </div>

          <p
            className="alb-flight-scale-disclosure"
            data-testid="flight-simulated-disclosure"
          >
            <strong>
              {simBadge.shortCode} · {simBadge.label}:
            </strong>{' '}
            {simBadge.disclosure} Catalog 3D geometry from Gaia DR3 / Hipparcos
            directions and distance priors (16th-84th percentile intervals along
            the line of sight).
          </p>
        </div>
      )}
    </div>
  );
};
