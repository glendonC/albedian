import { Logo } from '../look/index.js';

export interface OpenWaitProps {
  /** Until the upload's records are read: the app's black under the logo. Once read the photo frames in under it. */
  black: boolean;
  /** At phone width the app's black alone: the phone's reveal draws no logo, so none comes and goes (UIUX, open-direct-one). */
  phone: boolean;
}

/**
 * A page opened at an upload's address (open-direct-one, UIUX-opendirect-READY.md item 1): until the upload's records
 * are read, the app's black with the logo in its place and nothing else, no Upload, no drop frame, no home words, no
 * spinner and no count (there is no job to count; AGENTS.md rule 3). Once they are read the photo frames in where the
 * reveal places it and the logo stays where the reveal draws its own, until the reveal takes over. At phone width the
 * black stands alone until the photo comes in, with no logo: the phone's reveal draws none, so a logo there would appear
 * for a moment and go (UIUX's line for open-direct-one).
 */
export function OpenWait({ black, phone }: OpenWaitProps) {
  return (
    <div className={black ? 'open-wait is-black' : 'open-wait'} data-testid="open-wait">
      {!phone && (
        <span className="open-wait__word">
          <Logo sizePx={21} />
        </span>
      )}
    </div>
  );
}
