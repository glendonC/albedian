import React, { useCallback } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import {
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
} from '@albedian/evidence';

export interface EpochAndDisclosureHudProps {
  session: ObservationSession;
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>;
}

function parseIsoYear(iso: string | undefined): number | null {
  if (!iso) return null;
  const match = /^([+-]?\d{4,6})/.exec(iso.trim());
  if (!match) return null;
  const yr = Number(match[1]);
  return Number.isFinite(yr) ? yr : null;
}

/**
 * Moment 05 / 06:
 * - Alternative 3D viewpoint displacement (`space.viewpoint` orbit +1 pc),
 *   sightline description (`finding.describe`), and active marker filter
 *   toggles (`observedMarkers` and `predictedEphemerides`, read by
 *   `CelestialMarkerOverlay`).
 * - Deep-time epoch readout (`simulationEpochISO` vs `observationEpochISO`
 *   year delta without printing raw capture timestamps) with explicit honesty
 *   disclosure that the view does not move stars to a chosen year yet, plus a
 *   one-click reset to the observation epoch.
 * - Active `simulated` class disclosure from `getEpistemicBadgeDescriptor('simulated')`.
 */
export const EpochAndDisclosureHud: React.FC<EpochAndDisclosureHudProps> = ({
  session,
  setSession,
}) => {
  const spatialView = session.spatialView;
  const navState = session.machines?.navigation ?? 'source';
  const simEpoch = spatialView?.simulationEpochISO ?? '';
  const obsEpoch = spatialView?.observationEpochISO ?? '';
  const hasEpochShift =
    Boolean(simEpoch && obsEpoch && simEpoch !== obsEpoch) ||
    spatialView?.mode === 'modeled_epoch';

  const isSpaceActive =
    navState !== 'source' &&
    (spatialView?.mode === 'catalog_3d' ||
      spatialView?.mode === 'modeled_epoch' ||
      hasEpochShift);

  const selectedFinding =
    session.observationSolution?.findings.find(
      (f) => f.id === session.selectedFindingId
    ) ?? null;

  const canShiftViewpoint =
    selectedFinding !== null &&
    selectedFinding.evidenceClass === 'catalog_matched' &&
    Boolean(selectedFinding.inferredDistancePc) &&
    formatDistanceWithUncertainty(selectedFinding.inferredDistancePc)
      .canEnter3DSpace;

  const canDescribeSightline =
    selectedFinding !== null && Boolean(selectedFinding.skyPosition);

  const handleOrbitViewpoint = useCallback(() => {
    setSession((cur) => {
      const targetId = cur.selectedFindingId;
      if (!targetId) return cur;
      return dispatchAlbedianAction(cur, {
        type: 'space.viewpoint',
        requestId: `ui-viewpoint-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        targetFindingId: targetId,
        units: 'pc',
        offsetParsec: [1, 0, 0],
        trajectory: 'orbit',
      }).session;
    });
  }, [setSession]);

  const handleDescribeSightline = useCallback(() => {
    setSession((cur) => {
      const targetId = cur.selectedFindingId;
      if (!targetId) return cur;
      return dispatchAlbedianAction(cur, {
        type: 'finding.describe',
        requestId: `ui-sightline-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        findingId: targetId,
        fields: ['distance', 'evidence', 'behind_sightline'],
      }).session;
    });
  }, [setSession]);

  const handleToggleObservedMarkers = useCallback(() => {
    setSession((cur) => ({
      ...cur,
      viewRevision: cur.viewRevision + 1,
      spatialView: {
        ...cur.spatialView,
        activeOverlays: {
          ...cur.spatialView.activeOverlays,
          observedMarkers: !cur.spatialView.activeOverlays.observedMarkers,
        },
      },
    }));
  }, [setSession]);

  const handleTogglePredictedEphemerides = useCallback(() => {
    setSession((cur) => ({
      ...cur,
      viewRevision: cur.viewRevision + 1,
      spatialView: {
        ...cur.spatialView,
        activeOverlays: {
          ...cur.spatialView.activeOverlays,
          predictedEphemerides:
            !cur.spatialView.activeOverlays.predictedEphemerides,
        },
      },
    }));
  }, [setSession]);

  const handleResetEpoch = useCallback(() => {
    setSession((cur) => ({
      ...cur,
      viewRevision: cur.viewRevision + 1,
      spatialView: {
        ...cur.spatialView,
        mode:
          cur.spatialView.mode === 'modeled_epoch'
            ? 'catalog_3d'
            : cur.spatialView.mode,
        simulationEpochISO:
          cur.spatialView.observationEpochISO ??
          cur.spatialView.simulationEpochISO,
      },
      lastStatusBanner: 'Reset simulation epoch to observation moment.',
    }));
  }, [setSession]);

  if (!isSpaceActive && !hasEpochShift) {
    return null;
  }

  const simBadge = getEpistemicBadgeDescriptor('simulated');
  const simYear = parseIsoYear(simEpoch);
  const obsYear = parseIsoYear(obsEpoch);
  const yearsDelta =
    simYear !== null && obsYear !== null ? simYear - obsYear : null;
  const deltaText =
    yearsDelta !== null
      ? `${yearsDelta >= 0 ? '+' : ''}${yearsDelta} yr`
      : 'Non-observation epoch';

  return (
    <section
      className="albedian-epoch-disclosure-hud"
      data-testid="epoch-and-disclosure-hud"
      data-hud-interactive="true"
      aria-label="Simulated viewpoint, overlay, and epoch disclosure controls"
    >
      <div className="albedian-epoch-disclosure-hud__header">
        <span
          className="albedian-epoch-disclosure-hud__badge"
          data-testid="epoch-simulated-badge"
        >
          {simBadge.shortCode} · {simBadge.label}
        </span>

        <span
          className="albedian-epoch-disclosure-hud__disclosure"
          data-testid="epoch-simulated-disclosure"
        >
          {simBadge.disclosure}
        </span>
      </div>

      <div className="albedian-epoch-disclosure-hud__actions">
        <button
          type="button"
          className="albedian-epoch-disclosure-hud__btn"
          data-testid="shift-viewpoint-orbit-btn"
          disabled={!canShiftViewpoint}
          onClick={handleOrbitViewpoint}
        >
          Orbit Viewpoint (+1 pc)
        </button>

        <button
          type="button"
          className="albedian-epoch-disclosure-hud__btn"
          data-testid="describe-sightline-btn"
          disabled={!canDescribeSightline}
          onClick={handleDescribeSightline}
        >
          Describe Sightline
        </button>

        <button
          type="button"
          className={`albedian-epoch-disclosure-hud__btn ${
            spatialView?.activeOverlays?.observedMarkers ? 'is-active' : ''
          }`}
          data-testid="toggle-observed-markers-btn"
          onClick={handleToggleObservedMarkers}
        >
          Observed Markers:{' '}
          {spatialView?.activeOverlays?.observedMarkers ? 'On' : 'Off'}
        </button>

        <button
          type="button"
          className={`albedian-epoch-disclosure-hud__btn ${
            spatialView?.activeOverlays?.predictedEphemerides ? 'is-active' : ''
          }`}
          data-testid="toggle-predicted-ephemerides-btn"
          onClick={handleTogglePredictedEphemerides}
        >
          Predicted Ephemerides:{' '}
          {spatialView?.activeOverlays?.predictedEphemerides ? 'On' : 'Off'}
        </button>
      </div>

      {hasEpochShift && (
        <div
          className="albedian-epoch-disclosure-hud__epoch"
          data-testid="epoch-delta-readout"
        >
          <span>
            Epoch offset: {deltaText} · The app cannot show the sky at another
            year yet: the view does not move the stars to a chosen year.
          </span>
          <button
            type="button"
            className="albedian-epoch-disclosure-hud__reset-btn"
            data-testid="reset-observation-epoch-btn"
            onClick={handleResetEpoch}
          >
            Reset to Observation Epoch
          </button>
        </div>
      )}
    </section>
  );
};
