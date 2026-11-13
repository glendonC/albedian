import React, { useId } from 'react';
import { LOOK_LOGO } from '@albedian/design-system';

/**
 * The name, always as the logo, as apps/web draws it in its header: "albedian" in Geist 600 and the moon with its
 * star cut out at the shoulder. The size is the type's; the mark follows it. Each logo has its own mask id.
 */
export const Logo: React.FC<{ sizePx?: number }> = ({ sizePx = LOOK_LOGO.defaultSizePx }) => {
  const maskId = `look-logo-mask-${useId().replace(/:/g, '')}`;
  return (
    <span
      className="look-logo"
      role="img"
      aria-label="Albedian"
      style={{ '--look-logo-fs': `${sizePx}px` } as React.CSSProperties}
    >
      <span className="look-logo__word" aria-hidden="true">
        albedian
      </span>
      <svg className="look-logo__mark" viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
        <defs>
          <mask id={maskId}>
            <rect width="32" height="32" fill="white" />
            <path d="M18.2 2.2Q18.2 13.8 29.8 13.8Q18.2 13.8 18.2 25.4Q18.2 13.8 6.6 13.8Q18.2 13.8 18.2 2.2Z" fill="black" />
          </mask>
        </defs>
        <circle cx="15.2" cy="16.0" r="11.5" fill="currentColor" mask={`url(#${maskId})`} />
      </svg>
    </span>
  );
};
