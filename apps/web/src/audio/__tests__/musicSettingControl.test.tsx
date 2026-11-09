// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AUDIO_CREDIT_LINES, AUDIO_DISCLOSURE, AUDIO_DISCLOSURE_WITHOUT_MUSIC, HeardMusic, MusicAvailability, audioCreditLines, audioDisclosure } from '../audioCredits';
import { MusicSettingStore } from '../music/musicSetting';
import { SOUNDTRACK } from '../music/soundtrack';
import { MusicSettingControl } from '../MusicSettingControl';
import { useAudioCreditLines, useAudioDisclosure } from '../useAudioCreditLines';

declare global {
  // React's flag for tests that drive updates through act()
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function select(): HTMLSelectElement {
  const element = container.querySelector('select');
  expect(element).not.toBeNull();
  return element!;
}

describe('the Music control', () => {
  it('offers the soundtrack, each piece by its title, and off, and shows the setting', () => {
    const store = new MusicSettingStore('soundtrack', null);
    act(() => root.render(createElement(MusicSettingControl, { store })));
    expect(select().getAttribute('aria-label')).toBe('Music');
    expect(select().className).toBe('alb-music-setting');
    expect(Array.from(select().options, (option) => [option.value, option.textContent])).toEqual([
      ['soundtrack', 'Soundtrack, follows the moment'],
      ...SOUNDTRACK.map((piece) => [piece.id, piece.title]),
      ['off', 'Off'],
    ]);
    expect(select().value).toBe('soundtrack');
  });

  it("sets the music to the viewer's pick, and follows a change made elsewhere", () => {
    const store = new MusicSettingStore('soundtrack', null);
    act(() => root.render(createElement(MusicSettingControl, { store })));
    act(() => {
      select().value = 'recording';
      select().dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(store.get()).toBe('recording');
    act(() => store.set('off'));
    expect(select().value).toBe('off');
  });
});

describe('where no music can play', () => {
  it('shows no Music control, and shows it again once music can play', () => {
    const store = new MusicSettingStore('soundtrack', null);
    const available = new MusicAvailability(false);
    act(() => root.render(createElement(MusicSettingControl, { store, available })));
    expect(container.querySelector('select')).toBeNull();
    act(() => available.set(true));
    expect(select().value).toBe('soundtrack');
  });

  it('says no music plays, whatever the setting, and what plays once music can', () => {
    const setting = new MusicSettingStore('recording', null);
    const available = new MusicAvailability(false);
    function Disclosure() {
      return createElement('p', null, useAudioDisclosure(setting, available));
    }
    const text = () => container.querySelector('p')!.textContent;
    act(() => root.render(createElement(Disclosure)));
    expect(text()).toBe(AUDIO_DISCLOSURE_WITHOUT_MUSIC);
    act(() => available.set(true));
    expect(text()).toBe(audioDisclosure('recording'));
    act(() => setting.set('soundtrack'));
    expect(text()).toBe(AUDIO_DISCLOSURE);
  });
});

describe('the credits beside the canvas', () => {
  it('are the engine credits until a piece is heard, then that piece credits as well', () => {
    const heard = new HeardMusic();
    function Credits() {
      const lines = useAudioCreditLines(heard);
      return createElement('ul', null, lines.map((line) => createElement('li', { key: line }, line)));
    }
    const shown = () => Array.from(container.querySelectorAll('li'), (li) => li.textContent);
    act(() => root.render(createElement(Credits)));
    expect(shown()).toEqual([...AUDIO_CREDIT_LINES]);
    act(() => heard.add('recording'));
    expect(shown()).toEqual([...audioCreditLines(['recording'])]);
    act(() => heard.add('ambient-beats'));
    expect(shown()).toEqual([...audioCreditLines(['recording', 'ambient-beats'])]);
  });
});
