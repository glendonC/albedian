// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EXIF_ORIENTATIONS, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return null;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));

vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: {
    toggle: vi.fn(() => false),
    setMasterVolume: vi.fn(),
    setScaleRegime: vi.fn(),
    playDetentClick: vi.fn(),
  },
}));

vi.mock('../../audio/SkyAudioFeed.js', () => ({
  skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() },
}));

vi.mock('../CoalescingBar.js', () => ({
  CoalescingBar: () => null,
}));

const { CelestialTargetCard } = await import('../CelestialTargetCard.js');
const { SpatialObservatoryViewport } = await import(
  '../SpatialObservatoryViewport.js'
);

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const cssPath = path.resolve(__dirname, '../../styles/albedian.css');
const cssSource = fs.readFileSync(cssPath, 'utf8');
const { DISTANCE_RAIL_LY, LUMINOSITY_RAIL_SUN, TEMPERATURE_RAIL_K, SUN_TEFF_K, cropPlacement, placeOnLogRail } =
  await import('../scanScales.js');
const { SourcePhotoStage } = await import('../SourcePhotoStage.js');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const q = <T extends Element = HTMLElement>(testId: string) =>
  container.querySelector(`[data-testid="${testId}"]`) as T | null;

const LY_PER_PC = 3.261563777;

function betelgeuseIn(session: ObservationSession): CelestialFinding {
  return session.observationSolution!.findings.find((f) => f.id === 'finding_betelgeuse')!;
}

/** Tabit's real values from the app's own catalog tiles (Gaia DR3, Bailer-Jones 2021, GSP-Phot, FLAME). */
function asTabit(finding: CelestialFinding, astrophysics?: unknown): CelestialFinding {
  return {
    ...finding,
    title: 'Tabit',
    inferredDistancePc: { lower: 8.011, median: 8.024, upper: 8.039, method: 'photogeometric_bailer_jones_edr3' },
    catalogRef: finding.catalogRef
      ? ({ ...finding.catalogRef, commonName: 'Tabit', ...(astrophysics === undefined ? {} : { astrophysics }) } as CelestialFinding['catalogRef'])
      : undefined,
  };
}

const TABIT_ASTRO = {
  temperatureK: { median: 6154, lower: 6150, upper: 6158 },
  luminositySolar: { median: 2.85, lower: 2.84, upper: 2.87 },
  ruwe: 3.93,
};

function renderCard(props: {
  session: ObservationSession;
  selectedFinding: CelestialFinding | null;
  onRunUtterance?: (u: string, m?: 'voice' | 'text' | 'pointer') => void;
  onPushToTalk?: () => void;
  sourceImageUrl?: string | null;
}) {
  act(() => {
    root.render(
      createElement(CelestialTargetCard, {
        onRunUtterance: () => {},
        onPushToTalk: () => {},
        ...props,
      })
    );
  });
}

function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = window) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

const percent = (fraction: number) => `${(fraction * 100).toFixed(2)}%`;

describe('CelestialTargetCard scan card (hud-5)', () => {
  it('renders nothing when selectedFinding is null', () => {
    renderCard({ session: createSyntheticCalibratedSession(), selectedFinding: null });
    expect(q('celestial-target-card')).toBeNull();
  });

  it('shows a catalog matched star with its class, disclosure, measured pixels, distance, catalog fields and actions', () => {
    const base = createSyntheticCalibratedSession();
    const session: ObservationSession = { ...base, machines: { ...base.machines, navigation: 'sky_registered' } };
    const original = betelgeuseIn(session);
    const betelgeuse: CelestialFinding = {
      ...original,
      catalogRef: original.catalogRef
        ? { ...original.catalogRef, colorIndexBpRp: 1.5, properMotionRaMasYr: 27.54, properMotionDecMasYr: 11.3 }
        : undefined,
    };
    const onRunUtterance = vi.fn();
    const onPushToTalk = vi.fn();
    renderCard({ session, selectedFinding: betelgeuse, onRunUtterance, onPushToTalk });

    expect(q('celestial-target-card')!.getAttribute('data-hud-interactive')).toBe('true');
    const badge = q('target-card-evidence-badge')!;
    expect(badge.getAttribute('data-evidence-class')).toBe('catalog_matched');
    expect(badge.textContent).toContain('CAT');
    expect(badge.textContent).toContain('Catalog Matched (Verified WCS)');
    expect(q('target-card-glyph')!.getAttribute('data-evidence-class')).toBe('catalog_matched');
    expect(q('target-card-disclosure')?.textContent).toBe('Source pixel detection matched to catalog via independently verified WCS.');
    expect(q('target-card-title')?.textContent).toBe('Betelgeuse');
    expect(q('target-card-subtitle')?.textContent).toContain('Orion');

    const pixels = q('target-card-pixel-row')!;
    expect(pixels.textContent).toContain('SNR');
    expect(pixels.textContent).toContain('star width (FWHM)');
    expect(q('target-card-pixel-row-glyph')!.getAttribute('data-evidence-class')).toBe('observed');

    const distance = q('target-card-distance-row')!;
    expect(distance.textContent).toContain('pc');
    expect(distance.textContent).toContain('ly');

    expect(q('target-card-catalog-row')).not.toBeNull();
    expect(q('target-card-spectral-type')?.textContent).toContain(betelgeuse.catalogRef?.spectralType ?? '');
    expect(q('target-card-bprp')?.textContent).toBe('BP-RP: 1.50');
    expect(q('target-card-proper-motion')?.textContent).toContain('(27.54, 11.30) mas/yr');
    expect(q('target-card-citation')?.textContent).toBe(betelgeuse.catalogRef?.citation);

    const go = q<HTMLButtonElement>('target-card-go-there')!;
    expect(go.disabled).toBe(false);
    act(() => go.click());
    expect(onRunUtterance).toHaveBeenCalledWith('Take me to Betelgeuse', 'pointer');
    act(() => q<HTMLButtonElement>('target-card-ask')!.click());
    expect(onPushToTalk).toHaveBeenCalledTimes(1);
    act(() => q<HTMLButtonElement>('target-card-back')!.click());
    expect(onRunUtterance).toHaveBeenCalledWith('Take me back', 'pointer');
  });

  it('omits absent quantities rather than inventing them, disables Go there when distance is unconstrained, and hides Back in source mode', () => {
    const base = createSyntheticCalibratedSession();
    const session: ObservationSession = { ...base, machines: { ...base.machines, navigation: 'source' } };
    const predictedPlanet: CelestialFinding = {
      id: 'finding_jupiter_prd',
      observationSolutionId: session.observationSolution!.id,
      evidenceClass: 'predicted',
      title: 'Jupiter',
      subtitle: 'Solar System Planet',
      skyPosition: { raDeg: 88.5, decDeg: 7.2, uncertaintyArcsec: 0.5 },
      catalogRef: {
        id: 'cat_jup',
        provider: 'JPL Horizons',
        releaseVersion: 'DE440',
        catalogSourceId: '599',
        commonName: 'Jupiter',
        constellation: 'Taurus',
        objectType: 'planet',
        coordinateEpoch: 'J2000',
        raDeg: 88.5,
        decDeg: 7.2,
        apparentMagnitude: -2.4,
        citation: 'Meeus (1998) Astronomical Algorithms',
      },
      evidence: {
        id: 'ev_jup',
        evidenceClass: 'predicted',
        verificationMethod: 'Ephemeris at observedAtUTC',
        limitations: ['Computed from recording time and location; not recovered from camera pixels.'],
        verifiedByIndependentHoldout: false,
      },
    };
    renderCard({ session, selectedFinding: predictedPlanet, sourceImageUrl: 'blob:photo' });

    const badge = q('target-card-evidence-badge')!;
    expect(badge.getAttribute('data-evidence-class')).toBe('predicted');
    expect(badge.textContent).toContain('PRD');
    expect(badge.textContent).toContain('Predicted (Ephemeris / Model)');
    expect(q('target-card-pixel-row')).toBeNull();
    expect(q('target-card-pixel-crop')).toBeNull();

    const distance = q('target-card-distance-row')!;
    expect(distance.textContent).toContain('Distance Unconstrained');
    expect(distance.textContent).toContain('No reliable parallax or Bailer-Jones distance prior available.');
    expect(q('distance-rail')).toBeNull();

    expect(q('target-card-spectral-type')).toBeNull();
    expect(q('target-card-bprp')).toBeNull();
    expect(q('target-card-proper-motion')).toBeNull();
    expect(q('temperature-strip')).toBeNull();
    expect(q('luminosity-rail')).toBeNull();
    expect(q('target-card-ruwe-caution')).toBeNull();

    expect(q<HTMLButtonElement>('target-card-go-there')!.disabled).toBe(true);
    expect(q('target-card-back')).toBeNull();
    expect(q('target-card-limitations')?.textContent).toContain(
      'Computed from recording time and location; not recovered from camera pixels.'
    );
  });
});

describe('card layout (hud-5 fix)', () => {
  it('scrolls the sections in a body and keeps the actions outside it', () => {
    const session = createSyntheticCalibratedSession();
    renderCard({ session, selectedFinding: asTabit(betelgeuseIn(session), TABIT_ASTRO) });
    const card = q('celestial-target-card')!;
    const body = card.querySelector(':scope > .alb-scan-body')!;
    expect(body).not.toBeNull();
    expect(body.querySelector('[data-testid="target-card-distance-row"]')).not.toBeNull();
    const footer = card.querySelector(':scope > .alb-scan-actions')!;
    expect(footer).not.toBeNull();
    expect(body.contains(footer)).toBe(false);
  });
});

describe('distance rail (hud-5)', () => {
  it('marks the median and the 16th to 84th band on a 1 to 10,000 ly log rail, with the method named', () => {
    const session = createSyntheticCalibratedSession();
    renderCard({ session, selectedFinding: asTabit(betelgeuseIn(session)) });

    const rail = q('distance-rail')!;
    const ticks = [...rail.querySelectorAll('.alb-scan-tick-label')].map((t) => t.textContent);
    expect(ticks).toEqual(['1 ly', '10', '100', '1,000', '10,000']);

    const medianLy = 8.024 * LY_PER_PC;
    const marker = q('distance-rail-marker')!;
    expect(marker.style.left).toBe(percent(placeOnLogRail(medianLy, DISTANCE_RAIL_LY)!.fraction));
    // 26.17 ly sits at log10(26.17) / 4 of the rail
    expect(parseFloat(marker.style.left)).toBeCloseTo((Math.log10(medianLy) / 4) * 100, 2);

    const band = q('distance-rail-band')!;
    const lower = placeOnLogRail(8.011 * LY_PER_PC, DISTANCE_RAIL_LY)!.fraction;
    const upper = placeOnLogRail(8.039 * LY_PER_PC, DISTANCE_RAIL_LY)!.fraction;
    expect(band.style.left).toBe(percent(lower));
    expect(band.style.width).toBe(percent(upper - lower));

    expect(q('distance-rail-source')?.textContent).toBe('Bailer-Jones 2021 photogeometric · 16th to 84th percentile: 26.1 to 26.2 ly');
    const headline = q('target-card-distance-row')!.querySelector('.alb-scan-value')!;
    expect(headline.textContent).toBe('26.2 ly 8.02 pc');
  });
});

describe('values past a rail end (hud-5 fix)', () => {
  it('flags a 4,000 pc star past the 10,000 ly end instead of pinning it there, band ends included', () => {
    const session = createSyntheticCalibratedSession();
    const far = { ...asTabit(betelgeuseIn(session)), inferredDistancePc: { lower: 2900, median: 4000, upper: 5200, method: 'photogeometric_bailer_jones_edr3' as const } };
    renderCard({ session, selectedFinding: far });
    const markerEl = q('distance-rail-marker')!;
    expect(markerEl.getAttribute('data-off-scale')).toBe('above');
    expect(parseFloat(markerEl.style.left)).toBe(100);
    const band = q('distance-rail-band')!;
    // 2,900 pc is 9,459 ly, inside the rail; 5,200 pc is past it
    expect(band.getAttribute('data-lower-off-scale')).toBeNull();
    expect(band.getAttribute('data-upper-off-scale')).toBe('above');
    expect(q('distance-rail-off-scale')?.textContent).toBe('Past the 10,000 ly end of this scale');
  });

  it('flags a 45,000 K star past the strip and a 100,000 Sun star past the luminosity rail', () => {
    const session = createSyntheticCalibratedSession();
    const hot = asTabit(betelgeuseIn(session), {
      temperatureK: { median: 45000, lower: 43000, upper: 47000 },
      luminositySolar: { median: 1e5, lower: 8e4, upper: 1.2e5 },
    });
    renderCard({ session, selectedFinding: hot });
    expect(q('temperature-strip-marker')!.getAttribute('data-off-scale')).toBe('above');
    expect(q('temperature-strip-off-scale')?.textContent).toBe('Past the 40,000 K end of this scale');
    expect(q('luminosity-rail-marker')!.getAttribute('data-off-scale')).toBe('above');
    expect(q('luminosity-rail-band')!.getAttribute('data-lower-off-scale')).toBe('above');
    expect(q('luminosity-rail-off-scale')?.textContent).toBe('Past the 10,000 × Sun end of this scale');
  });

  it('flags a value below the low end too', () => {
    const session = createSyntheticCalibratedSession();
    const near = { ...asTabit(betelgeuseIn(session)), inferredDistancePc: { lower: 0.2, median: 0.25, upper: 0.3, method: 'photogeometric_bailer_jones_edr3' as const } };
    renderCard({ session, selectedFinding: near });
    expect(q('distance-rail-marker')!.getAttribute('data-off-scale')).toBe('below');
    expect(q('distance-rail-off-scale')?.textContent).toBe('Below the 1 ly end of this scale');
  });

  it('adds no off-scale note when everything sits on the rail', () => {
    const session = createSyntheticCalibratedSession();
    renderCard({ session, selectedFinding: asTabit(betelgeuseIn(session), TABIT_ASTRO) });
    for (const id of ['distance-rail', 'temperature-strip', 'luminosity-rail']) {
      expect(q(`${id}-off-scale`), id).toBeNull();
      expect(q(`${id}-marker`)!.getAttribute('data-off-scale'), id).toBeNull();
    }
  });
});

describe('temperature strip and luminosity scale (hud-5)', () => {
  it('draws neither, nor a RUWE caution, when the catalog reference carries no astrophysics', () => {
    const session = createSyntheticCalibratedSession();
    renderCard({ session, selectedFinding: asTabit(betelgeuseIn(session)) });
    expect(q('temperature-strip')).toBeNull();
    expect(q('luminosity-rail')).toBeNull();
    expect(q('target-card-ruwe-caution')).toBeNull();
  });

  it('draws both from the astrophysics block, with the Sun marked, and cautions on a loose position fit', () => {
    const session = createSyntheticCalibratedSession();
    renderCard({ session, selectedFinding: asTabit(betelgeuseIn(session), TABIT_ASTRO) });

    const strip = q('temperature-strip')!;
    expect(strip.textContent).toContain('6,154 K');
    expect(strip.textContent).toContain('6,150 to 6,158 K');
    expect(q('temperature-strip-marker')!.style.left).toBe(percent(placeOnLogRail(6154, TEMPERATURE_RAIL_K)!.fraction));
    expect(q('temperature-strip-sun')!.style.left).toBe(percent(placeOnLogRail(SUN_TEFF_K, TEMPERATURE_RAIL_K)!.fraction));
    expect(q('temperature-strip-bar')!.style.background).toContain('linear-gradient');

    const lum = q('luminosity-rail')!;
    expect(lum.textContent).toContain('2.85');
    expect(lum.textContent).toContain('2.84 to 2.87');
    expect(q('luminosity-rail-marker')!.style.left).toBe(percent(placeOnLogRail(2.85, LUMINOSITY_RAIL_SUN)!.fraction));
    expect([...lum.querySelectorAll('.alb-scan-tick-label')].map((t) => t.textContent)).toEqual(['0.01', '1', '100', '10,000']);

    expect(q('target-card-ruwe-caution')?.textContent).toContain('RUWE 3.93');
  });

  it('keeps quiet about RUWE at or under 1.4', () => {
    const session = createSyntheticCalibratedSession();
    renderCard({ session, selectedFinding: asTabit(betelgeuseIn(session), { ...TABIT_ASTRO, ruwe: 1.4 }) });
    expect(q('target-card-ruwe-caution')).toBeNull();
  });
});

describe('pixel crop (hud-5)', () => {
  const stillSession = (orientation?: (typeof EXIF_ORIENTATIONS)[1]): ObservationSession => {
    const base = createSyntheticCalibratedSession();
    return {
      ...base,
      sourceAsset: {
        ...base.sourceAsset!,
        mediaKind: 'image',
        mimeType: 'image/heic',
        width: 480,
        height: 360,
        ...(orientation ? { displayOrientation: orientation, metadataSources: { displayOrientation: { container: 'exif', tags: ['Orientation'] } } } : {}),
      },
    } as ObservationSession;
  };
  const placed = (img: HTMLImageElement) => [img.style.left, img.style.top, img.style.width, img.style.height].map((v) => parseFloat(v));
  const expectPlacement = (img: HTMLImageElement, want: { left: number; top: number; width: number; height: number }) => {
    const got = placed(img);
    [want.left, want.top, want.width, want.height].forEach((value, i) => expect(got[i]).toBeCloseTo(value, 2));
  };

  function loadCrop(naturalWidth: number, naturalHeight: number) {
    const img = q('target-card-pixel-crop')!.querySelector('img')!;
    Object.defineProperty(img, 'naturalWidth', { value: naturalWidth, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: naturalHeight, configurable: true });
    act(() => {
      img.dispatchEvent(new Event('load'));
    });
  }

  it('shows the uploaded still around the centroid when the photo is shown', () => {
    const session = stillSession();
    const finding = betelgeuseIn(session);
    renderCard({ session, selectedFinding: finding, sourceImageUrl: 'blob:photo' });
    const crop = q('target-card-pixel-crop')!;
    const img = crop.querySelector('img')!;
    const want = cropPlacement(finding.sourceDetection!, { width: 480, height: 360 }, undefined, 72, 12)!;
    expect(img.getAttribute('src')).toBe('blob:photo');
    expectPlacement(img, want);
    expect(crop.getAttribute('role')).toBe('img');
    expect(crop.getAttribute('aria-label')).toContain('your photo');
    loadCrop(480, 360);
    expect(q('target-card-pixel-crop')).not.toBeNull();
  });

  it('uses the upright size and the turned centroid for a photo stored on its side (EXIF Orientation 6)', () => {
    const session = stillSession(EXIF_ORIENTATIONS[6]);
    const finding = betelgeuseIn(session);
    renderCard({ session, selectedFinding: finding, sourceImageUrl: 'blob:portrait' });
    const img = q('target-card-pixel-crop')!.querySelector('img')!;
    const want = cropPlacement(finding.sourceDetection!, { width: 480, height: 360 }, EXIF_ORIENTATIONS[6], 72, 12)!;
    expectPlacement(img, want);
    expect(parseFloat(img.style.width)).toBe(2160);
    // The browser drew it upright, as 360 by 480: the crop stays
    loadCrop(360, 480);
    expect(q('target-card-pixel-crop')).not.toBeNull();
  });

  it('draws no crop when the browser shows the frame in another shape than its orientation says', () => {
    const session = stillSession(EXIF_ORIENTATIONS[6]);
    renderCard({ session, selectedFinding: betelgeuseIn(session), sourceImageUrl: 'blob:portrait' });
    loadCrop(480, 360);
    expect(q('target-card-pixel-crop')).toBeNull();
    expect(q('target-card-pixel-row')).not.toBeNull();
  });

  it('says what the photo stage says when the browser cannot draw the still', () => {
    const session = stillSession();
    renderCard({ session, selectedFinding: betelgeuseIn(session), sourceImageUrl: session.sourceAsset!.uri });
    act(() => {
      q('target-card-pixel-crop')!.querySelector('img')!.dispatchEvent(new Event('error'));
    });
    expect(q('target-card-pixel-crop')).toBeNull();
    const cardMessage = q('target-card-pixel-crop-unavailable')?.textContent;

    act(() => root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 1440, viewportHeightPx: 900 })));
    act(() => {
      (container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement).dispatchEvent(new Event('error'));
    });
    const stageMessage = q('source-photo-unavailable')?.textContent;
    expect(stageMessage).toBeTruthy();
    expect(cardMessage).toBe(stageMessage);
  });

  it('draws no crop for a clip or without a shown photo, and keeps the measured row', () => {
    const base = createSyntheticCalibratedSession();
    renderCard({ session: base, selectedFinding: betelgeuseIn(base), sourceImageUrl: 'blob:clip' });
    expect(q('target-card-pixel-crop')).toBeNull();
    expect(q('target-card-pixel-row')).not.toBeNull();

    const still = stillSession();
    renderCard({ session: still, selectedFinding: betelgeuseIn(still), sourceImageUrl: null });
    expect(q('target-card-pixel-crop')).toBeNull();
  });
});

describe('Go there key (hud-5)', () => {
  it('goes there on G when the star can be entered, never inside a field, with a modifier, or when Go there is off', () => {
    const session = createSyntheticCalibratedSession();
    const onRunUtterance = vi.fn();
    renderCard({ session, selectedFinding: betelgeuseIn(session), onRunUtterance });

    const g = press('g');
    expect(g.defaultPrevented).toBe(true);
    expect(onRunUtterance).toHaveBeenCalledWith('Take me to Betelgeuse', 'pointer');
    press('G', { shiftKey: true });
    expect(onRunUtterance).toHaveBeenCalledTimes(2);

    const owners = ['input', 'textarea', 'select', 'button'].map((tag) => document.createElement(tag));
    const editable = document.createElement('div');
    editable.contentEditable = 'true';
    Object.defineProperty(editable, 'isContentEditable', { value: true });
    document.body.append(...owners, editable);
    for (const el of [...owners, editable]) press('g', {}, el);
    for (const el of [...owners, editable]) el.remove();
    press('g', { metaKey: true });
    press('g', { ctrlKey: true });
    press('g', { altKey: true });
    press('g', { repeat: true });
    expect(onRunUtterance).toHaveBeenCalledTimes(2);

    renderCard({ session, selectedFinding: null, onRunUtterance });
    press('g');
    expect(onRunUtterance).toHaveBeenCalledTimes(2);

    const unconstrained = { ...betelgeuseIn(session), inferredDistancePc: undefined };
    renderCard({ session, selectedFinding: unconstrained, onRunUtterance });
    const ignored = press('g');
    expect(onRunUtterance).toHaveBeenCalledTimes(2);
    // The key stays free for anything else when Go there is off
    expect(ignored.defaultPrevented).toBe(false);
  });

  it('keeps Go there and G off for a star that is not a catalog match, even with a distance', () => {
    const session = createSyntheticCalibratedSession();
    const onRunUtterance = vi.fn();
    for (const evidenceClass of ['observed', 'predicted', 'simulated'] as const) {
      const star = { ...betelgeuseIn(session), evidenceClass } as CelestialFinding;
      renderCard({ session, selectedFinding: star, onRunUtterance });
      expect(q<HTMLButtonElement>('target-card-go-there')!.disabled, evidenceClass).toBe(true);
      press('g');
    }
    expect(onRunUtterance).not.toHaveBeenCalled();
  });
});

describe('SpatialObservatoryViewport wiring (hud-5)', () => {
  it('passes the selected star and the shown photo to the card once past the landing overlay', () => {
    const base = createSyntheticCalibratedSession();
    const betelgeuse = betelgeuseIn(base);
    const session: ObservationSession = {
      ...base,
      selectedFindingId: betelgeuse.id,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 480, height: 360 },
    };
    const onRunUtterance = vi.fn();
    const noop = () => {};
    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session,
          setSession: noop,
          selectedFinding: betelgeuse,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: 'blob:photo',
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance,
          onSelectFinding: noop,
          onPushToTalk: noop,
          onTriggerUpload: noop,
        })
      );
    });
    // An uploaded photo leaves the landing overlay on its own (SpatialObservatoryViewport), so the card shows at once
    expect(q('enter-observatory-btn')).toBeNull();
    expect(q('target-card-title')?.textContent).toBe('Betelgeuse');
    expect(q('target-card-pixel-crop')?.querySelector('img')?.getAttribute('src')).toBe('blob:photo');
    act(() => q<HTMLButtonElement>('target-card-go-there')!.click());
    expect(onRunUtterance).toHaveBeenCalledWith('Take me to Betelgeuse', 'pointer');
  });
});

describe('session selection (hud-5 fix)', () => {
  it("shows no card for App's fallback finding when the session has no selection", () => {
    const base = createSyntheticCalibratedSession();
    const noop = () => {};
    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session: { ...base, selectedFindingId: null },
          setSession: noop,
          selectedFinding: betelgeuseIn(base),
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onSelectFinding: noop,
          onPushToTalk: noop,
          onTriggerUpload: noop,
        })
      );
    });
    act(() => q<HTMLButtonElement>('enter-observatory-btn')!.click());
    expect(q('celestial-target-card')).toBeNull();
  });
});

describe('closing from the viewport (hud-5b)', () => {
  it('clears the session selection when the sheet is closed', () => {
    const base = createSyntheticCalibratedSession();
    const betelgeuse = betelgeuseIn(base);
    const session: ObservationSession = { ...base, selectedFindingId: betelgeuse.id };
    const setSession = vi.fn();
    const noop = () => {};
    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session,
          setSession,
          selectedFinding: betelgeuse,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onSelectFinding: noop,
          onPushToTalk: noop,
          onTriggerUpload: noop,
        })
      );
    });
    act(() => q<HTMLButtonElement>('enter-observatory-btn')!.click());
    setSession.mockClear();
    act(() => q<HTMLButtonElement>('target-card-close')!.click());
    expect(setSession).toHaveBeenCalledTimes(1);
    const update = setSession.mock.calls[0]![0] as (prev: ObservationSession) => ObservationSession;
    expect(update(session).selectedFindingId).toBeNull();
  });
});

describe('landing overlay (hud-5)', () => {
  it('keeps the card hidden on the landing overlay until Launch Albedian', () => {
    const base = createSyntheticCalibratedSession();
    const noop = () => {};
    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session: { ...base, selectedFindingId: betelgeuseIn(base).id },
          setSession: noop,
          selectedFinding: betelgeuseIn(base),
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onSelectFinding: noop,
          onPushToTalk: noop,
          onTriggerUpload: noop,
        })
      );
    });
    expect(q('celestial-target-card')).toBeNull();
    act(() => q<HTMLButtonElement>('enter-observatory-btn')!.click());
    expect(q('target-card-title')?.textContent).toBe('Betelgeuse');
    expect(q('target-card-pixel-crop')).toBeNull();
  });
});

describe('scan card CSS (hud-5)', () => {
  const start = cssSource.indexOf('MOMENT 05 CELESTIAL TARGET CARD');
  const end = cssSource.indexOf('/* ====', cssSource.indexOf('*/', start) + 2);
  const block = cssSource.slice(start, end);

  function rule(selector: string, within: string = block): Record<string, string> {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = new RegExp(`(^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`).exec(within);
    const out: Record<string, string> = {};
    for (const raw of (match?.[2] ?? '').split(';')) {
      const colon = raw.indexOf(':');
      if (colon > 0) out[raw.slice(0, colon).trim()] = raw.slice(colon + 1).trim();
    }
    return out;
  }

  function mediaBody(header: RegExp): string {
    const m = header.exec(block);
    if (!m) return '';
    let depth = 0;
    for (let i = m.index; i < block.length; i++) {
      if (block[i] === '{') depth++;
      else if (block[i] === '}' && --depth === 0) return block.slice(m.index, i + 1);
    }
    return '';
  }

  it('docks the card on the right in palette glass built from the HUD tokens, with no drawn border', () => {
    expect(start).toBeGreaterThan(0);
    const card = rule('.alb-target-card');
    expect(card.position).toBe('fixed');
    expect(card.right).toBe('24px');
    expect(card.background).toBe('var(--hud-glass-fill)');
    expect(card['backdrop-filter']).toBe('var(--hud-glass-filter)');
    expect(card['-webkit-backdrop-filter']).toBe('var(--hud-glass-filter)');
    expect(card['box-shadow']).toBe('var(--hud-shadow), var(--hud-hairline), var(--hud-specular)');
    expect(card['border-radius']).toBe('var(--hud-radius-palette)');
    expect(Object.keys(card).filter((k) => k === 'border' || (k.startsWith('border-') && k !== 'border-radius'))).toEqual([]);
  });

  it('sets every font from the HUD stacks and shows the photo crop with hard pixel edges', () => {
    const fonts = [...block.matchAll(/font-family:\s*([^;]+);/g)].map((m) => m[1]?.trim());
    expect(fonts.length).toBeGreaterThan(0);
    for (const font of fonts) expect(font).toMatch(/^var\(--hud-font-(sans|mono)\)$/);
    for (const m of block.matchAll(/\bfont:\s*([^;]+);/g)) expect(m[1]).toMatch(/var\(--hud-font-(sans|mono)\)$/);
    expect(rule('.alb-scan-crop')['image-rendering']).toBe('pixelated');
  });

  it('keeps the actions in view: the card is a column whose body scrolls and whose footer does not', () => {
    const card = rule('.alb-target-card');
    expect(card.display).toBe('flex');
    expect(card['flex-direction']).toBe('column');
    expect(card.overflow).toBe('hidden');
    const body = rule('.alb-scan-body');
    expect(body['overflow-y']).toBe('auto');
    expect(body['min-height']).toBe('0');
    expect(rule('.alb-scan-actions').flex).toBe('none');
  });

  it('draws the band in the highlight token', () => {
    const band = rule('.alb-scan-band');
    expect(band.background).toBe('var(--hud-highlight)');
    expect(band.opacity).toBe('0.35');
  });

  it('draws key chips from the HUD key tokens', () => {
    const kbd = rule('.alb-scan-action kbd');
    expect(kbd.background).toBe('var(--hud-key-fill)');
    expect(kbd.color).toBe('var(--hud-key-ink)');
  });

  it('turns into a bottom sheet on a phone', () => {
    const phone = mediaBody(/@media\s*\(max-width:\s*720px\)/);
    const sheet = rule('.alb-target-card', phone);
    expect(sheet.left).toBe('0');
    expect(sheet.right).toBe('0');
    expect(sheet.bottom).toBe('0');
    expect(sheet.top).toBe('auto');
  });
});

describe('catalog magnitude (integrity-25)', () => {
  it('shows no magnitude for a deep-sky object its catalog gives none for, and the magnitude when it does', () => {
    const session = createSyntheticCalibratedSession();
    // M31 as the real OpenNGC v20260501 row gives it (tests/fixtures/catalog_reference_contract.json)
    const m31: CelestialFinding = {
      id: 'finding-openngc-NGC0224',
      observationSolutionId: session.observationSolution!.id,
      evidenceClass: 'predicted',
      title: 'Andromeda Galaxy',
      subtitle: 'Galaxy · And · OpenNGC CC BY-SA 4.0',
      skyPosition: { raDeg: 10.684792, decDeg: 41.269056, uncertaintyArcsec: 1 },
      catalogRef: {
        id: 'openngc-NGC0224',
        provider: 'OpenNGC',
        releaseVersion: 'OpenNGC v20260501',
        catalogSourceId: 'NGC0224',
        commonName: 'Andromeda Galaxy',
        constellation: 'And',
        objectType: 'deep_sky',
        coordinateEpoch: 'J2000.0',
        raDeg: 10.684792,
        decDeg: 41.269056,
        citation: 'OpenNGC, Mattia Verga (github.com/mattiaverga/OpenNGC), CC BY-SA 4.0',
      },
      evidence: {
        id: 'ev-openngc-NGC0224',
        evidenceClass: 'predicted',
        verificationMethod: 'Catalog position projected through the solved WCS',
        limitations: ['Catalog position; not measured in the image'],
        verifiedByIndependentHoldout: false,
      },
    };
    const catalogLine = (finding: CelestialFinding) => {
      renderCard({ session, selectedFinding: finding });
      return q('target-card-catalog-row')?.querySelector('.alb-scan-catalog-line')?.textContent;
    };
    expect(catalogLine(m31)).toBe('And');
    expect(catalogLine({ ...m31, catalogRef: { ...m31.catalogRef!, apparentMagnitude: 3.44 } })).toBe('And · Mag 3.44');
  });
});
