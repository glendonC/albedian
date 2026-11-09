/**
 * Keeps the engine's AudioContext alive across what phones do to a page (AUDIO_ENGINE.md section
 * 14): the tab goes to the background, the phone locks, a call interrupts, the page enters the
 * back-forward cache.
 *
 * - While the page is hidden the context is suspended, so a hidden tab plays nothing and costs no
 *   CPU; when it is shown again and audio is on, the context resumes.
 * - iOS Safari moves a context to 'interrupted' when the phone locks or a call comes in, and a
 *   browser may refuse resume() outside a user gesture. Then the next tap, click or key resumes it.
 * - Audio the user turned off is never resumed.
 * - Where the browser has `navigator.audioSession` (the W3C Audio Session API), the session type is
 *   'ambient', which the spec defines as mixable with other audio. Whether iOS then silences the app
 *   with the ring/silent switch is not stated by the spec or WebKit's notes, so it is on the
 *   real-phone checklist.
 *
 * The engine attaches one lifecycle when it makes its context, inside the user's gesture.
 */

type Listener = (event?: { readonly target?: unknown }) => void;

/**
 * Marks the Audio control on the page. It resumes or stops the audio itself, so a tap on it is not
 * taken as a gesture to resume: turning Audio off while a tap is awaited then plays no tail.
 */
export const AUDIO_CONTROL_SELECTOR = '[data-audio-control="toggle"]';

/** Whether an event's target sits inside the Audio control. */
function onAudioControl(event?: { readonly target?: unknown }): boolean {
  const target = event?.target as { closest?: (selector: string) => unknown } | null | undefined;
  return typeof target?.closest === 'function' && target.closest(AUDIO_CONTROL_SELECTOR) != null;
}

export interface LifecycleEvents {
  addEventListener(type: string, listener: Listener, options?: boolean | AddEventListenerOptions): void;
  removeEventListener(type: string, listener: Listener, options?: boolean | EventListenerOptions): void;
}

/** The context calls the lifecycle makes; an AudioContext provides them. */
export interface LifecycleContext extends LifecycleEvents {
  /** iOS Safari reports 'interrupted' beside the standard states. */
  readonly state: AudioContextState | 'interrupted';
  resume(): Promise<void>;
  suspend(): Promise<void>;
}

/** The document (visibility, gestures) and window (page show and hide) the lifecycle listens to. */
export interface LifecycleHost extends LifecycleEvents {
  readonly visibilityState: DocumentVisibilityState;
  readonly window: LifecycleEvents;
  readonly audioSession?: { type: string } | undefined;
}

export interface LifecycleStatus {
  readonly state: AudioContextState | 'interrupted';
  /** The lifecycle suspended the context because the page was hidden. */
  readonly suspendedWhileHidden: boolean;
  /** The browser refused to resume: the next tap, click or key resumes. */
  readonly waitingForGesture: boolean;
}

/** Gestures that let a browser resume audio. */
const GESTURES = ['pointerdown', 'keydown', 'touchend'] as const;

/** The page's document and window, or null outside a browser. */
export function browserLifecycleHost(): LifecycleHost | null {
  if (typeof document === 'undefined' || typeof window === 'undefined') return null;
  const nav = navigator as Navigator & { readonly audioSession?: { type: string } };
  return {
    get visibilityState() {
      return document.visibilityState;
    },
    addEventListener: (type, listener, options) => document.addEventListener(type, listener, options),
    removeEventListener: (type, listener, options) => document.removeEventListener(type, listener, options),
    window: {
      addEventListener: (type, listener, options) => window.addEventListener(type, listener, options),
      removeEventListener: (type, listener, options) => window.removeEventListener(type, listener, options),
    },
    get audioSession() {
      return nav.audioSession;
    },
  };
}

export class AudioLifecycle {
  private attached = false;
  private suspendedWhileHidden = false;
  private waitingForGesture = false;

  constructor(
    private readonly ctx: LifecycleContext,
    private readonly host: LifecycleHost,
    /** Whether the user has audio on; the lifecycle never resumes audio that is off. */
    private readonly isEnabled: () => boolean
  ) {}

  attach(): void {
    if (this.attached) return;
    this.attached = true;
    try {
      if (this.host.audioSession) this.host.audioSession.type = 'ambient';
    } catch {
      // A browser that refuses the type keeps its own
    }
    this.host.addEventListener('visibilitychange', this.onVisibility);
    this.host.window.addEventListener('pagehide', this.onHide);
    this.host.window.addEventListener('pageshow', this.onShow);
    this.ctx.addEventListener('statechange', this.onState);
  }

  detach(): void {
    if (!this.attached) return;
    this.attached = false;
    this.host.removeEventListener('visibilitychange', this.onVisibility);
    this.host.window.removeEventListener('pagehide', this.onHide);
    this.host.window.removeEventListener('pageshow', this.onShow);
    this.ctx.removeEventListener('statechange', this.onState);
    this.stopWaiting();
  }

  getStatus(): LifecycleStatus {
    return { state: this.ctx.state, suspendedWhileHidden: this.suspendedWhileHidden, waitingForGesture: this.waitingForGesture };
  }

  private readonly onVisibility = (): void => {
    if (this.host.visibilityState === 'hidden') this.onHide();
    else this.onShow();
  };

  private readonly onHide = (): void => {
    if (this.ctx.state !== 'running') return;
    this.suspendedWhileHidden = true;
    void this.ctx.suspend().catch(() => undefined);
  };

  private readonly onShow = (): void => {
    if (this.host.visibilityState === 'hidden') return;
    this.suspendedWhileHidden = false;
    if (!this.isEnabled() || this.ctx.state === 'running') return;
    void this.tryResume();
  };

  /** A context stopped by the browser while the page is visible (iOS 'interrupted', or suspended). */
  private readonly onState = (): void => {
    if (this.ctx.state === 'running') {
      this.stopWaiting();
      return;
    }
    if (this.ctx.state === 'closed' || this.suspendedWhileHidden || this.host.visibilityState === 'hidden') return;
    if (!this.isEnabled()) return;
    void this.tryResume();
  };

  private async tryResume(): Promise<void> {
    await this.ctx.resume().catch(() => undefined);
    if (this.ctx.state !== 'running' && this.isEnabled()) this.waitForGesture();
  }

  private readonly onGesture = (event?: { readonly target?: unknown }): void => {
    if (!this.isEnabled()) {
      this.stopWaiting();
      return;
    }
    // The Audio control resumes or stops the audio itself: a tap there must not resume first
    if (onAudioControl(event)) return;
    void this.ctx.resume().catch(() => undefined);
  };

  private waitForGesture(): void {
    if (this.waitingForGesture || !this.attached) return;
    this.waitingForGesture = true;
    for (const type of GESTURES) this.host.addEventListener(type, this.onGesture, true);
  }

  private stopWaiting(): void {
    if (!this.waitingForGesture) return;
    this.waitingForGesture = false;
    for (const type of GESTURES) this.host.removeEventListener(type, this.onGesture, true);
  }
}
