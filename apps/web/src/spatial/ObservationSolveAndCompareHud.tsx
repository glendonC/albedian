import React, { useCallback } from 'react';
import {
  hasVerifiedWcs,
  type ObservationSession,
} from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';

export interface ObservationSolveAndCompareHudProps {
  session: ObservationSession;
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>;
}

/**
 * Moments 02 & 03:
 * - Moment 02 (Observation Solve Receipt & Calibration Status): Reports real
 *   observable counts (detected candidates, matched inliers, held-out pairs
 *   and inliers, and median pixel/angular residuals when held-out pairs exist)
 *   along with the appropriate EvidenceClass badge (`catalog_matched` when WCS
 *   passed independent verification, `predicted` when ephemeris-only, or
 *   unresolved/pending status). Never animates fake progress percentages and
 *   never prints capture timestamps or location coordinates.
 * - Moment 03 (Original vs Restored Sky A/B Wipe HUD): Displays immutable
 *   SourceAsset SHA-256 provenance, dispatches `media.restore` (`star_protected`)
 *   and `media.compare`, and drives `session.compareWipePosition` with a
 *   synchronized A/B wipe slider.
 */
export const ObservationSolveAndCompareHud: React.FC<
  ObservationSolveAndCompareHudProps
> = ({ session, setSession }) => {
  const solution = session.observationSolution;
  const sourceAsset = session.sourceAsset;

  const isVerifiedCalibrated =
    solution !== undefined &&
    solution.status === 'calibrated' &&
    hasVerifiedWcs(solution);

  const isPredictedCapable =
    solution !== undefined &&
    (solution.status === 'predicted_only' || solution.status === 'approximate');

  const canRegisterSky = isVerifiedCalibrated || isPredictedCapable;

  const handleRegisterSky = useCallback(() => {
    setSession((cur) => {
      const sol = cur.observationSolution;
      if (!sol) return cur;
      const mode =
        sol.status === 'calibrated' && hasVerifiedWcs(sol)
          ? 'measured'
          : 'predicted';
      return dispatchAlbedianAction(cur, {
        type: 'sky.show',
        requestId: `ui-sky-show-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        mode,
        observationSolutionId: sol.id,
      }).session;
    });
  }, [setSession]);

  const handleRestoreStarProtected = useCallback(() => {
    setSession((cur) => {
      if (!cur.sourceAsset) return cur;
      return dispatchAlbedianAction(cur, {
        type: 'media.restore',
        requestId: `ui-restore-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        sourceAssetId: cur.sourceAsset.id,
        profile: 'star_protected',
        constraints: {
          preservePeople: true,
          allowSyntheticSky: false,
          protectStars: true,
        },
      }).session;
    });
  }, [setSession]);

  const handleCompareWipe = useCallback(() => {
    setSession((cur) => {
      const variants = cur.mediaVariants ?? [];
      if (variants.length < 2) return cur;
      const first = variants[0]!;
      const last = variants[variants.length - 1]!;
      return dispatchAlbedianAction(cur, {
        type: 'media.compare',
        requestId: `ui-compare-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        variantAId: first.id,
        variantBId: last.id,
        preserveTime: true,
      }).session;
    });
  }, [setSession]);

  const handleWipeSliderChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const rawPct = Number(event.target.value);
      if (!Number.isFinite(rawPct)) return;
      const clamped = Math.min(1, Math.max(0, rawPct / 100));
      setSession((cur) => ({
        ...cur,
        compareWipePosition: Number(clamped.toFixed(4)),
      }));
    },
    [setSession]
  );

  if (!solution || !sourceAsset) {
    return null;
  }

  const quality = solution.quality;
  const badgeClass = isVerifiedCalibrated
    ? 'catalog_matched'
    : isPredictedCapable
      ? 'predicted'
      : 'observed';
  const badge = getEpistemicBadgeDescriptor(badgeClass);

  const isComparisonActive =
    session.machines?.navigation === 'comparison' ||
    Boolean(session.compareVariantId);
  const wipePct = Math.round((session.compareWipePosition ?? 0.5) * 100);
  const canCompareVariants = (session.mediaVariants?.length ?? 0) >= 2;

  return (
    <section
      className="albedian-solve-compare-hud"
      data-testid="observation-solve-compare-hud"
      data-hud-interactive="true"
      aria-label="Observation solve receipt and restoration compare controls"
    >
      <div className="albedian-solve-compare-hud__header">
        <span
          className="albedian-solve-compare-hud__badge"
          data-testid="solve-receipt-badge"
          data-evidence-class={badgeClass}
        >
          {badge.shortCode} · {badge.label}
        </span>

        <span
          className="albedian-solve-compare-hud__asset"
          data-testid="solve-source-asset-pill"
        >
          Immutable SourceAsset · sha256:{sourceAsset.sha256.slice(0, 12)}
        </span>
      </div>

      <div
        className="albedian-solve-compare-hud__metrics"
        data-testid="solve-receipt-metrics"
      >
        {quality === undefined ? (
          <span>No solver ran</span>
        ) : (
          <>
            <span>{quality.detectedCandidateCount} detected</span>
            <span>·</span>
            <span>{quality.matchedInlierCount} matched inliers</span>
            <span>·</span>
            <span>
              {quality.heldOutInliers}/{quality.heldOutPairCount} held-out inliers
            </span>
            {quality.medianResidualPx !== undefined && (
              <>
                <span>·</span>
                <span>{quality.medianResidualPx.toFixed(2)} px median residual</span>
              </>
            )}
          </>
        )}
      </div>

      <div
        className="albedian-solve-compare-hud__solver"
        data-testid="solve-receipt-solver"
      >
        {solution.solver ? (
          <>
            Solver: {solution.solver.name} v{solution.solver.version} · Catalog:{' '}
            {solution.solver.catalogRelease} · Status: {solution.status}
          </>
        ) : (
          <>No solver ran · Status: {solution.status}</>
        )}
      </div>

      <p
        className="albedian-solve-compare-hud__disclosure"
        data-testid="solve-receipt-disclosure"
      >
        {badge.disclosure}
      </p>

      {solution.warnings.length > 0 && (
        <ul
          className="albedian-solve-compare-hud__warnings"
          data-testid="solve-receipt-warnings"
        >
          {solution.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      <div className="albedian-solve-compare-hud__actions">
        <button
          type="button"
          className="albedian-solve-compare-hud__btn-primary"
          data-testid="solve-register-sky-btn"
          disabled={!canRegisterSky}
          onClick={handleRegisterSky}
        >
          {isVerifiedCalibrated
            ? 'Register Sky'
            : isPredictedCapable
              ? 'Show Predicted Sky'
              : 'Sky Unresolved'}
        </button>

        <button
          type="button"
          className="albedian-solve-compare-hud__btn-secondary"
          data-testid="restore-star-protected-btn"
          onClick={handleRestoreStarProtected}
        >
          Restore Star-Protected
        </button>

        <button
          type="button"
          className="albedian-solve-compare-hud__btn-secondary"
          data-testid="toggle-compare-wipe-btn"
          disabled={!canCompareVariants}
          onClick={handleCompareWipe}
        >
          Compare A/B Wipe
        </button>
      </div>

      {isComparisonActive && (
        <div
          className="albedian-solve-compare-hud__wipe"
          data-testid="compare-wipe-panel"
        >
          <div className="albedian-solve-compare-hud__wipe-labels">
            <span>Original Source</span>
            <span data-testid="compare-wipe-readout">Wipe {wipePct}%</span>
            <span>Star-Protected Restored</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={wipePct}
            onChange={handleWipeSliderChange}
            className="albedian-solve-compare-hud__slider"
            data-testid="compare-wipe-slider"
            aria-label="Original vs restored sky A/B wipe position"
          />
        </div>
      )}
    </section>
  );
};
