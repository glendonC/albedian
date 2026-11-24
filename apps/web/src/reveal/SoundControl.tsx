import React, { useId } from 'react';
import { IconButton } from '../look/index.js';

/**
 * The sound control after the home (the owner's yes of 2026-11-18 20:39; .lead/uiux/UIUX-sound-READY.md): the approved
 * icon circle switches sound on or off, and while sound is on, pointing at the circle or focusing it shows the volume
 * capsule under it with the app's own sentence about what the sound is beside it. Nothing else: no word, no choice of
 * piece, no credits here (they are on the Credits page's Sound row).
 */
export interface SoundControlProps {
  on: boolean;
  onToggle: () => void;
  /** The volume, 0 to 1. */
  level: number;
  onLevel: (level: number) => void;
  /** What the sound is, word for word from the audio engine's own disclosure. */
  disclosure: string;
}

/** The volume line's height inside the capsule, in CSS px (styles/reveal.css). */
const LINE_PX = 64;
/** One press of an arrow key, of 100. */
const KEY_STEP = 10;

/** The level a height on the capsule stands for: the line's foot is none, its top is full, never past either. */
export function levelAtHeight(clientY: number, capsule: { top: number; height: number }): number {
  const foot = capsule.top + (capsule.height + LINE_PX) / 2;
  return Math.min(1, Math.max(0, (foot - clientY) / LINE_PX));
}

export const SoundControl: React.FC<SoundControlProps> = ({ on, onToggle, level, onLevel, disclosure }) => {
  const noteId = useId();
  const percent = Math.round(Math.min(1, Math.max(0, level)) * 100);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Home') {
      event.preventDefault();
      onLevel(0);
      return;
    }
    if (event.key === 'End') {
      event.preventDefault();
      onLevel(1);
      return;
    }
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    const next = percent + (event.key === 'ArrowUp' ? KEY_STEP : -KEY_STEP);
    onLevel(Math.min(100, Math.max(0, next)) / 100);
  };

  return (
    <div className={`sound-control ${on ? 'is-on' : 'is-off'}`}>
      {/* The icon says on or off; the circle fills only on hover or press, so no `pressed` look */}
      <IconButton
        label="Sound"
        icon={on ? 'speaker-high' : 'speaker-slash'}
        aria-pressed={on}
        aria-describedby={on ? noteId : undefined}
        onClick={onToggle}
        data-audio-control="toggle"
      />
      {on && (
        <div className="sound-control__pop">
          <p id={noteId} role="note">
            {disclosure}
          </p>
          <div
            className="sound-control__volume"
            role="slider"
            aria-label="Volume"
            aria-orientation="vertical"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            tabIndex={0}
            onClick={(event) => onLevel(levelAtHeight(event.clientY, event.currentTarget.getBoundingClientRect()))}
            onKeyDown={onKeyDown}
          >
            <span>
              <i style={{ height: `${percent}%` }} />
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
