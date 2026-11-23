import React, { useEffect, useRef, useState } from 'react';
import { IconButton } from '../look/index.js';

/** The time in the clip as minutes and seconds ("0:05"), with no total. */
export function clipTime(sec: number): string {
  const whole = Math.max(0, Math.floor(sec));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** The pill's padding: its line runs 18 px inside it at each end. */
const LINE_INSET_PX = 18;

/** The time a place on the line stands for, never before the clip's start nor past its end. */
export function timeAtX(clientX: number, pill: { left: number; width: number }, durationSec: number): number {
  const span = Math.max(1, pill.width - 2 * LINE_INSET_PX);
  const fraction = Math.min(1, Math.max(0, (clientX - pill.left - LINE_INSET_PX) / span));
  return fraction * durationSec;
}

/** One press of an arrow key, in seconds. */
const STEP_SEC = 1;

/**
 * A clip's player on the reveal (INTERIM: .lead/uiux/UIUX-clip-QUESTION.md, waiting on the owner; Vimeo's player): a
 * play circle apart from one thin timeline, the time riding above it in a small chip. A press on the line goes there,
 * holding and moving drags through the clip, the arrows step one second. Where it stops, the session's clock is set
 * through the reducer (onSeek), so what the reveal draws follows the frame on screen.
 */
export const ClipPlayer: React.FC<{ video: HTMLVideoElement | null; durationSec: number; onSeek: (sec: number) => void; phone?: boolean }> = ({
  video,
  durationSec,
  onSeek,
  phone = false,
}) => {
  const [time, setTime] = useState(video?.currentTime ?? 0);
  const [playing, setPlaying] = useState(video ? !video.paused : false);
  const held = useRef(false);
  const seek = useRef(onSeek);
  seek.current = onSeek;

  useEffect(() => {
    if (!video) return undefined;
    const onTime = () => setTime(video.currentTime);
    const onPlay = () => setPlaying(true);
    // Every stop sets the session clock to the frame shown, the clip's own end included, so nothing lined up for
    // another frame stands over it (P-SKY-9; the re-read of unit-clip-1)
    const onPause = () => {
      setPlaying(false);
      seek.current(video.currentTime);
    };
    video.addEventListener('timeupdate', onTime);
    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    setTime(video.currentTime);
    setPlaying(!video.paused);
    return () => {
      video.removeEventListener('timeupdate', onTime);
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
    };
  }, [video]);

  const go = (sec: number, settle: boolean) => {
    if (!video) return;
    const at = Math.min(durationSec, Math.max(0, sec));
    video.currentTime = at;
    setTime(at);
    if (settle) onSeek(at);
  };
  const toggle = () => {
    if (!video) return;
    if (video.paused) {
      void video.play();
    } else {
      video.pause();
    }
  };
  const fraction = durationSec > 0 ? Math.min(1, Math.max(0, time / durationSec)) : 0;
  const place = `${fraction * 100}%`;

  return (
    <div className={phone ? 'clip-player is-phone' : 'clip-player'} data-hud-interactive="true" data-testid="clip-player">
      <IconButton label={playing ? 'Pause' : 'Play'} icon={playing ? 'pause' : 'play'} size={phone ? 'phone' : undefined} onClick={toggle} />
      <div
        className="clip-player__line"
        role="slider"
        aria-label="Time in the clip"
        aria-valuemin={0}
        aria-valuemax={durationSec}
        aria-valuenow={Math.round(time * 10) / 10}
        aria-valuetext={clipTime(time)}
        tabIndex={0}
        onPointerDown={(event) => {
          held.current = true;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          go(timeAtX(event.clientX, event.currentTarget.getBoundingClientRect(), durationSec), false);
        }}
        onPointerMove={(event) => {
          if (held.current) go(timeAtX(event.clientX, event.currentTarget.getBoundingClientRect(), durationSec), false);
        }}
        onPointerUp={(event) => {
          if (!held.current) return;
          held.current = false;
          go(timeAtX(event.clientX, event.currentTarget.getBoundingClientRect(), durationSec), true);
        }}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          event.preventDefault();
          go(time + (event.key === 'ArrowRight' ? STEP_SEC : -STEP_SEC), true);
        }}
      >
        <span className="clip-player__track">
          <i className="clip-player__fill" style={{ width: place }} />
        </span>
        <b className="clip-player__time" style={{ left: place }}>
          {clipTime(time)}
        </b>
      </div>
    </div>
  );
};
