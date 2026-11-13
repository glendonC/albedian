import React, { useRef } from 'react';
import type { LookSizeName } from './actions.js';

export interface LookView {
  id: string;
  /** The view's word, exactly as the screen's mockup has it ("Your photo", "Real sky", "3D"). */
  label: string;
  disabled?: boolean;
}

export interface ViewPillsProps {
  /** The group's name for a screen reader. */
  label: string;
  views: readonly LookView[];
  current: string;
  onSelect: (id: string) => void;
  size?: LookSizeName;
}

const SIZE_CLASS: Readonly<Record<LookSizeName, string>> = {
  compact: 'look-views look-size-compact',
  home: 'look-views look-size-home',
  phone: 'look-views look-size-phone',
};

/**
 * The views as outlined pills joined edge to edge, the current one filled and pressed. Each is a real button: Tab
 * reaches the group, the arrow keys, Home and End move between the views that are on, Enter or Space chooses.
 */
export const ViewPills: React.FC<ViewPillsProps> = ({ label, views, current, onSelect, size = 'compact' }) => {
  const group = useRef<HTMLDivElement>(null);

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const buttons = [...(group.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])].filter((b) => !b.disabled);
    const at = buttons.indexOf(event.currentTarget);
    if (at < 0 || buttons.length === 0) return;
    const next =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? buttons[(at + 1) % buttons.length]
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? buttons[(at - 1 + buttons.length) % buttons.length]
          : event.key === 'Home'
            ? buttons[0]
            : event.key === 'End'
              ? buttons[buttons.length - 1]
              : undefined;
    if (!next) return;
    event.preventDefault();
    next.focus();
  };

  return (
    <div ref={group} className={SIZE_CLASS[size]} role="group" aria-label={label}>
      {views.map((view) => {
        const isCurrent = view.id === current;
        return (
          <button
            key={view.id}
            type="button"
            className={isCurrent ? 'look-views__item is-current' : 'look-views__item'}
            aria-pressed={isCurrent}
            disabled={view.disabled}
            onClick={() => onSelect(view.id)}
            onKeyDown={onKeyDown}
          >
            {view.label}
          </button>
        );
      })}
    </div>
  );
};
