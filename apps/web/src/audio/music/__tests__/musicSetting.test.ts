// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

import fc from 'fast-check';
import { describe, expect, it, vi } from 'vitest';
import { MUSIC_SETTING_KEY, MusicSettingStore, initialMusicSetting, parseMusicSetting, readStoredSetting, type MusicSetting, type SettingStorage } from '../musicSetting';
import { SOUNDTRACK } from '../soundtrack';

const SETTINGS: MusicSetting[] = ['soundtrack', 'off', ...SOUNDTRACK.map((piece) => piece.id)];

function memoryStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  const storage: SettingStorage = {
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => void items.set(key, value),
  };
  return { storage, items };
}

const refusingStorage: SettingStorage = {
  getItem: () => {
    throw new DOMException('denied', 'SecurityError');
  },
  setItem: () => {
    throw new DOMException('full', 'QuotaExceededError');
  },
};

describe('the Music setting', () => {
  it('is the soundtrack, off, or one of the pieces, and nothing else', () => {
    for (const setting of SETTINGS) expect(parseMusicSetting(setting)).toBe(setting);
    fc.assert(
      fc.property(fc.string(), (value) => {
        const parsed = parseMusicSetting(value);
        expect(parsed === null || SETTINGS.includes(parsed)).toBe(true);
        if (parsed !== null) expect(parsed).toBe(value);
      }),
      { seed: SEED, examples: [['organ'], ['Soundtrack'], [''], ['recording ']] }
    );
    expect(parseMusicSetting(null)).toBeNull();
  });

  it('starts as the stored setting, or the soundtrack when none is stored or it is unknown', () => {
    expect(initialMusicSetting({ dev: false, search: '', stored: null })).toBe('soundtrack');
    expect(initialMusicSetting({ dev: false, search: '', stored: 'off' })).toBe('off');
    expect(initialMusicSetting({ dev: false, search: '', stored: 'ambient-beats' })).toBe('ambient-beats');
    expect(initialMusicSetting({ dev: false, search: '', stored: 'organ' })).toBe('soundtrack');
  });

  it('lets development set it with ?music= for that load, and production ignores the address', () => {
    fc.assert(
      fc.property(fc.constantFrom(...SETTINGS), fc.constantFrom<string | null>(null, ...SETTINGS), (addressed, stored) => {
        expect(initialMusicSetting({ dev: true, search: `?music=${addressed}`, stored })).toBe(addressed);
        expect(initialMusicSetting({ dev: false, search: `?music=${addressed}`, stored })).toBe(parseMusicSetting(stored) ?? 'soundtrack');
      }),
      { seed: SEED, examples: [['off', 'recording'], ['recording', null]] }
    );
    expect(initialMusicSetting({ dev: true, search: '?renderer=webgpu&music=recording', stored: 'off' })).toBe('recording');
    // An unknown value falls back rather than guessing a piece
    expect(initialMusicSetting({ dev: true, search: '?music=organ', stored: 'off' })).toBe('off');
  });

  it('stores what the viewer picks and tells its listeners, and ignores what is not a setting', () => {
    const { storage, items } = memoryStorage();
    const store = new MusicSettingStore('soundtrack', storage);
    const heard = vi.fn();
    const unsubscribe = store.subscribe(heard);
    store.set('recording');
    expect(store.get()).toBe('recording');
    expect(items.get(MUSIC_SETTING_KEY)).toBe('recording');
    expect(heard).toHaveBeenCalledTimes(1);
    store.set('recording');
    store.set('organ' as MusicSetting);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(store.get()).toBe('recording');
    unsubscribe();
    store.set('off');
    expect(heard).toHaveBeenCalledTimes(1);
    expect(items.get(MUSIC_SETTING_KEY)).toBe('off');
    expect(readStoredSetting(storage)).toBe('off');
  });

  it('works for the visit when storage refuses, and reads nothing from it', () => {
    expect(readStoredSetting(refusingStorage)).toBeNull();
    expect(readStoredSetting(null)).toBeNull();
    const store = new MusicSettingStore('soundtrack', refusingStorage);
    store.set('off');
    expect(store.get()).toBe('off');
    const unstored = new MusicSettingStore('soundtrack', null);
    unstored.set('ambient-beats');
    expect(unstored.get()).toBe('ambient-beats');
  });
});
