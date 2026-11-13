import React from 'react';
import { LookIcon, type LookIconName } from './Icon.js';

/** The control sizes: the laptop's compact controls, the laptop home's one action, the iPhone web view's. */
export type LookSizeName = 'compact' | 'home' | 'phone';
const SIZE_CLASS: Readonly<Record<LookSizeName, string>> = {
  compact: 'look-size-compact',
  home: 'look-size-home',
  phone: 'look-size-phone',
};

type ButtonProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'type' | 'className'>;
const join = (...names: Array<string | false | undefined>) => names.filter(Boolean).join(' ');

export interface MainActionProps extends ButtonProps {
  /** The word, exactly as the screen's mockup has it. */
  label: string;
  icon?: LookIconName;
  size?: LookSizeName;
}

/**
 * The screen's one main action: a filled pill as wide as its word, and a circle apart that springs into it on hover
 * while the arrow turns. There is one per screen.
 */
export const MainAction: React.FC<MainActionProps> = ({ label, icon = 'arrow-right', size = 'compact', ...rest }) => (
  <button type="button" className={join('look-main', SIZE_CLASS[size])} {...rest}>
    <span className="look-main__label">{label}</span>
    <span className="look-main__circle" aria-hidden="true">
      <LookIcon name={icon} turn={icon === 'arrow-right'} />
    </span>
  </button>
);

/** A main action inside a crowded place: a filled pill with the dark circle inside it. */
export const SmallMainAction: React.FC<MainActionProps> = ({ label, icon = 'arrow-right', size = 'compact', ...rest }) => (
  <button type="button" className={join('look-small-main', SIZE_CLASS[size])} {...rest}>
    {label}
    <span className="look-small-main__circle" aria-hidden="true">
      <LookIcon name={icon} turn={icon === 'arrow-right'} />
    </span>
  </button>
);

export interface SecondActionProps extends ButtonProps {
  label: string;
  /** When given, the pill carries a filled circle holding the icon (Ask out loud's microphone). */
  icon?: LookIconName;
  size?: LookSizeName;
}

/** A second action: an outlined pill. */
export const SecondAction: React.FC<SecondActionProps> = ({ label, icon, size = 'compact', ...rest }) => (
  <button type="button" className={join('look-second', icon && 'look-second--with-circle', SIZE_CLASS[size])} {...rest}>
    {label}
    {icon && (
      <span className="look-second__circle" aria-hidden="true">
        <LookIcon name={icon} />
      </span>
    )}
  </button>
);

export interface IconButtonProps extends ButtonProps {
  /** The button's name for a screen reader: it shows no word. */
  label: string;
  icon: LookIconName;
  /** For a switch (sound on or off): pressed shows filled and says so; leave it out for a plain button. */
  pressed?: boolean;
  size?: LookSizeName;
}

/** An icon only: an outlined circle the controls' height, filled on hover or while on. */
export const IconButton: React.FC<IconButtonProps> = ({ label, icon, pressed, size = 'compact', ...rest }) => (
  <button
    type="button"
    className={join('look-icon', pressed && 'is-on', SIZE_CLASS[size])}
    aria-label={label}
    aria-pressed={pressed}
    {...rest}
  >
    <LookIcon name={icon} />
  </button>
);
