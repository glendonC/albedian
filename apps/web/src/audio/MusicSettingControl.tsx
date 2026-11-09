import React, { useSyncExternalStore } from 'react';
import { appMusicAvailability, type MusicAvailability } from './audioCredits.js';
import { appMusicSetting, parseMusicSetting, type MusicSettingStore } from './music/musicSetting.js';
import { SOUNDTRACK } from './music/soundtrack.js';

/**
 * The Music setting beside the Audio control (AUDIO_ENGINE.md 13.7): the soundtrack, which follows
 * the moment of the visit, one piece pinned, or off. It changes the page's setting, which the
 * engine follows and the viewer's browser keeps. It carries one class name and no look of its own.
 * Where no music can play (no samples served, or a file missing) there is nothing to choose, so it
 * is not shown.
 */
export const MusicSettingControl: React.FC<{ readonly store?: MusicSettingStore; readonly available?: MusicAvailability }> = ({
  store = appMusicSetting,
  available = appMusicAvailability,
}) => {
  const setting = useSyncExternalStore(store.subscribe, store.get, store.get);
  const canPlay = useSyncExternalStore(available.subscribe, available.get, available.get) === 'available';
  if (!canPlay) return null;
  return (
    <select
      className="alb-music-setting"
      aria-label="Music"
      data-hud-interactive="true"
      value={setting}
      onChange={(event) => {
        const next = parseMusicSetting(event.target.value);
        if (next) store.set(next);
      }}
    >
      <option value="soundtrack">Soundtrack, follows the moment</option>
      {SOUNDTRACK.map((piece) => (
        <option key={piece.id} value={piece.id}>
          {piece.title}
        </option>
      ))}
      <option value="off">Off</option>
    </select>
  );
};
