/**
 * ============================================================================
 * @albedian/web: push-to-talk through the browser's speech recognition
 * ============================================================================
 *
 * One capture: listen once, hand over the words, stop. The app is told the capture is asking when it starts,
 * and listening only when the browser opens the microphone (audiostart), so it never says listening while a
 * permission prompt is up. Every way a capture ends (words heard, an error, the browser ending the session with
 * nothing heard, or recognition refusing to start) reports the stop exactly once, so the app never stays in a
 * listening state with no capture running.
 *
 * Where the browser can say whether English runs on the device (SpeechRecognition.available, Chrome 139 and later), a
 * capture asks for on-device recognition when it is there, and asks for the language pack on the press when it can be
 * downloaded, so speech works with no internet after one download made at home (FIELD field-3). Otherwise, and in a
 * browser that cannot say, Chrome's recognition runs on a remote service, and with no internet a capture ends in a
 * 'network' error. The sentence for each ending says plainly why it stopped and that the typed command bar still
 * works, since every command runs the same way typed. Nothing on screen names where recognition runs.
 */

export const SPEECH_UNAVAILABLE =
  'Browser speech recognition unavailable in this environment; use the typed command bar below.';

/** The banner while the browser opens the microphone, and may be asking the user to allow it. */
export const SPEECH_ASKING = 'Opening the microphone. Allow it if the browser asks.';

/** The banner while the microphone is open. */
export const SPEECH_LISTENING = 'Listening (Push-to-Talk active)… Speak a command.';

/** The banner when words arrive after a new photo replaced the one they were spoken over. */
export const SPEECH_PREVIOUS_PHOTO =
  'Those words were spoken over the previous photo, so they were not applied to this one.';

const STOPPED = 'Speech recognition stopped. Type the command below.';

/**
 * The browser's error codes (Web Speech API, SpeechRecognitionErrorEvent.error) mapped to a plain sentence.
 * A code not listed here gets the general sentence.
 */
const BANNERS: Readonly<Record<string, string>> = {
  network: 'Speech recognition needs the internet in this browser, and it could not reach it. Type the command below.',
  'not-allowed': 'The microphone is not allowed for this page. Allow it in the browser, or type the command below.',
  'service-not-allowed':
    'The microphone is not allowed for this page. Allow it in the browser, or type the command below.',
  'no-speech': 'No speech was heard. Press push-to-talk and speak, or type the command below.',
  'audio-capture': 'No microphone was found. Connect one, or type the command below.',
  'language-not-supported': 'This browser cannot recognise speech in English. Type the command below.',
  aborted: 'Listening stopped.',
};

export function speechErrorBanner(code?: string): string {
  if (code === undefined || !Object.hasOwn(BANNERS, code)) return STOPPED;
  return BANNERS[code] ?? STOPPED;
}

export interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  /** Each result holds its alternatives; `isFinal` is false while the recogniser may still change it (absent: final). */
  onresult: ((ev: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }> }) => void) | null;
  onerror: ((ev: { error?: string }) => void) | null;
  onend: (() => void) | null;
  /** The browser opened the microphone, after any permission prompt (Web Speech API audiostart). */
  onaudiostart: (() => void) | null;
  /** Recognition must run on the device (Web Speech API processLocally); absent where the browser has none. */
  processLocally?: boolean;
  start(): void;
}

/** What a browser says about recognising a language on the device, and how it is asked for the pack (Web Speech API). */
interface OnDeviceOptions {
  langs: string[];
  processLocally?: boolean;
}
export type SpeechRecognitionConstructor = (new () => SpeechRecognitionLike) & {
  available?: (options: OnDeviceOptions) => Promise<string>;
  install?: (options: OnDeviceOptions) => Promise<boolean>;
};

const LANG = 'en-US';

export interface SpeechCaptureHandlers {
  /** The capture started; the microphone is not open yet, and the browser may be asking for it. */
  onAsking(): void;
  /** The microphone is open: the only point at which the app may say it is listening. */
  onListening(): void;
  /** Words were heard, trimmed and not empty. */
  onTranscript(text: string): void;
  /**
   * The capture ended. `banner` says why when no words were heard; it is null after words were heard, so
   * the banner the command itself wrote stays. Called exactly once per capture.
   */
  onStopped(banner: string | null): void;
  /**
   * The words as they are heard, all results in order, and whether the recogniser has settled the last of them (ask,
   * approved 2026-11-12: the listening button shows them). Given, the capture asks for results as they form.
   */
  onWords?(text: string, settled: boolean): void;
}

/**
 * Starts one capture with the browser's recognition constructor, or reports SPEECH_UNAVAILABLE when the
 * browser has none.
 */
export function startSpeechCapture(Recognition: SpeechRecognitionConstructor | undefined, handlers: SpeechCaptureHandlers): void {
  if (!Recognition) {
    handlers.onStopped(SPEECH_UNAVAILABLE);
    return;
  }

  let stopped = false;
  let heard = false;
  const stop = (banner: string | null) => {
    if (stopped) return;
    stopped = true;
    handlers.onStopped(banner);
  };

  const begin = (onDevice: boolean) => {
    let rec: SpeechRecognitionLike;
    try {
      rec = new Recognition();
      rec.lang = LANG;
      if (onDevice) rec.processLocally = true;
      rec.interimResults = handlers.onWords !== undefined;
      rec.onresult = (event) => {
        // One capture hands over its words once
        if (stopped || heard) return;
        const results = Array.from(event.results);
        const transcript = results.map((result) => result[0]?.transcript ?? '').join('').trim();
        if (!transcript) return;
        const settled = results.at(-1)?.isFinal !== false;
        handlers.onWords?.(transcript, settled);
        if (!settled) return;
        heard = true;
        handlers.onTranscript(transcript);
      };
      // After words were heard the command has already answered, so a late error or end keeps its banner.
      rec.onerror = (event) => stop(heard ? null : speechErrorBanner(event.error));
      rec.onend = () => stop(heard ? null : speechErrorBanner('no-speech'));
      rec.onaudiostart = () => {
        if (!stopped) handlers.onListening();
      };
      rec.start();
    } catch {
      stop(STOPPED);
    }
  };

  // A browser that cannot say where recognition runs starts at once, as before
  if (typeof Recognition.available !== 'function') {
    begin(false);
    if (!stopped) handlers.onAsking();
    return;
  }
  handlers.onAsking();
  const options: OnDeviceOptions = { langs: [LANG], processLocally: true };
  Recognition.available(options).then(
    (state) => {
      // Asked for on the press, which a download needs; this capture uses the browser's service meanwhile
      if (state === 'downloadable' && typeof Recognition.install === 'function') {
        Promise.resolve()
          .then(() => Recognition.install!(options))
          .catch(() => false);
      }
      begin(state === 'available');
    },
    () => begin(false)
  );
}
