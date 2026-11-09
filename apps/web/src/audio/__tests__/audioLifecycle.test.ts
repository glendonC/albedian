import { afterEach, describe, expect, it, vi } from 'vitest';
import { AudioLifecycle, type LifecycleContext, type LifecycleHost } from '../engine/audioLifecycle';
import { ObservatoryAudioEngine } from '../ObservatoryAudioEngine';

type Listener = (event?: unknown) => void;

const captureOf = (options?: boolean | { capture?: boolean }) => (typeof options === 'boolean' ? options : (options?.capture ?? false));

/**
 * Event targets with the calls the lifecycle makes, and a way to fire them. As in the DOM, a
 * listener is kept per type and capture flag: removing it with another capture flag leaves it.
 */
class Target {
  readonly listeners = new Map<string, Set<Listener>>();
  addEventListener(type: string, fn: Listener, options?: boolean | { capture?: boolean }) {
    const key = `${type}|${captureOf(options)}`;
    if (!this.listeners.has(key)) this.listeners.set(key, new Set());
    this.listeners.get(key)!.add(fn);
  }
  removeEventListener(type: string, fn: Listener, options?: boolean | { capture?: boolean }) {
    this.listeners.get(`${type}|${captureOf(options)}`)?.delete(fn);
  }
  fire(type: string) {
    for (const capture of [true, false]) for (const fn of [...(this.listeners.get(`${type}|${capture}`) ?? [])]) fn();
  }
  count(type: string) {
    return (this.listeners.get(`${type}|true`)?.size ?? 0) + (this.listeners.get(`${type}|false`)?.size ?? 0);
  }
  captured(type: string) {
    return this.listeners.get(`${type}|true`)?.size ?? 0;
  }
}

/**
 * An AudioContext as phones run one: iOS Safari moves a running context to 'interrupted' when the
 * phone locks or a call comes in, and both browsers may refuse resume() outside a user gesture.
 */
class PhoneContext extends Target implements LifecycleContext {
  state: AudioContextState | 'interrupted' = 'running';
  /** While true, resume() outside a gesture leaves the state as it is, as a strict browser does. */
  needsGesture = false;
  inGesture = false;
  resumes = 0;
  suspends = 0;
  resume() {
    this.resumes += 1;
    if (!this.needsGesture || this.inGesture) this.setState('running');
    return Promise.resolve();
  }
  suspend() {
    this.suspends += 1;
    this.setState('suspended');
    return Promise.resolve();
  }
  close() {
    this.setState('closed');
    return Promise.resolve();
  }
  setState(state: AudioContextState | 'interrupted') {
    if (state === this.state) return;
    this.state = state;
    this.fire('statechange');
  }
}

class Host extends Target implements LifecycleHost {
  visibilityState: DocumentVisibilityState = 'visible';
  readonly window = new Target();
  audioSession: { type: string } | undefined = { type: 'auto' };
  hide() {
    this.visibilityState = 'hidden';
    this.fire('visibilitychange');
  }
  show() {
    this.visibilityState = 'visible';
    this.fire('visibilitychange');
  }
  gesture(ctx: PhoneContext, type = 'touchend') {
    ctx.inGesture = true;
    this.fire(type);
    ctx.inGesture = false;
  }
}

const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};

function setup(options: { enabled?: boolean; needsGesture?: boolean } = {}) {
  const ctx = new PhoneContext();
  ctx.needsGesture = options.needsGesture ?? false;
  const host = new Host();
  let enabled = options.enabled ?? true;
  const lifecycle = new AudioLifecycle(ctx, host, () => enabled);
  lifecycle.attach();
  return { ctx, host, lifecycle, setEnabled: (value: boolean) => (enabled = value) };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AudioLifecycle on phones', () => {
  it("asks for the 'ambient' audio session, which mixes with other audio, where the browser has one", () => {
    const { host } = setup();
    expect(host.audioSession).toEqual({ type: 'ambient' });
    // A browser without navigator.audioSession is left as it is
    const bare = new Host();
    bare.audioSession = undefined;
    new AudioLifecycle(new PhoneContext(), bare, () => true).attach();
    expect(bare.audioSession).toBeUndefined();
  });

  it('suspends while the page is hidden and resumes when it is shown again', async () => {
    const { ctx, host, lifecycle } = setup();
    host.hide();
    await flush();
    expect(ctx.state).toBe('suspended');
    expect(lifecycle.getStatus()).toMatchObject({ suspendedWhileHidden: true, waitingForGesture: false });
    host.show();
    await flush();
    expect(ctx.state).toBe('running');
    expect(lifecycle.getStatus()).toMatchObject({ suspendedWhileHidden: false, waitingForGesture: false });
  });

  it('treats pagehide and pageshow (the back-forward cache) as hide and show', async () => {
    const { ctx, host } = setup();
    host.window.fire('pagehide');
    await flush();
    expect(ctx.state).toBe('suspended');
    host.window.fire('pageshow');
    await flush();
    expect(ctx.state).toBe('running');
  });

  it('resumes on the next tap when the browser refuses to resume without a gesture', async () => {
    const { ctx, host, lifecycle } = setup({ needsGesture: true });
    host.hide();
    await flush();
    host.show();
    await flush();
    expect(ctx.state).toBe('suspended');
    expect(lifecycle.getStatus().waitingForGesture).toBe(true);
    // Heard in the capture phase, before the page's own handlers
    for (const type of ['pointerdown', 'keydown', 'touchend']) expect(host.captured(type)).toBe(1);
    host.gesture(ctx, 'touchend');
    await flush();
    expect(ctx.state).toBe('running');
    expect(lifecycle.getStatus().waitingForGesture).toBe(false);
    // The gesture listeners are gone once the context runs again
    for (const type of ['pointerdown', 'keydown', 'touchend']) expect(host.count(type)).toBe(0);
  });

  it("recovers from iOS's 'interrupted' state (a locked phone or a call) on return or on the next tap", async () => {
    const { ctx, host, lifecycle } = setup({ needsGesture: true });
    ctx.setState('interrupted');
    await flush();
    expect(lifecycle.getStatus().waitingForGesture).toBe(true);
    host.gesture(ctx, 'pointerdown');
    await flush();
    expect(ctx.state).toBe('running');

    // Interrupted while hidden: nothing until the page is shown, then a resume
    const free = setup();
    free.host.hide();
    await flush();
    free.ctx.setState('interrupted');
    await flush();
    const before = free.ctx.resumes;
    free.host.show();
    await flush();
    expect(free.ctx.resumes).toBe(before + 1);
    expect(free.ctx.state).toBe('running');
  });

  it('never resumes audio the user turned off: Audio off stays silent through hide, show and taps', async () => {
    const { ctx, host, lifecycle, setEnabled } = setup({ needsGesture: true });
    setEnabled(false);
    ctx.setState('suspended');
    await flush();
    host.hide();
    host.show();
    host.gesture(ctx);
    await flush();
    expect(ctx.resumes).toBe(0);
    expect(ctx.state).toBe('suspended');
    expect(lifecycle.getStatus().waitingForGesture).toBe(false);
  });

  it('does not resume on a tap when Audio was turned off while it waited for one, and stops waiting', async () => {
    const { ctx, host, lifecycle, setEnabled } = setup({ needsGesture: true });
    ctx.setState('interrupted');
    await flush();
    expect(lifecycle.getStatus().waitingForGesture).toBe(true);
    setEnabled(false);
    const resumes = ctx.resumes;
    host.gesture(ctx, 'pointerdown');
    await flush();
    expect(ctx.resumes).toBe(resumes);
    expect(ctx.state).toBe('interrupted');
    expect(lifecycle.getStatus().waitingForGesture).toBe(false);
    for (const type of ['pointerdown', 'keydown', 'touchend']) expect(host.count(type)).toBe(0);
  });

  it('stops waiting for a tap on detach, so a later tap resumes nothing', async () => {
    const { ctx, host, lifecycle } = setup({ needsGesture: true });
    ctx.setState('interrupted');
    await flush();
    expect(lifecycle.getStatus().waitingForGesture).toBe(true);
    lifecycle.detach();
    expect(lifecycle.getStatus().waitingForGesture).toBe(false);
    for (const type of ['pointerdown', 'keydown', 'touchend']) expect(host.count(type)).toBe(0);
    const resumes = ctx.resumes;
    host.gesture(ctx);
    await flush();
    expect(ctx.resumes).toBe(resumes);
  });

  it('resumes nothing while the page is hidden, even when the context changes state then', async () => {
    const { ctx, host } = setup({ needsGesture: true });
    // Interrupted while visible: the resume is refused and a tap is awaited
    ctx.setState('interrupted');
    await flush();
    // Then hidden: the context was not running, so the lifecycle did not suspend it itself
    host.hide();
    await flush();
    const resumes = ctx.resumes;
    // The browser moves the context on while the page is hidden
    ctx.setState('suspended');
    await flush();
    expect(ctx.resumes).toBe(resumes);
  });

  it('removes every listener on detach', () => {
    const { ctx, host, lifecycle } = setup();
    lifecycle.detach();
    for (const type of ['visibilitychange', 'pointerdown', 'keydown', 'touchend']) expect(host.count(type)).toBe(0);
    for (const type of ['pagehide', 'pageshow']) expect(host.window.count(type)).toBe(0);
    expect(ctx.count('statechange')).toBe(0);
  });
});

/**
 * A phone's context that the engine can also build its graph on: every node and parameter it asks
 * for is a stand-in that accepts any call.
 */
function phoneAudioContext(): PhoneContext {
  const anything: unknown = new Proxy(function () {}, {
    get: (_target, prop) => (prop === 'value' ? 0 : prop === Symbol.toPrimitive ? () => 0 : prop === 'then' ? undefined : anything),
    apply: () => anything,
    set: () => true,
  });
  return new Proxy(new PhoneContext(), {
    get(target, prop, receiver) {
      if (typeof prop === 'string' && prop.startsWith('create') && !(prop in target)) return () => anything;
      if (prop === 'currentTime') return 0;
      if (prop === 'sampleRate') return 48_000;
      if (prop === 'destination') return anything;
      return Reflect.get(target, prop, receiver);
    },
  });
}

function phoneEngine() {
  const ctx = phoneAudioContext();
  const host = new Host();
  const factory = vi.fn(() => ctx as unknown as AudioContext);
  const engine = new ObservatoryAudioEngine({ contextFactory: factory, lifecycleHost: host, impulseResponseUrl: null, bedPreference: 'stereo-diffuse' });
  return { ctx, host, engine, factory };
}

describe('the engine on phones', () => {
  it('attaches the lifecycle when Audio on makes the context, so hiding the page suspends it and showing resumes it', async () => {
    const { ctx, host, engine } = phoneEngine();
    expect(host.count('visibilitychange')).toBe(0);
    engine.enable();
    expect(host.count('visibilitychange')).toBe(1);
    expect(host.audioSession).toEqual({ type: 'ambient' });
    host.hide();
    await flush();
    expect(ctx.state).toBe('suspended');
    host.show();
    await flush();
    expect(ctx.state).toBe('running');
    // Audio on again does not attach a second lifecycle
    engine.disable();
    engine.enable();
    expect(host.count('visibilitychange')).toBe(1);
  });

  it('keeps Audio off silent through hiding and showing the page', async () => {
    const { ctx, host, engine } = phoneEngine();
    engine.enable();
    engine.disable();
    host.hide();
    await flush();
    expect(ctx.state).toBe('suspended');
    const resumes = ctx.resumes;
    host.show();
    host.gesture(ctx);
    await flush();
    expect(ctx.resumes).toBe(resumes);
    expect(ctx.state).toBe('suspended');
  });

  it("resumes an 'interrupted' context when the user turns Audio on again", async () => {
    const { ctx, engine } = phoneEngine();
    engine.enable();
    ctx.needsGesture = true;
    ctx.setState('interrupted');
    await flush();
    // The Audio control is a gesture
    ctx.inGesture = true;
    engine.disable();
    engine.enable();
    ctx.inGesture = false;
    await flush();
    expect(ctx.state).toBe('running');
  });

  it('removes the lifecycle listeners when the engine is disposed', () => {
    const { ctx, host, engine } = phoneEngine();
    engine.enable();
    engine.spatial.dispose();
    expect(host.count('visibilitychange')).toBe(0);
    for (const type of ['pagehide', 'pageshow']) expect(host.window.count(type)).toBe(0);
    expect(ctx.count('statechange')).toBe(0);
  });

  it('makes no AudioContext before a gesture, and none at all where the browser has none', () => {
    const factory = vi.fn(() => null);
    const engine = new ObservatoryAudioEngine({ contextFactory: factory, impulseResponseUrl: null });
    engine.setMasterVolume(0.5);
    engine.setListenerOrientation([0, 0, 0, 1]);
    expect(factory).not.toHaveBeenCalled();
    expect(engine.toggle()).toBe(false);
    expect(factory).toHaveBeenCalledTimes(1);
    expect(engine.getEnabled()).toBe(false);
  });
});
