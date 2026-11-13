import React from 'react';
import type { EvidenceClass } from '@albedian/contracts';
import { LOOK_EVIDENCE, lookEvidenceDescription } from '@albedian/design-system';
import { EvidenceGlyph } from './EvidenceGlyph.js';

/** Each class's modifier, named whole so the stylesheet guard can find it. */
const TAG_CLASS: Readonly<Record<EvidenceClass, string>> = {
  observed: 'look-tag look-tag--observed',
  catalog_matched: 'look-tag look-tag--catalog',
  predicted: 'look-tag look-tag--predicted',
  simulated: 'look-tag look-tag--simulated',
};

/**
 * An evidence class as the approved soft tag: its glyph and its word, always both, so colour is never the only cue.
 * The evidence package's long label and disclosure are the tag's title.
 */
export const EvidenceTag: React.FC<{ evidenceClass: EvidenceClass }> = ({ evidenceClass }) => (
  <span className={TAG_CLASS[evidenceClass]} data-evidence-class={evidenceClass} title={lookEvidenceDescription(evidenceClass)}>
    <EvidenceGlyph evidenceClass={evidenceClass} />
    {LOOK_EVIDENCE[evidenceClass].word}
  </span>
);
