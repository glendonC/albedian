import React from 'react';
import type { SampleNight } from '@albedian/contracts';

/**
 * sample-reveal-one (UIUX-sample-reveal-READY.md item 3, RULED at the lead's line stamped 03:48 on 27 Nov): the sample
 * photo's courtesy credit in Commons' suggested form, author / the source's site / licence, from the sample's own record
 * ("YagnKambariya / Wikimedia Commons / CC0"), Geist 12 at ink 66 percent on one line, a link to the sample's source page
 * in a new tab styled as the Credits page's links. It stands with the photo view only, since it credits the photo.
 * Laptop: on the Credits link's line, its right edge 24 px left of the link. Phone: right-aligned 24 px from the edge,
 * under the photo's foot.
 */

/** The sites a sample's source page may stand on, by host, named as their own credit lines name them. */
const SOURCE_SITES: Readonly<Record<string, string>> = {
  'commons.wikimedia.org': 'Wikimedia Commons',
};

/** The credit line from the sample's record, or null when its source's site has no name here (nothing is made up). */
export function sampleCreditLine(night: SampleNight): string | null {
  let host = '';
  try {
    host = new URL(night.credit.sourceUrl).host;
  } catch {
    return null;
  }
  const site = SOURCE_SITES[host];
  return site ? `${night.credit.author} / ${site} / ${night.credit.licence}` : null;
}

/** The gap between the credit's right edge and the Credits link on the laptop. */
export const SAMPLE_CREDIT_GAP_PX = 24;

export const SampleCredit: React.FC<{
  night: SampleNight;
  /** Laptop: the window's right edge to the credit's right edge (the Credits link's right, its width and the gap). */
  rightPx?: number;
  /** Phone: the photo's foot in the window, where the credit's line stands. */
  topPx?: number;
}> = ({ night, rightPx, topPx }) => {
  const line = sampleCreditLine(night);
  if (line === null) return null;
  const phone = topPx !== undefined;
  return (
    <a
      className={phone ? 'reveal-sample-credit is-phone' : 'reveal-sample-credit'}
      data-testid="sample-credit"
      data-hud-interactive="true"
      href={night.credit.sourceUrl}
      target="_blank"
      rel="noopener noreferrer"
      style={phone ? { top: `${topPx}px` } : { right: `${rightPx ?? 0}px` }}
    >
      {line}
    </a>
  );
};
