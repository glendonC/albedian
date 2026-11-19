import { DESIGNATION_CONSTELLATIONS, type CelestialFinding, type EvidenceClass } from '@albedian/contracts';
import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';
import { findingCanEnterSpace } from '../components/askSuggestions.js';
import { RUWE_CAUTION_ABOVE, astrophysicsOf, formatSignificant } from '../components/scanScales.js';

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
const CATALOG_DISTANCE_METHODS: ReadonlySet<string> = new Set(['geometric_parallax', 'photogeometric_bailer_jones_edr3']);

/** A constellation's name in full ("Lyra") for its code ("Lyr"), from the contract's own table. */
const CONSTELLATION_NAME: ReadonlyMap<string, string> = new Map(DESIGNATION_CONSTELLATIONS.map(([code, name]) => [code, name]));

export function starCardFacts(finding: CelestialFinding): StarCardFacts {
  const ref = finding.catalogRef;
  const designation = ref?.bayerDesignation !== undefined && ref.bayerDesignation !== finding.title ? ref.bayerDesignation : null;
  const constellation = ref ? (CONSTELLATION_NAME.get(ref.constellation) ?? null) : null;
  const subParts = [designation, constellation].filter((part): part is string => part !== null);

  const numbers: StarCardNumber[] = [];
  const distance = finding.inferredDistancePc;
  if (distance && CATALOG_DISTANCE_METHODS.has(distance.method)) {
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

  const notes = [...(finding.evidence?.limitations ?? [])];
  const ruwe = astrophysicsOf(ref)?.ruwe ?? null;
  if (ruwe !== null && ruwe > RUWE_CAUTION_ABOVE) {
    notes.push(`Gaia's position fit for this star is loose (RUWE ${ruwe.toFixed(2)}). Read the distance with care.`);
  }

  return {
    evidenceClass: finding.evidenceClass,
    title: finding.title,
    subLine: subParts.length > 0 ? subParts.join(' · ') : null,
    numbers,
    sourceLine: ref ? `${ref.releaseVersion} · ${ref.catalogSourceId}` : null,
    canGoThere: findingCanEnterSpace(finding),
    notes,
  };
}
