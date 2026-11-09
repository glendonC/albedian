import React, { useEffect, useRef, useState } from 'react';
import type { CelestialFinding, EvidenceClass, ObservationSession } from '@albedian/contracts';
import {
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
  PARSEC_TO_LIGHT_YEAR,
} from '@albedian/evidence';
import {
  DISTANCE_RAIL_LY,
  LUMINOSITY_RAIL_SUN,
  RUWE_CAUTION_ABOVE,
  SUN_TEFF_K,
  TEMPERATURE_RAIL_K,
  astrophysicsOf,
  blackbodyGradientCss,
  cropPlacement,
  distanceMethodLabel,
  formatSignificant,
  offScaleNote,
  placeIntervalOnLogRail,
  placeOnLogRail,
  type IntervalPlacement,
  type LogRail,
  type ScanInterval,
} from './scanScales.js';
import { findingCanEnterSpace } from './askSuggestions.js';
import { decodedFrameMatches, orientedSize } from './sourcePhotoRect.js';

export interface CelestialTargetCardProps {
  session: ObservationSession;
  selectedFinding: CelestialFinding | null;
  onRunUtterance: (
    utterance: string,
    modality?: 'voice' | 'text' | 'pointer'
  ) => void;
  onPushToTalk: () => void;
  /** The uploaded photo as shown, for the pixel crop; a clip or no photo draws no crop. */
  sourceImageUrl?: string | null;
}

const CROP_BOX_PX = 72;
const CROP_SOURCE_PX = 12;

const formatNumber = (value: number, digits: number) =>
  value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });

const formatTick = (value: number) =>
  value < 1 ? String(value) : value.toLocaleString('en-US', { maximumFractionDigits: 0 });

const percent = (fraction: number) => `${(fraction * 100).toFixed(2)}%`;

/** The evidence class as a shape (the same grammar as the photo markers), coloured by the HUD evidence tokens. */
const ScanGlyph: React.FC<{ evidenceClass: EvidenceClass; testId?: string }> = ({ evidenceClass, testId }) => (
  <svg
    className={`alb-scan-glyph alb-scan-glyph--${evidenceClass}`}
    data-testid={testId}
    data-evidence-class={evidenceClass}
    viewBox="0 0 16 16"
    width="14"
    height="14"
    aria-hidden="true"
  >
    {evidenceClass === 'observed' && (
      <>
        <circle cx="8" cy="8" r="5.5" />
        <circle cx="8" cy="8" r="1.4" className="alb-scan-glyph-fill" />
      </>
    )}
    {evidenceClass === 'catalog_matched' && (
      <>
        <circle cx="8" cy="8" r="4.5" />
        <path d="M8 0.8V3.5M8 12.5V15.2M0.8 8H3.5M12.5 8H15.2" />
      </>
    )}
    {evidenceClass === 'predicted' && <circle cx="8" cy="8" r="5.5" strokeDasharray="2.2 2" />}
    {evidenceClass === 'simulated' && <path d="M1.5 5V1.5H5M11 1.5H14.5V5M14.5 11V14.5H11M5 14.5H1.5V11" />}
  </svg>
);

const SectionHead: React.FC<{
  evidenceClass: EvidenceClass;
  label: string;
  glyphTestId?: string;
  value?: React.ReactNode;
}> = ({ evidenceClass, label, glyphTestId, value }) => (
  <div className="alb-scan-sec-head">
    <span className="alb-scan-kicker">
      <ScanGlyph evidenceClass={evidenceClass} testId={glyphTestId} />
      {label}
    </span>
    {value !== undefined && <span className="alb-scan-value">{value}</span>}
  </div>
);

/** A log rail with its ticks, an optional 16th to 84th band and the median marker. */
const LogRailView: React.FC<{
  rail: LogRail;
  placement: IntervalPlacement;
  testId: string;
  unit: string;
  tickLabel: (value: number, index: number) => string;
}> = ({ rail, placement, testId, unit, tickLabel }) => {
  const { lower, upper, median } = placement;
  return (
    <div className="alb-scan-rail" data-testid={testId}>
      <div className="alb-scan-rail-line" aria-hidden="true">
        {rail.ticks.map((tick) => (
          <span key={tick} className="alb-scan-tick" style={{ left: percent(placeOnLogRail(tick, rail)?.fraction ?? 0) }} />
        ))}
        {lower && upper && (
          <span
            className="alb-scan-band"
            data-testid={`${testId}-band`}
            data-lower-off-scale={lower.offScale ?? undefined}
            data-upper-off-scale={upper.offScale ?? undefined}
            style={{ left: percent(lower.fraction), width: percent(upper.fraction - lower.fraction) }}
          />
        )}
        <span
          className="alb-scan-marker"
          data-testid={`${testId}-marker`}
          data-off-scale={median.offScale ?? undefined}
          style={{ left: percent(median.fraction) }}
        />
      </div>
      <div className="alb-scan-tick-labels" aria-hidden="true">
        {rail.ticks.map((tick, index) => (
          <span key={tick} className="alb-scan-tick-label" style={{ left: percent(placeOnLogRail(tick, rail)?.fraction ?? 0) }}>
            {tickLabel(tick, index)}
          </span>
        ))}
      </div>
      {offScaleNote(placement, rail, unit) && (
        <p className="alb-scan-off-scale" data-testid={`${testId}-off-scale`}>
          {offScaleNote(placement, rail, unit)}
        </p>
      )}
    </div>
  );
};

/** The photo stage's own words for a still this browser cannot draw (SourcePhotoStage.tsx). */
const cannotDisplayMessage = (mimeType: string | undefined) =>
  `This browser cannot display ${mimeType ?? 'this file type'}. The file was stored and solved as uploaded; open the app in a browser that shows it, such as Safari on iPhone or Mac, to see it here.`;

const rangeText = (interval: ScanInterval, digits: number, unit: string) =>
  interval.lower !== undefined && interval.upper !== undefined
    ? `${formatNumber(interval.lower, digits)} to ${formatNumber(interval.upper, digits)}${unit}`
    : null;

/**
 * Moment 05 star scan card in the HUD black glass.
 *
 * Scientific honesty rules:
 * - Always displays the authoritative `EvidenceClass` badge and disclosure from `@albedian/evidence`.
 * - Renders only quantities present on `selectedFinding`; absent catalog fields (`spectralType`,
 *   `colorIndexBpRp`, `properMotionRaMasYr`, the astrophysics block) stay omitted rather than guessed.
 * - The pixel crop is the uploaded photo's own pixels around the measured centroid.
 * - Enables `Go there` (and its G key) only when `evidenceClass === 'catalog_matched'` and `canEnter3DSpace` is true.
 */
export const CelestialTargetCard: React.FC<CelestialTargetCardProps> = ({
  session,
  selectedFinding,
  onRunUtterance,
  onPushToTalk,
  sourceImageUrl,
}) => {
  const distInfo = formatDistanceWithUncertainty(selectedFinding?.inferredDistancePc);
  const canEnterSpace = findingCanEnterSpace(selectedFinding);
  const [cropFailedUrl, setCropFailedUrl] = useState<string | null>(null);
  const [cropMismatchUrl, setCropMismatchUrl] = useState<string | null>(null);
  const goThere = useRef<() => void>(() => {});
  goThere.current = () => {
    if (selectedFinding && canEnterSpace) onRunUtterance(`Take me to ${selectedFinding.title}`, 'pointer');
  };

  useEffect(() => {
    if (!canEnterSpace) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
      if (event.key !== 'g' && event.key !== 'G') return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'button' || target?.isContentEditable) return;
      event.preventDefault();
      goThere.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [canEnterSpace]);

  if (!selectedFinding) return null;

  const evidenceClass = selectedFinding.evidenceClass;
  const badge = getEpistemicBadgeDescriptor(evidenceClass);
  const navState = session.machines?.navigation ?? 'source';
  const showBackToMoment = navState !== 'source';

  const det = selectedFinding.sourceDetection;
  const cat = selectedFinding.catalogRef;
  const astro = astrophysicsOf(cat);
  const limitations = selectedFinding.evidence?.limitations ?? [];
  const residualPx = selectedFinding.evidence.pixelResidualPx;

  const asset = session.sourceAsset;
  const crop =
    det && sourceImageUrl && asset?.mediaKind === 'image'
      ? cropPlacement(det, { width: asset.width, height: asset.height }, asset.displayOrientation, CROP_BOX_PX, CROP_SOURCE_PX)
      : null;
  const cropFailed = Boolean(sourceImageUrl) && cropFailedUrl === sourceImageUrl;
  const cropMismatch = Boolean(sourceImageUrl) && cropMismatchUrl === sourceImageUrl;
  const checkDecodedFrame = (img: HTMLImageElement) => {
    if (!asset || !sourceImageUrl) return;
    const upright = orientedSize(asset.width, asset.height, asset.displayOrientation);
    const matches = decodedFrameMatches(img.naturalWidth, img.naturalHeight, upright.widthPx, upright.heightPx);
    setCropMismatchUrl(matches ? null : sourceImageUrl);
  };

  const distancePc = selectedFinding.inferredDistancePc;
  const distancePlacement =
    distInfo.canEnter3DSpace && distancePc
      ? placeIntervalOnLogRail(
          {
            median: distancePc.median * PARSEC_TO_LIGHT_YEAR,
            lower: distancePc.lower * PARSEC_TO_LIGHT_YEAR,
            upper: distancePc.upper * PARSEC_TO_LIGHT_YEAR,
          },
          DISTANCE_RAIL_LY
        )
      : null;

  const temperature = astro?.temperatureK ?? null;
  const temperaturePlacement = temperature ? placeIntervalOnLogRail(temperature, TEMPERATURE_RAIL_K) : null;
  const sunPlacement = placeOnLogRail(SUN_TEFF_K, TEMPERATURE_RAIL_K);
  const luminosity = astro?.luminositySolar ?? null;
  const luminosityPlacement = luminosity ? placeIntervalOnLogRail(luminosity, LUMINOSITY_RAIL_SUN) : null;
  const ruwe = astro?.ruwe ?? null;
  const catalogSource = cat ? `${cat.provider}` : 'Catalog';

  return (
    <aside
      className="alb-target-card"
      data-hud-interactive="true"
      data-testid="celestial-target-card"
      aria-label={`Selected target: ${selectedFinding.title}`}
    >
      <div className="alb-scan-body">
      <header className="alb-scan-head">
        <span className="alb-scan-kicker">
          <ScanGlyph evidenceClass={evidenceClass} testId="target-card-glyph" />
          <span
            className="alb-scan-class"
            data-testid="target-card-evidence-badge"
            data-evidence-class={evidenceClass}
          >
            <span className="alb-target-card-shortcode">{badge.shortCode}</span>{' '}
            <span>{badge.label}</span>
          </span>
        </span>
        <h2 className="alb-scan-name" data-testid="target-card-title">
          {selectedFinding.title}
        </h2>
        <div className="alb-scan-sub" data-testid="target-card-subtitle">
          {selectedFinding.subtitle}
        </div>
        <p className="alb-scan-disclosure" data-testid="target-card-disclosure">
          {badge.disclosure}
        </p>
      </header>

      {det && (
        <section className="alb-scan-sec" data-testid="target-card-pixel-row">
          <SectionHead evidenceClass="observed" label="In your photo" glyphTestId="target-card-pixel-row-glyph" />
          <div className="alb-scan-photo">
            {crop && sourceImageUrl && !cropFailed && !cropMismatch && (
              <div
                className="alb-scan-crop"
                data-testid="target-card-pixel-crop"
                role="img"
                aria-label={`The star in your photo, ${CROP_SOURCE_PX} by ${CROP_SOURCE_PX} pixels around its measured centre`}
              >
                <img
                  src={sourceImageUrl}
                  alt=""
                  draggable={false}
                  style={{
                    left: `${crop.left.toFixed(2)}px`,
                    top: `${crop.top.toFixed(2)}px`,
                    width: `${crop.width.toFixed(2)}px`,
                    height: `${crop.height.toFixed(2)}px`,
                  }}
                  onLoad={(e) => checkDecodedFrame(e.currentTarget)}
                  onError={() => setCropFailedUrl(sourceImageUrl)}
                />
                <span className="alb-scan-crop-cross" aria-hidden="true" />
              </div>
            )}
            {crop && cropFailed && (
              <p className="alb-scan-crop-unavailable" data-testid="target-card-pixel-crop-unavailable">
                {cannotDisplayMessage(asset?.mimeType)}
              </p>
            )}
            <dl className="alb-scan-facts">
              <dt>SNR {det.snr.toFixed(1)}</dt>
              <dd>signal to noise</dd>
              <dt>{det.fwhmPx.toFixed(2)} px</dt>
              <dd>star width (FWHM)</dd>
              {residualPx !== undefined && (
                <>
                  <dt>{residualPx.toFixed(2)} px</dt>
                  <dd>off its solved spot (held out)</dd>
                </>
              )}
              <dt>
                ({det.xPx.toFixed(1)}, {det.yPx.toFixed(1)})
              </dt>
              <dd>centre in your photo, px</dd>
            </dl>
          </div>
        </section>
      )}

      <section className="alb-scan-sec" data-testid="target-card-distance-row">
        <SectionHead
          evidenceClass={evidenceClass === 'catalog_matched' ? 'catalog_matched' : evidenceClass}
          label="Distance"
          value={
            distInfo.canEnter3DSpace && distancePc ? (
              <>
                {formatSignificant(distancePc.median * PARSEC_TO_LIGHT_YEAR)} ly{' '}
                <small>{formatSignificant(distancePc.median)} pc</small>
              </>
            ) : (
              'Distance Unconstrained'
            )
          }
        />
        {distancePlacement && (
          <LogRailView
            rail={DISTANCE_RAIL_LY}
            placement={distancePlacement}
            testId="distance-rail"
            unit=" ly"
            tickLabel={(tick, index) => (index === 0 ? `${formatTick(tick)} ly` : formatTick(tick))}
          />
        )}
        {distancePc && distInfo.canEnter3DSpace ? (
          <p className="alb-scan-source" data-testid="distance-rail-source">
            {distanceMethodLabel(distancePc.method).name} · {distanceMethodLabel(distancePc.method).range}:{' '}
            {formatSignificant(distancePc.lower * PARSEC_TO_LIGHT_YEAR)} to{' '}
            {formatSignificant(distancePc.upper * PARSEC_TO_LIGHT_YEAR)} ly
          </p>
        ) : (
          <p className="alb-scan-source">{distInfo.uncertaintyText}</p>
        )}
      </section>

      {temperature && temperaturePlacement && (
        <section className="alb-scan-sec" data-testid="temperature-strip">
          <SectionHead
            evidenceClass="catalog_matched"
            label="Surface temperature"
            value={`${formatNumber(temperature.median, 0)} K`}
          />
          <div className="alb-scan-strip">
            <div className="alb-scan-strip-bar" data-testid="temperature-strip-bar" style={{ background: blackbodyGradientCss() }} />
            {sunPlacement && (
              <span className="alb-scan-sun" data-testid="temperature-strip-sun" style={{ left: percent(sunPlacement.fraction) }}>
                Sun
              </span>
            )}
            <span
              className="alb-scan-marker alb-scan-marker--strip"
              data-testid="temperature-strip-marker"
              data-off-scale={temperaturePlacement.median.offScale ?? undefined}
              style={{ left: percent(temperaturePlacement.median.fraction) }}
            />
          </div>
          {offScaleNote(temperaturePlacement, TEMPERATURE_RAIL_K, ' K') && (
            <p className="alb-scan-off-scale" data-testid="temperature-strip-off-scale">
              {offScaleNote(temperaturePlacement, TEMPERATURE_RAIL_K, ' K')}
            </p>
          )}
          <div className="alb-scan-strip-ends" aria-hidden="true">
            <span>{formatTick(TEMPERATURE_RAIL_K.min)} K</span>
            <span>{formatTick(TEMPERATURE_RAIL_K.max)} K</span>
          </div>
          <p className="alb-scan-source">
            {catalogSource}
            {rangeText(temperature, 0, ' K') ? ` · ${rangeText(temperature, 0, ' K')}` : ''}
            {' · strip is Planck times CIE 1931, computed'}
          </p>
        </section>
      )}

      {luminosity && luminosityPlacement && (
        <section className="alb-scan-sec" data-testid="luminosity-rail">
          <SectionHead
            evidenceClass="catalog_matched"
            label="Luminosity"
            value={
              <>
                {formatNumber(luminosity.median, 2)} <small>× Sun</small>
              </>
            }
          />
          <LogRailView
            rail={LUMINOSITY_RAIL_SUN}
            placement={luminosityPlacement}
            testId="luminosity-rail"
            unit=" × Sun"
            tickLabel={(tick) => formatTick(tick)}
          />
          <p className="alb-scan-source">
            {catalogSource}
            {rangeText(luminosity, 2, ' × Sun') ? ` · ${rangeText(luminosity, 2, ' × Sun')}` : ''}
          </p>
        </section>
      )}

      {ruwe !== null && ruwe > RUWE_CAUTION_ABOVE && (
        <p className="alb-scan-caution" data-testid="target-card-ruwe-caution">
          Gaia's position fit for this star is loose (RUWE {ruwe.toFixed(2)}). Read the distance with care.
        </p>
      )}

      {cat && (
        <section className="alb-scan-sec alb-scan-catalog" data-testid="target-card-catalog-row">
          <div className="alb-scan-catalog-id">
            {cat.provider} ({cat.releaseVersion}) · {cat.catalogSourceId}
          </div>
          <div className="alb-scan-catalog-line">
            {cat.constellation}
            {cat.apparentMagnitude !== undefined && ` · Mag ${cat.apparentMagnitude.toFixed(2)}`}
          </div>
          {cat.spectralType && (
            <div className="alb-scan-catalog-line" data-testid="target-card-spectral-type">
              Spectral type: {cat.spectralType}
            </div>
          )}
          {cat.colorIndexBpRp !== undefined && (
            <div className="alb-scan-catalog-line" data-testid="target-card-bprp">
              BP-RP: {cat.colorIndexBpRp.toFixed(2)}
            </div>
          )}
          {cat.properMotionRaMasYr !== undefined && cat.properMotionDecMasYr !== undefined && (
            <div className="alb-scan-catalog-line" data-testid="target-card-proper-motion">
              Proper motion: ({cat.properMotionRaMasYr.toFixed(2)}, {cat.properMotionDecMasYr.toFixed(2)}) mas/yr
            </div>
          )}
          <div className="alb-scan-source" data-testid="target-card-citation">
            {cat.citation}
          </div>
        </section>
      )}

      {limitations.length > 0 && (
        <section className="alb-scan-sec" data-testid="target-card-limitations">
          <div className="alb-scan-kicker">Limits and provenance</div>
          <ul className="alb-scan-limits">
            {limitations.map((lim) => (
              <li key={lim}>{lim}</li>
            ))}
          </ul>
        </section>
      )}

      </div>

      <footer className="alb-scan-actions">
        <button
          type="button"
          className="alb-scan-action"
          data-testid="target-card-go-there"
          disabled={!canEnterSpace}
          onClick={() => goThere.current()}
        >
          Go there {canEnterSpace && <kbd className="alb-key-only">G</kbd>}
        </button>
        <button type="button" className="alb-scan-action" data-testid="target-card-ask" onClick={onPushToTalk}>
          Ask out loud <kbd className="alb-key-only">Space</kbd>
        </button>
        {showBackToMoment && (
          <button
            type="button"
            className="alb-scan-action alb-scan-action--end"
            data-testid="target-card-back"
            onClick={() => onRunUtterance('Take me back', 'pointer')}
          >
            Back to moment <kbd className="alb-key-only">Esc</kbd>
          </button>
        )}
      </footer>
    </aside>
  );
};
