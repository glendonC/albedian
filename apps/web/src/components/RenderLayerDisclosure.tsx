import React, { useId, useState } from 'react';
import type { RenderLayerSet } from '@albedian/contracts';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';
import { creditsFor } from './layerCredits.js';

export interface RenderLayerDisclosureProps {
  layers: RenderLayerSet;
}

/**
 * What the renderer is drawing and what kind of evidence each layer is (AGENTS.md section 1):
 * always visible as one line, expandable to every layer's evidence class and disclosure. The
 * credits of the drawn data and imagery show under it without a click.
 */
export const RenderLayerDisclosure: React.FC<RenderLayerDisclosureProps> = ({ layers }) => {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (layers.length === 0) return null;
  const allSimulated = layers.every((layer) => layer.evidenceClass === 'simulated');
  const summary = `${allSimulated ? 'Modeled scene' : 'Mixed evidence'} · ${layers.length} layers`;
  const credits = creditsFor(layers);

  return (
    <aside className="alb-layer-disclosure" data-hud-interactive="true" aria-label="What this view shows">
      <button
        type="button"
        className="alb-layer-disclosure-toggle"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
      >
        {summary}
      </button>
      {credits.length > 0 && <p className="alb-layer-credits">{credits.join(' · ')}</p>}
      {open && (
        <ul id={listId} className="alb-layer-disclosure-list">
          {layers.map((layer) => {
            const badge = getEpistemicBadgeDescriptor(layer.evidenceClass);
            return (
              <li key={layer.id} className="alb-layer-disclosure-item">
                <span className="alb-layer-disclosure-code" title={badge.label}>
                  {badge.shortCode}
                </span>
                <span className="alb-layer-disclosure-title">{layer.title}</span>
                <span className="alb-layer-disclosure-text">{layer.disclosure}</span>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
};
