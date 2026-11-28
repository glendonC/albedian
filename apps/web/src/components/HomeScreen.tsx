import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { resolveMotionTokens } from '@albedian/design-system';
import { blockLeaveMs, reducedMotion, useLeaving } from '../hooks/useLeaving.js';
import { IconButton, LookIcon, Logo, MainAction, SecondAction } from '../look/index.js';
import { ConnectPhonePage } from './ConnectPhonePage.js';
import { CreditsPage } from './CreditsPage.js';
import { nameParts, refusalSentence, shortFileName, type HomeRefusal } from './homeRefusal.js';
import { homeBackdropStyle, homeShadeStyle } from './homeBackdrop.js';
import { carriesFiles, droppedFile } from './homeDrop.js';
import { servedPhoneAddress } from './phoneAddress.js';
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/home.css';

/** ESO's credit for the home's panorama, unaltered ("Credit: ESO/S. Brunier"), after the app's name for the picture. */
export const HOME_CREDIT = 'Milky Way: ESO/S. Brunier';
/** ESO's own page for the image (eso0932a), where its credit links when online, as ESO asks. */
export const ESO_IMAGE_PAGE = 'https://www.eso.org/public/images/eso0932a/';

export interface HomeScreenProps {
  soundOn: boolean;
  onToggleSound: () => void;
  /** A photo or clip chosen with the home's picker. */
  onFileChosen: (file: File) => void;
  /** Told when Upload is pressed, as the picker opens. */
  onUploadPressed?: () => void;
  /** The last upload the server refused, shown beside Upload until the next one; none when nothing was refused. */
  refusal?: HomeRefusal | null;
  /**
   * Try a sample night, pressed (sample-pill-one, UIUX-first-use-wow-READY.md item 2 and P1 to P5): given only once the
   * sample's asset and its kept solve both answer; none draws no pill and nothing takes its place.
   */
  onTrySample?: () => void;
  /**
   * phone-code-home-one (UIUX-phone-code-READY.md Q3): the phone's address offered as a code, only while the field start
   * serves the network and only at the laptop's own host (phoneAddress.ts); read from the build when not given, null
   * for none: no Connect phone and nothing takes its place.
   */
  phoneAddress?: string | null;
}

/**
 * The approved home (arrive, 2026-11-12): the Milky Way the app ships, darkened toward the bottom left, the logo at
 * 132 px with Upload under it, and Credits and the sound circle at the top right. Credits opens in its place and its
 * back button returns here. A refused upload shows beside Upload as approved the same day. A photo dragged onto the
 * window dims the home behind "Drop to upload" (the same approval's second screen), and a drop takes the picker's
 * path. Nothing on the home moves, counts or spins.
 */
export const HomeScreen: React.FC<HomeScreenProps> = ({
  soundOn,
  onToggleSound,
  onFileChosen,
  onUploadPressed,
  refusal = null,
  onTrySample,
  phoneAddress,
}) => {
  const offered = phoneAddress === undefined ? servedPhoneAddress() : phoneAddress;
  // P2: there from the first frame when the sample answered before the home was drawn; when it answers later, the pill
  // words in as it mounts, as Fly on your own does
  const [sampleAtFirst] = useState(() => onTrySample !== undefined);
  const [creditsOpen, setCreditsOpen] = useState(false);
  const [phoneOpen, setPhoneOpen] = useState(false);
  // Credits or Connect phone stands in the home's place
  const pageOpen = creditsOpen || phoneOpen;
  // The status region is on screen empty first and gets its words after, so a screen reader announces them
  const [shownRefusal, setShownRefusal] = useState<HomeRefusal | null>(null);
  useEffect(() => setShownRefusal(refusal), [refusal]);
  const reason = refusalSentence(shownRefusal?.detail ?? null);
  const name = nameParts(shortFileName(shownRefusal?.fileName ?? ''));
  const fileInput = useRef<HTMLInputElement>(null);
  const home = useRef<HTMLDivElement>(null);
  const credit = useRef<HTMLAnchorElement>(null);
  // The pill whose page has just closed, which takes the focus back (Credits or Connect phone)
  const returnFocus = useRef<'Credits' | 'Connect phone' | null>(null);
  // A file held over the window: how many parts of the window it has entered and not yet left (a browser sends a
  // child's enter before its parent's leave), so the overlay stays while the file crosses the window
  const [fileOver, setFileOver] = useState(false);
  // A file leaving the window lets the overlay leave (words out, frame out: the motion record); a drop takes it with
  // the home, and Credits in the home's place, at once
  const [goneAtOnce, setGoneAtOnce] = useState(false);
  const drop = useLeaving(fileOver, blockLeaveMs(1, resolveMotionTokens(reducedMotion())));
  const credits = useLeaving(creditsOpen, blockLeaveMs(1, resolveMotionTokens(reducedMotion())));
  const phonePage = useLeaving(phoneOpen, blockLeaveMs(1, resolveMotionTokens(reducedMotion())));
  const entered = useRef(0);
  const chosen = useRef(onFileChosen);
  chosen.current = onFileChosen;

  // Anywhere on the window takes a dropped photo while the home shows (approved 2026-11-12); a drag that holds no
  // file is left to the browser. Credits stands in the home's place, so it takes none
  useEffect(() => {
    if (pageOpen) return undefined;
    const close = () => {
      entered.current = 0;
      setGoneAtOnce(true);
      setFileOver(false);
    };
    const onEnter = (event: DragEvent) => {
      if (!carriesFiles(event.dataTransfer)) return;
      entered.current += 1;
      setGoneAtOnce(false);
      setFileOver(true);
    };
    const onOver = (event: DragEvent) => {
      if (!carriesFiles(event.dataTransfer)) return;
      // Without this a browser refuses the drop
      event.preventDefault();
      event.dataTransfer!.dropEffect = 'copy';
    };
    const onLeave = (event: DragEvent) => {
      if (!carriesFiles(event.dataTransfer)) return;
      entered.current = Math.max(0, entered.current - 1);
      if (entered.current === 0) setFileOver(false);
    };
    const onDrop = (event: DragEvent) => {
      if (!carriesFiles(event.dataTransfer)) return;
      // Without this a browser leaves the app to open the file itself
      event.preventDefault();
      const file = droppedFile(event.dataTransfer);
      close();
      if (file) chosen.current(file);
    };
    window.addEventListener('dragenter', onEnter);
    window.addEventListener('dragover', onOver);
    window.addEventListener('dragleave', onLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragenter', onEnter);
      window.removeEventListener('dragover', onOver);
      window.removeEventListener('dragleave', onLeave);
      window.removeEventListener('drop', onDrop);
      close();
    };
  }, [pageOpen]);

  // refusal-credit-gap-one (UIUX-homecredit-READY.md, its last addition): the credit stays whole where it is and a
  // refused upload's message ends 22 px before it, so the credit's drawn width goes onto the home for the message's
  // max-width; read again when its size changes (the web font arriving after the first frame)
  useLayoutEffect(() => {
    const line = credit.current;
    const screen = home.current;
    if (pageOpen || !line || !screen) return undefined;
    const measure = () => {
      const width = line.getBoundingClientRect().width;
      if (width > 0) screen.style.setProperty('--home-credit-w', `${Math.ceil(width)}px`);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const watch = new ResizeObserver(measure);
    watch.observe(line);
    return () => watch.disconnect();
  }, [pageOpen]);

  // Back from a page puts the keyboard where it was, on the pill that opened it
  useEffect(() => {
    const pill = returnFocus.current;
    if (pageOpen || !pill) return;
    returnFocus.current = null;
    [...(home.current?.querySelectorAll<HTMLButtonElement>('.home-top .look-second') ?? [])].find((b) => b.textContent === pill)?.focus();
  }, [pageOpen]);

  // Credits in the home's place; Back brings the home back at once beneath it while the page leaves over it (the
  // motion record: its words out, the page frame out), the same page, read once
  const creditsPage = credits.mounted ? (
    <CreditsPage
      leaving={credits.leaving}
      onBack={() => {
        returnFocus.current = 'Credits';
        setCreditsOpen(false);
      }}
    />
  ) : null;
  // Connect phone's page, as Credits' (UIUX-phone-code-READY.md Q3: in the Credits page's frame, leaving as it leaves)
  const phoneCodePage =
    phonePage.mounted && offered ? (
      <ConnectPhonePage
        address={offered}
        leaving={phonePage.leaving}
        onBack={() => {
          returnFocus.current = 'Connect phone';
          setPhoneOpen(false);
        }}
      />
    ) : null;

  return (
    <>
    {!pageOpen && (
    <div className="home-screen" data-testid="home-screen" data-hud-interactive="true" ref={home}>
      <div className="home-sky" style={homeBackdropStyle()} aria-hidden="true" />
      <div className="home-shade" style={homeShadeStyle()} aria-hidden="true" />
      <div className="home-top">
        {offered && <SecondAction label="Connect phone" onClick={() => setPhoneOpen(true)} />}
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
          {onTrySample && (
            // P1: SecondAction at the home size, 10 px after Upload on its row; P5: it stays while a refusal shows
            <SecondAction label="Try a sample night" size="home" part={sampleAtFirst ? 'home-sample' : 'home-sample is-arriving'} onClick={() => fileOver || onTrySample()} />
          )}
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
      {/* ESO's credit carried with the Milky Way it draws (home-credit-one, UIUX-homecredit-READY.md): ESO's words
          unaltered after the picture's name, one link to ESO's page for the image, in the corner each width leaves free */}
      <a ref={credit} className="home-credit" href={ESO_IMAGE_PAGE} target="_blank" rel="noopener noreferrer">
        {HOME_CREDIT}
      </a>
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
      {drop.mounted && !(drop.leaving && goneAtOnce) && (
        <div className={drop.leaving ? 'home-drop is-leaving' : 'home-drop'} data-testid="home-drop">
          <div className="home-drop__frame" aria-hidden="true" />
          <div className="home-drop__mid">
            <span className="home-drop__circle" aria-hidden="true">
              <LookIcon name="upload-simple" />
            </span>
            <span className="home-drop__words">Drop to upload</span>
          </div>
        </div>
      )}
    </div>
    )}
    {creditsPage}
    {phoneCodePage}
    </>
  );
};
