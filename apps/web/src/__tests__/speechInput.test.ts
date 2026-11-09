import { describe, expect, it, vi } from 'vitest';
import {
  SPEECH_UNAVAILABLE,
  speechErrorBanner,
  startSpeechCapture,
  type SpeechRecognitionLike,
} from '../voice/speechInput';

/**
 * Push-to-talk through the browser's speech recognition. In Chrome recognition runs on a remote service,
 * so with no internet it ends in a 'network' error. Every way a capture ends returns the voice machine to
 * off exactly once, with a plain sentence saying why when no words were heard.
 */

type Event = { results: ArrayLike<ArrayLike<{ transcript: string }>> };

class FakeRecognition implements SpeechRecognitionLike {
  static last: FakeRecognition | undefined;
  lang = '';
  interimResults = true;
  onresult: ((ev: Event) => void) | null = null;
  onerror: ((ev: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  started = false;
  constructor() {
    FakeRecognition.last = this;
  }
  start() {
    this.started = true;
  }
  hear(transcript: string) {
    this.onresult?.({ results: [[{ transcript }]] });
  }
}

class RefusingRecognition extends FakeRecognition {
  override start(): void {
    throw new DOMException('recognition has already started', 'InvalidStateError');
  }
}

/** Ends inside start(), before the caller could mark the capture as listening. */
class EndsAtOnceRecognition extends FakeRecognition {
  override start(): void {
    this.onerror?.({ error: 'not-allowed' });
    this.onend?.();
  }
}

/** `null` stands for a browser with no recognition (an explicit undefined would take the default). */
function capture(Ctor: (new () => SpeechRecognitionLike) | null = FakeRecognition) {
  const calls = { listening: 0, transcripts: [] as string[], stopped: [] as Array<string | null> };
  startSpeechCapture(Ctor ?? undefined, {
    onListening: () => calls.listening++,
    onTranscript: (text) => calls.transcripts.push(text),
    onStopped: (banner) => calls.stopped.push(banner),
  });
  return { calls, rec: FakeRecognition.last! };
}

describe('@albedian/web push-to-talk speech capture', () => {
  it('says plainly when the browser has no speech recognition', () => {
    const { calls } = capture(null);
    expect(calls).toEqual({ listening: 0, transcripts: [], stopped: [SPEECH_UNAVAILABLE] });
  });

  it('listens, hands over the words, and stops without replacing the banner the command wrote', () => {
    const { calls, rec } = capture();
    expect(rec.started).toBe(true);
    expect(rec.lang).toBe('en-US');
    expect(rec.interimResults).toBe(false);
    expect(calls.listening).toBe(1);
    rec.hear('take me to Canopus');
    rec.onend?.();
    expect(calls.transcripts).toEqual(['take me to Canopus']);
    expect(calls.stopped).toEqual([null]);
  });

  it('says speech needs the internet when recognition fails on the network, and stops once', () => {
    const { calls, rec } = capture();
    rec.onerror?.({ error: 'network' });
    rec.onend?.();
    expect(calls.stopped).toEqual([speechErrorBanner('network')]);
    expect(calls.stopped[0]).toMatch(/internet/i);
    expect(calls.stopped[0]).toMatch(/type the command/i);
  });

  it.each([
    ['not-allowed', /microphone is not allowed/i],
    ['service-not-allowed', /microphone is not allowed/i],
    ['no-speech', /no speech was heard/i],
    ['audio-capture', /no microphone/i],
    ['language-not-supported', /cannot recognise speech/i],
    ['something-new', /speech recognition stopped/i],
    ['toString', /speech recognition stopped/i],
  ])('says why for a %s error', (code, words) => {
    const { calls, rec } = capture();
    rec.onerror?.({ error: code });
    rec.onend?.();
    expect(calls.stopped).toHaveLength(1);
    expect(calls.stopped[0]).toMatch(words);
  });

  it('never leaves the app listening when a session ends with no words and no error', () => {
    const { calls, rec } = capture();
    rec.onend?.();
    expect(calls.transcripts).toEqual([]);
    expect(calls.stopped).toEqual([speechErrorBanner('no-speech')]);
  });

  it('stops at once when recognition refuses to start', () => {
    const { calls } = capture(RefusingRecognition);
    expect(calls.listening).toBe(0);
    expect(calls.stopped).toHaveLength(1);
    expect(calls.stopped[0]).toMatch(/speech recognition stopped/i);
  });

  it("keeps the command's own banner when an error follows the words", () => {
    const { calls, rec } = capture();
    rec.hear('what is that');
    rec.onerror?.({ error: 'network' });
    rec.onend?.();
    expect(calls.transcripts).toEqual(['what is that']);
    expect(calls.stopped).toEqual([null]);
  });

  it('runs no command from words that arrive after the capture stopped', () => {
    const { calls, rec } = capture();
    rec.onerror?.({ error: 'aborted' });
    rec.hear('take me to Canopus');
    rec.onend?.();
    expect(calls.transcripts).toEqual([]);
    expect(calls.stopped).toEqual([speechErrorBanner('aborted')]);
  });

  it('never marks the app listening after a capture that ended inside start', () => {
    const { calls } = capture(EndsAtOnceRecognition);
    expect(calls.listening).toBe(0);
    expect(calls.stopped).toEqual([speechErrorBanner('not-allowed')]);
  });

  it('ignores an empty transcript', () => {
    const { calls, rec } = capture();
    rec.hear('   ');
    rec.onend?.();
    expect(calls.transcripts).toEqual([]);
    expect(calls.stopped).toEqual([speechErrorBanner('no-speech')]);
  });

  it('writes no em or en dash in any sentence', () => {
    for (const code of ['network', 'not-allowed', 'no-speech', 'audio-capture', 'aborted', 'language-not-supported', undefined]) {
      expect(speechErrorBanner(code)).not.toMatch(/[–—]/);
    }
    expect(SPEECH_UNAVAILABLE).not.toMatch(/[–—]/);
    vi.restoreAllMocks();
  });
});
