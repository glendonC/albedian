import React, { useEffect, useRef, useState } from 'react';
import { IconButton, LookIcon, Logo, MainAction, SecondAction } from '../look/index.js';
import { CreditsPage } from './CreditsPage.js';
import { nameParts, refusalSentence, shortFileName, type HomeRefusal } from './homeRefusal.js';
import { homeBackdropStyle, homeShadeStyle } from './homeBackdrop.js';
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/home.css';

export interface HomeScreenProps {
  soundOn: boolean;
  onToggleSound: () => void;
  /** A photo or clip chosen with the home's picker. */
  onFileChosen: (file: File) => void;
  /** Told when Upload is pressed, as the picker opens. */
  onUploadPressed?: () => void;
  /** The last upload the server refused, shown beside Upload until the next one; none when nothing was refused. */
  refusal?: HomeRefusal | null;
}

/**
 * The approved home (arrive, 2026-11-12): the Milky Way the app ships, darkened toward the bottom left, the logo at
 * 132 px with Upload under it, and Credits and the sound circle at the top right. Credits opens in its place and its
 * back button returns here. A refused upload shows beside Upload as approved the same day. Nothing on the home moves,
 * counts or spins.
 */
export const HomeScreen: React.FC<HomeScreenProps> = ({
  soundOn,
  onToggleSound,
  onFileChosen,
  onUploadPressed,
  refusal = null,
}) => {
  const [creditsOpen, setCreditsOpen] = useState(false);
  // The status region is on screen empty first and gets its words after, so a screen reader announces them
  const [shownRefusal, setShownRefusal] = useState<HomeRefusal | null>(null);
  useEffect(() => setShownRefusal(refusal), [refusal]);
  const reason = refusalSentence(shownRefusal?.detail ?? null);
  const name = nameParts(shortFileName(shownRefusal?.fileName ?? ''));
  const fileInput = useRef<HTMLInputElement>(null);
  const home = useRef<HTMLDivElement>(null);
  const returnFocus = useRef(false);

  // Back from Credits puts the keyboard where it was, on Credits
  useEffect(() => {
    if (creditsOpen || !returnFocus.current) return;
    returnFocus.current = false;
    home.current?.querySelector<HTMLButtonElement>('.home-top .look-second')?.focus();
  }, [creditsOpen]);

  if (creditsOpen) {
    return (
      <CreditsPage
        onBack={() => {
          returnFocus.current = true;
          setCreditsOpen(false);
        }}
      />
    );
  }

  return (
    <div className="home-screen" data-testid="home-screen" data-hud-interactive="true" ref={home}>
      <div className="home-sky" style={homeBackdropStyle()} aria-hidden="true" />
      <div className="home-shade" style={homeShadeStyle()} aria-hidden="true" />
      <div className="home-top">
        <SecondAction label="Credits" onClick={() => setCreditsOpen(true)} />
        <IconButton label="Sound" icon="speaker-high" pressed={soundOn} onClick={onToggleSound} data-audio-control="toggle" />
      </div>
      <div className="home-hero">
        <Logo sizePx={132} />
        <div className="home-row">
          <MainAction
            label="Upload"
            icon="upload-simple"
            size="home"
            onClick={() => {
              onUploadPressed?.();
              fileInput.current?.click();
            }}
          />
          <div className="home-refusal" role="status">
            {shownRefusal && (
              <>
                <p className="home-refusal__file">
                  <LookIcon name="warning-circle" />
                  <span className="home-refusal__words">
                    <span className="home-refusal__open">{"Albedian can't open "}</span>
                    <span className="home-refusal__name">
                      <span className="home-refusal__head">{name.head}</span>
                      <span className="home-refusal__tail">{name.tail}</span>
                    </span>
                  </span>
                </p>
                {reason && <p className="home-refusal__reason">{reason}</p>}
              </>
            )}
          </div>
        </div>
      </div>
      <input
        ref={fileInput}
        className="home-file-input"
        data-testid="home-file-input"
        type="file"
        accept="image/*,video/*"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          // The same file chosen again still counts as a choice
          event.currentTarget.value = '';
          if (file) onFileChosen(file);
        }}
      />
    </div>
  );
};
