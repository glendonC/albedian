import { DESIGNATION_CONSTELLATIONS, flamsteedLongDesignation, type CelestialFinding, type EvidenceClass, type ObservationSolution } from '@albedian/contracts';
import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';
import { findingCanEnterSpace } from '../components/askSuggestions.js';
import { RUWE_CAUTION_ABOVE, astrophysicsOf, formatSignificant } from '../components/scanScales.js';
import { deepSkyKindWords, deepSkyName, isDeepSky, type DeepSkyRow } from './deepSky.js';
import { onScreenWarning } from './revealStatus.js';

/**
 * What the picked star's card says (reveal, approved 2026-11-12, laptop screen 3), each fact from the finding itself:
 * its evidence class, its name, its designation and constellation, its catalog distance (in light years, to three figures) and its measured signal to
 * noise, the catalog row, and at the foot its own limitations (cautions, decided the same night). Each number carries
 * the class of where it came from (numberglyph, decided): a catalog distance is the catalog's, a signal to noise was
 * measured in the photo. A fact the finding does not have is left out.
 */

export interface StarCardNumber {
  /** The number as written. */
  value: string;
  /** Its unit, written small after it. */
  unit: string;
  /** The words under it. */
  label: string;
  /** The class of the number itself, drawn as its glyph before the words. */
  evidenceClass: EvidenceClass;
}

export interface StarCardFacts {
  evidenceClass: EvidenceClass;
  title: string;
  /** "Alpha Lyrae · Lyra": the designation when it is not the name, and the constellation in full; null for neither. */
  subLine: string | null;
  numbers: StarCardNumber[];
  /** "Hipparcos 2007 · HIP 91262": the catalog release and the row; null with no catalog row. */
  sourceLine: string | null;
  /** Whether the app can fly to it: a catalog match with a usable distance. Otherwise the card offers no Go there. */
  canGoThere: boolean;
  /** The finding's own limitations, word for word, then Gaia's caution when its fit for the star is loose. */
  notes: string[];
}

/** Distance methods that come from a star catalog (a parallax or a Bailer-Jones prior), not an ephemeris. */
const CATALOG_DISTANCE_METHODS: ReadonlySet<string> = new Set([
  'geometric_parallax',
  'photogeometric_bailer_jones_edr3',
  'geometric_bailer_jones_edr3',
]);

export function isCatalogDistanceMethod(method: string): boolean {
  return CATALOG_DISTANCE_METHODS.has(method);
}

/**
 * A constellation's name in full ("Lyra") for its code ("Lyr"), its Latin name ("Lyra"), or OpenNGC's Serpens Caput
 * and Cauda codes ("Se1", "Se2", packages/contracts/src/deep-sky.ts), from the contract's own table.
 */
const SERPENS = DESIGNATION_CONSTELLATIONS.find(([code]) => code === 'Ser')![1];
const CONSTELLATION_NAME: ReadonlyMap<string, string> = new Map([
  ...DESIGNATION_CONSTELLATIONS.flatMap(([code, name]): [string, string][] => [
    [code, name],
    [name, name],
  ]),
  ['Se1', SERPENS],
  ['Se2', SERPENS],
]);

export function constellationFullName(constellation: string): string | null {
  return CONSTELLATION_NAME.get(constellation) ?? null;
}

/**
 * A star with no name, whose title is its catalog number (INTERIM: .lead/uiux/UIUX-unnamed-QUESTION.md, waiting on the
 * owner): its catalog's name as the title ("Gaia DR3", "Hipparcos"), its number in that catalog on the small line.
 */
function namelessParts(finding: CelestialFinding): { title: string; number: string } | null {
  const ref = finding.catalogRef;
  if (!ref || finding.title !== ref.catalogSourceId) return null;
  const number = ref.catalogSourceId.split(/\s+/).at(-1) ?? ref.catalogSourceId;
  return { title: ref.provider, number };
}

export function starCardFacts(finding: CelestialFinding, deepSky: DeepSkyRow | null = null, solution?: ObservationSolution): StarCardFacts {
  const ref = finding.catalogRef;
  const deep = isDeepSky(finding);
  // A deep-sky object is titled by its own name or its NGC number (D23): never a nameless star's parts
  const nameless = deep ? null : namelessParts(finding);
  const rawDesignation =
    ref?.bayerDesignation ??
    (ref?.flamsteedNumber !== undefined ? flamsteedLongDesignation(ref.flamsteedNumber, ref.constellation) : null);
  const designation = rawDesignation !== null && rawDesignation !== finding.title ? rawDesignation : null;
  const constellation = ref ? constellationFullName(ref.constellation) : null;
  // A deep-sky object's small line is its type in words and its constellation ("Globular cluster · Centaurus", D23)
  const first = deep ? (deepSky ? deepSkyKindWords(deepSky.type) : null) : nameless ? nameless.number : designation;
  const subParts = [first, constellation].filter((part): part is string => part !== null);

  const numbers: StarCardNumber[] = [];
  // Its one number (D23): V as the catalog gives it, labelled "magnitude"; a catalog value, never more than the
  // finding's own class
  const vMag = ref?.apparentMagnitude;
  if (deep && vMag !== undefined && Number.isFinite(vMag)) {
    numbers.push({ value: String(vMag), unit: '', label: 'magnitude', evidenceClass: finding.evidenceClass === 'observed' ? 'catalog_matched' : finding.evidenceClass });
  }
  const distance = finding.inferredDistancePc;
  if (distance && isCatalogDistanceMethod(distance.method)) {
    numbers.push({
      // Three figures: "25.0" for the approved picture's Vega, "322" for a star 322 light years away
      value: formatSignificant(distance.median * PARSEC_TO_LIGHT_YEAR),
      unit: 'ly',
      label: 'away from Earth',
      // The catalog's value: catalog matched for a matched star, and never more than the finding's own class
      evidenceClass: finding.evidenceClass === 'observed' ? 'catalog_matched' : finding.evidenceClass,
    });
  }
  const snr = finding.sourceDetection?.snr;
  if (snr !== undefined && Number.isFinite(snr)) {
    numbers.push({ value: Math.round(snr).toLocaleString('en-US'), unit: '×', label: 'above the noise', evidenceClass: 'observed' });
  }

  // Each caution as it shows on screen: those C5's rule cuts by their first sentence (C24's place caution), the rest whole
  const notes = (finding.evidence?.limitations ?? []).map(onScreenWarning);
  // A prediction ends with the solve's own sentence for it (INTERIM: UIUX-predicted-pick-QUESTION.md), said once
  const predictedSaid = finding.evidenceClass === 'predicted' && finding.evidence?.verificationMethod ? `${finding.evidence.verificationMethod.replace(/\.$/, '')}.` : null;
  if (predictedSaid && !notes.includes(predictedSaid)) notes.push(predictedSaid);
  const ruwe = astrophysicsOf(ref)?.ruwe ?? null;
  if (ruwe !== null && ruwe > RUWE_CAUTION_ABOVE) {
    notes.push(`Gaia's position fit for this star is loose (RUWE ${ruwe.toFixed(2)}). Read the distance with care.`);
  }

  return {
    evidenceClass: finding.evidenceClass,
    // A deep-sky object's title is the name its mark reads (C19 item 3)
    title: nameless ? nameless.title : isDeepSky(finding) ? deepSkyName(finding.title) : finding.title,
    subLine: subParts.length > 0 ? subParts.join(' · ') : null,
    numbers,
    // A nameless star's title and small line already say what the catalog line would
    sourceLine: ref && !nameless ? `${ref.releaseVersion} · ${ref.catalogSourceId}` : null,
    // go-there-predicted-1: a predicted star's Go there needs the photo's pose estimate to show its ring
    canGoThere: findingCanEnterSpace(finding, solution),
    notes,
  };
}
