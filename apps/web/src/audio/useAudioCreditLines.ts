import { useMemo, useSyncExternalStore } from 'react';
import { appHeardMusic, appMusicAvailability, audioCreditLines, audioDisclosure, type HeardMusic, type MusicAvailability } from './audioCredits.js';
import { appMusicSetting, type MusicSettingStore } from './music/musicSetting.js';

/**
 * The credit lines beside the canvas: the engine's works, then those of each soundtrack piece heard
 * this visit, updated as pieces start (audioCredits.ts).
 */
export function useAudioCreditLines(heard: HeardMusic = appHeardMusic): readonly string[] {
  const pieces = useSyncExternalStore(heard.subscribe, heard.get, heard.get);
  return useMemo(() => audioCreditLines(pieces), [pieces]);
}

/** What the sound is beside the Audio control, for the page's Music setting and whether music can play (audioCredits.ts). */
export function useAudioDisclosure(setting: MusicSettingStore = appMusicSetting, available: MusicAvailability = appMusicAvailability): string {
  const value = useSyncExternalStore(setting.subscribe, setting.get, setting.get);
  const availability = useSyncExternalStore(available.subscribe, available.get, available.get);
  return audioDisclosure(value, availability);
}
