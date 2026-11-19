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
  onaudiostart: (() => void) | null = null;
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
  /** The browser opened the microphone (after any permission prompt). */
  open() {
    this.onaudiostart?.();
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
  const calls = { asking: 0, listening: 0, transcripts: [] as string[], stopped: [] as Array<string | null> };
  startSpeechCapture(Ctor ?? undefined, {
    onAsking: () => calls.asking++,
    onListening: () => calls.listening++,
    onTranscript: (text) => calls.transcripts.push(text),
    onStopped: (banner) => calls.stopped.push(banner),
  });
  return { calls, rec: FakeRecognition.last! };
}

describe('@albedian/web push-to-talk speech capture', () => {
  it('says plainly when the browser has no speech recognition', () => {
    const { calls } = capture(null);
    expect(calls).toEqual({ asking: 0, listening: 0, transcripts: [], stopped: [SPEECH_UNAVAILABLE] });
  });

  it('listens, hands over the words, and stops without replacing the banner the command wrote', () => {
    const { calls, rec } = capture();
    expect(rec.started).toBe(true);
    expect(rec.lang).toBe('en-US');
    expect(rec.interimResults).toBe(false);
    expect([calls.asking, calls.listening]).toEqual([1, 0]);
    rec.open();
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
    expect([calls.asking, calls.listening]).toEqual([0, 0]);
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
    expect([calls.asking, calls.listening]).toEqual([0, 0]);
    expect(calls.stopped).toEqual([speechErrorBanner('not-allowed')]);
  });

  it('never says listening before the microphone opens, even when the capture ends unopened', () => {
    const { calls, rec } = capture();
    expect([calls.asking, calls.listening]).toEqual([1, 0]);
    rec.onerror?.({ error: 'not-allowed' });
    rec.onend?.();
    rec.open();
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

  it('hands over the words as they are heard when asked to, the last unsettled until the recogniser settles it (ask, approved 2026-11-12)', () => {
    const words: Array<[string, boolean]> = [];
    const transcripts: string[] = [];
    startSpeechCapture(FakeRecognition, {
      onAsking: () => {},
      onListening: () => {},
      onTranscript: (text) => transcripts.push(text),
      onStopped: () => {},
      onWords: (text, settled) => words.push([text, settled]),
    });
    const rec = FakeRecognition.last!;
    expect(rec.interimResults).toBe(true);
    const result = (transcript: string, isFinal: boolean) => Object.assign([{ transcript }], { isFinal });
    rec.onresult?.({ results: [result('How far', false)] } as never);
    rec.onresult?.({ results: [result('How far away is', false)] } as never);
    expect(transcripts).toEqual([]);
    // The browser splits a capture into results; the words are all of them, in order
    rec.onresult?.({ results: [result('How far away', true), result(' is Vega', false)] } as never);
    expect(words.at(-1)).toEqual(['How far away is Vega', false]);
    expect(transcripts).toEqual([]);
    rec.onresult?.({ results: [result('How far away', true), result(' is Vega', true)] } as never);
    expect(words).toEqual([
      ['How far', false],
      ['How far away is', false],
      ['How far away is Vega', false],
      ['How far away is Vega', true],
    ]);
    expect(transcripts).toEqual(['How far away is Vega']);
    // Nothing after the words were handed over
    rec.onresult?.({ results: [result('How far away is Vega now', true)] } as never);
    expect(transcripts).toEqual(['How far away is Vega']);
  });

  it('writes no em or en dash in any sentence', () => {
    for (const code of ['network', 'not-allowed', 'no-speech', 'audio-capture', 'aborted', 'language-not-supported', undefined]) {
      expect(speechErrorBanner(code)).not.toMatch(/[\u2013\u2014]/);
    }
    expect(SPEECH_UNAVAILABLE).not.toMatch(/[\u2013\u2014]/);
    vi.restoreAllMocks();
  });
});

// On-device recognition (FIELD field-3, the lead's answer 23:50): where the browser can say whether English runs on the
// device, a capture asks it to; a language pack it can download is asked for on the press; nothing new is shown
describe('on-device recognition where the browser offers it', () => {
  type Availability = 'available' | 'downloadable' | 'downloading' | 'unavailable';
  function deviceRecognition(availability: Availability, installs = true) {
    const asked: Array<{ langs: string[]; processLocally?: boolean }> = [];
    const installed: Array<{ langs: string[]; processLocally?: boolean }> = [];
    class DeviceRecognition extends FakeRecognition {
      processLocally = false;
      static available(options: { langs: string[]; processLocally?: boolean }) {
        asked.push(options);
        return Promise.resolve(availability);
      }
      static install(options: { langs: string[]; processLocally?: boolean }) {
        installed.push(options);
        return Promise.resolve(installs);
      }
    }
    return { Ctor: DeviceRecognition, asked, installed };
  }
  const settle = () => new Promise((r) => setTimeout(r, 0));
  const last = () => FakeRecognition.last as FakeRecognition & { processLocally?: boolean };

  it('runs on the device when English is there, says it is asking at once and starts once the browser has answered', async () => {
    const { Ctor, asked, installed } = deviceRecognition('available');
    FakeRecognition.last = undefined;
    const { calls } = capture(Ctor);
    expect(calls.asking).toBe(1);
    await settle();
    expect(asked).toEqual([{ langs: ['en-US'], processLocally: true }]);
    expect(installed).toEqual([]);
    expect(last().started).toBe(true);
    expect(last().processLocally).toBe(true);
    expect(last().lang).toBe('en-US');
  });

  it('asks for the language pack on the press when it can be downloaded, and uses the browser\'s service this time', async () => {
    const { Ctor, installed } = deviceRecognition('downloadable');
    capture(Ctor);
    await settle();
    expect(installed).toEqual([{ langs: ['en-US'], processLocally: true }]);
    expect(last().started).toBe(true);
    expect(last().processLocally).toBe(false);
  });

  it('uses the browser\'s service while the pack downloads or where the device cannot, and asks for no download', async () => {
    for (const availability of ['downloading', 'unavailable'] as const) {
      const { Ctor, installed } = deviceRecognition(availability);
      capture(Ctor);
      await settle();
      expect(installed).toEqual([]);
      expect(last().started).toBe(true);
      expect(last().processLocally).toBe(false);
    }
  });

  it('still starts when the browser\'s answer fails, and reports the stop once if start refuses after it', async () => {
    class Failing extends FakeRecognition {
      static available() {
        return Promise.reject(new DOMException('blocked', 'InvalidStateError'));
      }
    }
    capture(Failing);
    await settle();
    expect(last().started).toBe(true);
    class FailingRefusing extends RefusingRecognition {
      static available() {
        return Promise.resolve('available');
      }
    }
    const { calls } = capture(FailingRefusing);
    await settle();
    expect(calls.stopped).toEqual(['Speech recognition stopped. Type the command below.']);
  });

  it('starts at once, as before, in a browser that cannot say', () => {
    FakeRecognition.last = undefined;
    capture(FakeRecognition);
    expect(last().started).toBe(true);
  });
});
