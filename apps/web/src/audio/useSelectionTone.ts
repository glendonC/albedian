import { useEffect } from 'react';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { astrophysicsOf } from '../components/scanScales.js';
import { MusicSelectionStore, appMusicSelection, type MusicSelection } from './music/selectionTone.js';

export { MusicSelectionStore };

/**
 * What the music hears of a selected finding: its evidence class, its catalog temperature (the Gaia
 * DR3 GSP-Phot value in its catalog reference's astrophysics block, as the star card reads it) and
 * its pan from its position on the photo. Nothing it lacks is filled in.
 */
export function selectionOf(finding: CelestialFinding | null, session: ObservationSession): MusicSelection | null {
  if (!finding) return null;
  const width = session.sourceAsset?.width;
  const x = finding.sourceDetection?.xPx;
  return {
    id: finding.id,
    evidenceClass: finding.evidenceClass,
    temperatureK: astrophysicsOf(finding.catalogRef)?.temperatureK?.median ?? null,
    panX: typeof x === 'number' && typeof width === 'number' && width > 0 ? Math.max(-1, Math.min(1, (x / width) * 2 - 1)) : null,
  };
}

/** The viewport's hook: each newly selected finding goes to the page store, which the app's engine follows. */
export function useSelectionTone(finding: CelestialFinding | null, session: ObservationSession, store: MusicSelectionStore = appMusicSelection): void {
  const selection = selectionOf(finding, session);
  const id = selection?.id ?? null;
  // Keyed on the id: only a different finding (or none) is a new selection
  useEffect(() => store.set(selection), [store, id]);
}
