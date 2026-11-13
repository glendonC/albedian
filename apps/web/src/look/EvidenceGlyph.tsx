import React from 'react';
import type { EvidenceClass } from '@albedian/contracts';
import { LOOK_GLYPHS } from '@albedian/design-system';

/** The glyph of an evidence class, in the class's colour (the colour of the text around it). Never shown alone. */
export const EvidenceGlyph: React.FC<{ evidenceClass: EvidenceClass }> = ({ evidenceClass }) => {
  const glyph = LOOK_GLYPHS[evidenceClass];
  return (
    <svg
      className="look-glyph"
      data-evidence-class={evidenceClass}
      viewBox={glyph.viewBox}
      aria-hidden="true"
      focusable="false"
    >
      {glyph.parts.map((part, index) =>
        part.kind === 'circle' ? (
          <circle
            key={index}
            r={part.r}
            fill={part.filled ? 'currentColor' : 'none'}
            stroke={part.filled ? undefined : 'currentColor'}
            strokeWidth={part.filled ? undefined : glyph.strokeWidth}
            strokeDasharray={part.dash}
          />
        ) : (
          <path key={index} d={part.d} fill="none" stroke="currentColor" strokeWidth={glyph.strokeWidth} />
        )
      )}
    </svg>
  );
};
