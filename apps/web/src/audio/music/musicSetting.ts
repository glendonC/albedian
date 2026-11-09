/**
 * The Music setting (AUDIO_ENGINE.md 13.7): 'soundtrack' (the default) lets the soundtrack follow
 * the moment, a piece id pins that piece, and 'off' plays no music. Each viewer's choice is kept in
 * their own browser's localStorage; where storage is unavailable the setting still works for the
 * visit and starts as the soundtrack next time.
 *
 * In development, `?music=soundtrack|organ-pulse|ambient-beats|recording|off` sets it for that
 * load without storing it, so pieces can be compared in the real app. A production build ignores
 * the address.
 */

import { SOUNDTRACK, type PieceId } from './soundtrack';

export type MusicSetting = 'soundtrack' | 'off' | PieceId;

/** The localStorage key of the setting. */
export const MUSIC_SETTING_KEY = 'albedian.music';

/** The setting a stored or addressed value names; null for anything else. */
export function parseMusicSetting(value: string | null | undefined): MusicSetting | null {
  if (value === 'soundtrack' || value === 'off') return value;
  return SOUNDTRACK.find((piece) => piece.id === value)?.id ?? null;
}

/** The setting a page starts with: in development the address's, else the stored one, else the soundtrack. */
export function initialMusicSetting(input: { readonly dev: boolean; readonly search: string; readonly stored: string | null }): MusicSetting {
  const addressed = input.dev ? parseMusicSetting(new URLSearchParams(input.search).get('music')) : null;
  return addressed ?? parseMusicSetting(input.stored) ?? 'soundtrack';
}

/** The part of Storage the setting uses. */
export interface SettingStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** This browser's localStorage, or null where the page may not use it. */
export function browserStorage(): SettingStorage | null {
  try {
    return typeof window === 'undefined' ? null : (window.localStorage ?? null);
  } catch {
    return null;
  }
}

/** Reads the stored setting; null when there is none or storage refuses. */
export function readStoredSetting(storage: SettingStorage | null): string | null {
  try {
    return storage?.getItem(MUSIC_SETTING_KEY) ?? null;
  } catch {
    return null;
  }
}

/** The setting for one page: what the control shows and the engine plays, stored when the viewer changes it. */
export class MusicSettingStore {
  private setting: MusicSetting;
  private readonly listeners = new Set<() => void>();

  constructor(
    initial: MusicSetting,
    private readonly storage: SettingStorage | null
  ) {
    this.setting = initial;
  }

  readonly get = (): MusicSetting => this.setting;

  readonly set = (setting: MusicSetting): void => {
    if (parseMusicSetting(setting) === null || setting === this.setting) return;
    this.setting = setting;
    try {
      this.storage?.setItem(MUSIC_SETTING_KEY, setting);
    } catch {
      // Storage full or refused: the setting holds for this visit only
    }
    for (const listener of [...this.listeners]) listener();
  };

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

// Vite replaces import.meta.env at build time; the app's tsconfig carries no vite/client types
const env = (import.meta as ImportMeta & { readonly env?: { readonly DEV?: boolean } }).env;

/** True in a development build (the Vite dev server), where `?music=` sets the music. */
export const DEV_BUILD = env?.DEV === true;

/** This page's setting, shared by the Music control and the engine. */
export const appMusicSetting = new MusicSettingStore(
  initialMusicSetting({
    dev: DEV_BUILD,
    search: typeof window === 'undefined' ? '' : window.location.search,
    stored: readStoredSetting(browserStorage()),
  }),
  browserStorage()
);
