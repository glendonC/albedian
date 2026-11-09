/**
 * ============================================================================
 * @albedian/web: push-to-talk through the browser's speech recognition
 * ============================================================================
 *
 * One capture: listen once, hand over the words, stop. Every way a capture ends (words heard, an error, the
 * browser ending the session with nothing heard, or recognition refusing to start) reports the stop exactly
 * once, so the app never stays in a listening state with no capture running.
 *
 * In Chrome the recognition runs on a remote service, so with no internet a capture ends in a 'network'
 * error. The sentence for each ending says plainly why it stopped and that the typed command bar still
 * works, since every command runs the same way typed.
 */

export const SPEECH_UNAVAILABLE =
  'Browser speech recognition unavailable in this environment; use the typed command bar below.';

/** The banner shown while a capture is open. */
export const SPEECH_LISTENING = 'Listening (Push-to-Talk active)… Speak a command.';

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
  onresult: ((ev: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((ev: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
}

export interface SpeechCaptureHandlers {
  /** The capture started and is listening. */
  onListening(): void;
  /** Words were heard, trimmed and not empty. */
  onTranscript(text: string): void;
  /**
   * The capture ended. `banner` says why when no words were heard; it is null after words were heard, so
   * the banner the command itself wrote stays. Called exactly once per capture.
   */
  onStopped(banner: string | null): void;
}

/**
 * Starts one capture with the browser's recognition constructor, or reports SPEECH_UNAVAILABLE when the
 * browser has none.
 */
export function startSpeechCapture(
  Recognition: (new () => SpeechRecognitionLike) | undefined,
  handlers: SpeechCaptureHandlers
): void {
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

  let rec: SpeechRecognitionLike;
  try {
    rec = new Recognition();
    rec.lang = 'en-US';
    rec.interimResults = false;
    rec.onresult = (event) => {
      if (stopped) return;
      const transcript = (event.results[0]?.[0]?.transcript ?? '').trim();
      if (!transcript) return;
      heard = true;
      handlers.onTranscript(transcript);
    };
    // After words were heard the command has already answered, so a late error or end keeps its banner.
    rec.onerror = (event) => stop(heard ? null : speechErrorBanner(event.error));
    rec.onend = () => stop(heard ? null : speechErrorBanner('no-speech'));
    rec.start();
  } catch {
    stop(STOPPED);
    return;
  }
  if (!stopped) handlers.onListening();
}
