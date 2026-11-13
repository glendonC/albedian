// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, VoiceMachineState } from '@albedian/contracts';
import {
  createEmptyObservationSession,
  executeVoiceOrTextCommand,
} from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { choosePhotoOnHome } from './homeEntry.js';

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return null;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));

vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: {
    toggle: vi.fn(() => false),
    setMasterVolume: vi.fn(),
    setScaleRegime: vi.fn(),
    playDetentClick: vi.fn(),
  },
}));

vi.mock('../../audio/SkyAudioFeed.js', () => ({
  skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() },
}));

vi.mock('../CoalescingBar.js', () => ({
  CoalescingBar: () => null,
}));

const { VoiceCommandHud } = await import('../VoiceCommandHud.js');
const { SpatialObservatoryViewport } = await import('../SpatialObservatoryViewport.js');
const { askSuggestionsFor } = await import('../askSuggestions.js');

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const cssPath = path.resolve(__dirname, '../../styles/albedian.css');
const cssSource = fs.readFileSync(cssPath, 'utf8');

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

const noop = () => {};

interface HudProps {
  voiceState?: VoiceMachineState;
  lastStatusBanner?: string | null;
  lastUtterance?: string | null;
  commandText?: string;
  setCommandText?: (text: string) => void;
  onRunUtterance?: (utterance: string, modality?: 'voice' | 'text' | 'pointer') => void;
  onPushToTalk?: () => void;
  selectedFinding?: CelestialFinding | null;
}

function renderHud(props: HudProps = {}) {
  act(() => {
    root.render(
      createElement(VoiceCommandHud, {
        voiceState: 'off',
        lastStatusBanner: null,
        lastUtterance: null,
        commandText: '',
        setCommandText: noop,
        onRunUtterance: noop,
        onPushToTalk: noop,
        ...props,
      })
    );
  });
}

const q = <T extends Element = HTMLElement>(testId: string) =>
  container.querySelector(`[data-testid="${testId}"]`) as T | null;

function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = window): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

function typeInto(input: HTMLInputElement, value: string) {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function starOf(evidenceClass: CelestialFinding['evidenceClass'], overrides: Partial<CelestialFinding> = {}): CelestialFinding {
  const finding = createSyntheticCalibratedSession().observationSolution?.findings.find((f) => f.evidenceClass === evidenceClass);
  if (!finding) throw new Error(`fixture has no ${evidenceClass} finding`);
  return { ...finding, title: 'Tabit', ...overrides } as CelestialFinding;
}

const tabit = () => starOf('catalog_matched');

describe('VoiceCommandHud listen key (hud-4)', () => {
  it('shows the listening state on the listen key and draws no screen-edge glow', () => {
    renderHud({ voiceState: 'off' });
    const off = q<HTMLButtonElement>('voice-ptt-btn')!;
    expect(off.getAttribute('aria-pressed')).toBe('false');
    expect(off.getAttribute('aria-label')).toBe('Ask out loud');
    expect(off.classList.contains('is-listening')).toBe(false);
    expect(off.textContent).toContain('Press');
    expect(off.textContent).toContain('Space');
    expect(off.textContent).not.toMatch(/\bHold\b/);
    expect(container.querySelector('.alb-voice-edge-glow, [data-testid="voice-edge-glow"]')).toBeNull();

    renderHud({ voiceState: 'listening' });
    const on = q<HTMLButtonElement>('voice-ptt-btn')!;
    expect(on.getAttribute('aria-pressed')).toBe('true');
    expect(on.getAttribute('aria-label')).toBe('Listening, speak now');
    expect(on.classList.contains('is-listening')).toBe(true);
    expect(on.textContent).toContain('Listening, speak now');
    expect(container.querySelector('.alb-voice-edge-glow, [data-testid="voice-edge-glow"]')).toBeNull();
  });

  it('starts push to talk once on Space, never on auto-repeat, inside a field or on a focused button', () => {
    const onPushToTalk = vi.fn();
    renderHud({ onPushToTalk });

    const first = press(' ', { code: 'Space' });
    expect(onPushToTalk).toHaveBeenCalledTimes(1);
    expect(first.defaultPrevented).toBe(true);

    press(' ', { code: 'Space', repeat: true });
    expect(onPushToTalk).toHaveBeenCalledTimes(1);

    const field = document.createElement('input');
    const area = document.createElement('textarea');
    const button = document.createElement('button');
    document.body.append(field, area, button);
    press(' ', { code: 'Space' }, field);
    press(' ', { code: 'Space' }, area);
    press(' ', { code: 'Space' }, button);
    press(' ', { code: 'Space', ctrlKey: true });
    press(' ', { code: 'Space', metaKey: true });
    press(' ', { code: 'Space', altKey: true });
    field.remove();
    area.remove();
    button.remove();
    expect(onPushToTalk).toHaveBeenCalledTimes(1);

    act(() => q<HTMLButtonElement>('voice-ptt-btn')!.click());
    expect(onPushToTalk).toHaveBeenCalledTimes(2);
  });

  it('stops listening for keys once unmounted', () => {
    const onPushToTalk = vi.fn();
    renderHud({ onPushToTalk });
    act(() => root.render(createElement('div')));
    press(' ', { code: 'Space' });
    press('/');
    expect(onPushToTalk).not.toHaveBeenCalled();
  });
});

describe('VoiceCommandHud captions (hud-4)', () => {
  it('shows captions only with a non-empty banner, in a polite status region with speaker labels', () => {
    renderHud({ lastStatusBanner: '   ', lastUtterance: 'What star is that?' });
    expect(q('voice-status-banner')).toBeNull();

    const answer = 'Tabit (Pi3 Orionis) is 8.02 parsecs away [Bailer-Jones 2021, Gaia DR3].';
    renderHud({ voiceState: 'speaking', lastStatusBanner: answer, lastUtterance: '   ' });
    const banner = q('voice-status-banner')!;
    expect(banner.getAttribute('role')).toBe('status');
    expect(banner.getAttribute('aria-live')).toBe('polite');
    expect(banner.textContent).toContain(answer);
    expect(banner.querySelector('.alb-voice-caption--answer .alb-voice-speaker')?.textContent).toBe('Albedian');
    expect(q('voice-last-utterance')).toBeNull();

    renderHud({ voiceState: 'speaking', lastStatusBanner: answer, lastUtterance: 'How far away is Tabit?' });
    expect(q('voice-last-utterance')?.textContent).toBe('How far away is Tabit?');
    expect(container.querySelector('.alb-voice-caption--you .alb-voice-speaker')?.textContent).toBe('You');
  });

  it('sets each answer word in its own span with its stagger index, keeping the sentence intact', () => {
    renderHud({ lastStatusBanner: 'Tabit is about 26 light-years away.' });
    const words = [...container.querySelectorAll('.alb-voice-caption--answer .alb-voice-word')] as HTMLElement[];
    expect(words.map((w) => w.textContent)).toEqual(['Tabit', 'is', 'about', '26', 'light-years', 'away.']);
    expect(words.map((w) => w.style.getPropertyValue('--i'))).toEqual(['0', '1', '2', '3', '4', '5']);
  });
});

describe('VoiceCommandHud ask palette (hud-4)', () => {
  it('opens on the type key with an empty field, focuses it, and wires the field and Ask to the session handlers', () => {
    const setCommandText = vi.fn();
    const onRunUtterance = vi.fn();
    renderHud({ commandText: 'old words', setCommandText, onRunUtterance });

    const hud = q('voice-command-hud')!;
    expect(hud.getAttribute('data-hud-interactive')).toBe('true');
    expect(q('ask-palette')).toBeNull();
    expect(q('voice-command-input')).toBeNull();

    act(() => q<HTMLButtonElement>('voice-type-btn')!.click());
    expect(setCommandText).toHaveBeenLastCalledWith('');
    const palette = q('ask-palette')!;
    expect(palette.getAttribute('data-hud-interactive')).toBe('true');

    renderHud({ commandText: '   ', setCommandText, onRunUtterance });
    const input = q<HTMLInputElement>('voice-command-input')!;
    expect(document.activeElement).toBe(input);
    expect(input.getAttribute('aria-label')).toBe('Voice or text command');

    const submit = q<HTMLButtonElement>('voice-command-submit')!;
    expect(submit.disabled).toBe(true);
    act(() => {
      (container.querySelector('form.alb-voice-bar') as HTMLFormElement).dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true })
      );
    });
    expect(onRunUtterance).not.toHaveBeenCalled();

    typeInto(input, '  Select Betelgeuse  ');
    expect(setCommandText).toHaveBeenCalledWith('  Select Betelgeuse  ');

    renderHud({ commandText: '  Select Betelgeuse  ', setCommandText, onRunUtterance });
    expect(q<HTMLButtonElement>('voice-command-submit')!.disabled).toBe(false);
    act(() => {
      (container.querySelector('form.alb-voice-bar') as HTMLFormElement).dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true })
      );
    });
    expect(onRunUtterance).toHaveBeenCalledTimes(1);
    expect(onRunUtterance).toHaveBeenCalledWith('Select Betelgeuse', 'text');
    expect(q('ask-palette')).toBeNull();
  });

  it('opens on "/" from anywhere but a field or with a modifier held', () => {
    renderHud();
    const field = document.createElement('input');
    const area = document.createElement('textarea');
    document.body.append(field, area);
    press('/', {}, field);
    press('/', {}, area);
    field.remove();
    area.remove();
    press('/', { metaKey: true });
    press('/', { altKey: true });
    press('/', { ctrlKey: true });
    expect(q('ask-palette')).toBeNull();

    const slash = press('/');
    expect(slash.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q<HTMLInputElement>('voice-command-input'));
  });

  it('closes on Esc from its field without letting Esc reach Back to Moment, and returns focus', () => {
    renderHud();
    const typeKey = q<HTMLButtonElement>('voice-type-btn')!;
    act(() => typeKey.focus());
    act(() => typeKey.click());
    const input = q<HTMLInputElement>('voice-command-input')!;
    expect(document.activeElement).toBe(input);

    const reachedWindow = vi.fn();
    window.addEventListener('keydown', reachedWindow);
    const esc = press('Escape', {}, input);
    window.removeEventListener('keydown', reachedWindow);
    expect(esc.defaultPrevented).toBe(true);
    expect(reachedWindow).not.toHaveBeenCalled();
    expect(q('ask-palette')).toBeNull();
    expect(document.activeElement).toBe(typeKey);
  });

  it('leaves the palette open on an Esc that comes from outside it', () => {
    renderHud();
    press('/');
    press('Escape');
    expect(q('ask-palette')).not.toBeNull();
  });

  it('returns focus to what had it before the palette opened once a question is asked', () => {
    const onRunUtterance = vi.fn();
    renderHud({ selectedFinding: tabit(), onRunUtterance });
    const typeKey = q<HTMLButtonElement>('voice-type-btn')!;
    act(() => typeKey.focus());
    act(() => typeKey.click());
    act(() => (container.querySelector('[data-testid="ask-suggestions"] [role="option"]') as HTMLElement).click());
    expect(onRunUtterance).toHaveBeenCalledWith('How far away is it?', 'text');
    expect(document.activeElement).toBe(typeKey);
  });

  it('offers no suggestions when no star is selected', () => {
    renderHud({ selectedFinding: null });
    press('/');
    expect(q('ask-suggestions')).toBeNull();
    expect(q('ask-palette-head')).toBeNull();
    expect(q<HTMLInputElement>('voice-command-input')!.placeholder).toBe('Ask about a star or give a command');
  });

  it('offers Take me there only for a star the app can fly to', () => {
    // A predicted star was not measured in the pixels, so the relabelled one keeps no detection (REQ-SKY-5.2)
    for (const star of [starOf('observed'), starOf('catalog_matched', { evidenceClass: 'predicted', sourceDetection: undefined }), starOf('catalog_matched', { inferredDistancePc: undefined })]) {
      renderHud({ selectedFinding: star });
      press('/');
      const texts = [...container.querySelectorAll('[data-testid="ask-suggestions"] .alb-ask-text')].map((o) => o.textContent);
      expect(texts, star.evidenceClass).toEqual(['How far away is it?']);
      press('Escape', {}, q<HTMLInputElement>('voice-command-input')!);
    }
  });

  it('lists the selected star suggestions and asks the one chosen with the arrows and Enter', () => {
    const onRunUtterance = vi.fn();
    const star = tabit();
    renderHud({ selectedFinding: star, onRunUtterance });
    press('/');
    expect(q('ask-palette-head')?.textContent).toBe('Ask about Tabit');
    expect(q<HTMLInputElement>('voice-command-input')!.placeholder).toBe('Ask about Tabit');
    const options = () => [...container.querySelectorAll('[data-testid="ask-suggestions"] [role="option"]')] as HTMLElement[];
    expect(options().map((o) => o.querySelector('.alb-ask-text')?.textContent)).toEqual([...askSuggestionsFor(star)]);
    expect(options().map((o) => o.querySelector('.alb-ask-n')?.textContent)).toEqual(['1', '2']);
    expect(options().every((o) => o.getAttribute('aria-selected') === 'false')).toBe(true);

    const input = q<HTMLInputElement>('voice-command-input')!;
    press('ArrowDown', {}, input);
    press('ArrowDown', {}, input);
    expect(options().map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'true']);
    expect(input.getAttribute('aria-activedescendant')).toBe(options()[1]!.id);
    press('ArrowDown', {}, input);
    expect(options().map((o) => o.getAttribute('aria-selected'))).toEqual(['true', 'false']);
    press('ArrowUp', {}, input);
    expect(options().map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'true']);

    const enter = press('Enter', {}, input);
    expect(enter.defaultPrevented).toBe(true);
    expect(onRunUtterance).toHaveBeenCalledWith('Take me there', 'text');
    expect(q('ask-palette')).toBeNull();
  });

  it('chooses the last suggestion when ArrowUp comes first', () => {
    renderHud({ selectedFinding: tabit() });
    press('/');
    press('ArrowUp', {}, q<HTMLInputElement>('voice-command-input')!);
    const selected = [...container.querySelectorAll('[data-testid="ask-suggestions"] [role="option"]')].map((o) =>
      o.getAttribute('aria-selected')
    );
    expect(selected).toEqual(['false', 'true']);
  });

  it('asks a suggestion by its number on an empty field, or by a click', () => {
    const onRunUtterance = vi.fn();
    renderHud({ selectedFinding: tabit(), onRunUtterance });
    press('/');
    press('2', {}, q<HTMLInputElement>('voice-command-input')!);
    expect(onRunUtterance).toHaveBeenLastCalledWith('Take me there', 'text');

    renderHud({ selectedFinding: tabit(), onRunUtterance, commandText: 'how' });
    act(() => q<HTMLButtonElement>('voice-type-btn')!.click());
    press('1', {}, q<HTMLInputElement>('voice-command-input')!);
    expect(onRunUtterance).toHaveBeenCalledTimes(1);

    const first = container.querySelector('[data-testid="ask-suggestions"] [role="option"]') as HTMLElement;
    act(() => first.click());
    expect(onRunUtterance).toHaveBeenLastCalledWith('How far away is it?', 'text');
  });
});

describe('SpatialObservatoryViewport wiring (hud-4)', () => {
  function renderViewport(session: ObservationSession, selectedFinding: CelestialFinding | null, handlers: Record<string, unknown> = {}) {
    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session,
          setSession: noop,
          selectedFinding,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: 'Take me back',
          setCommandText: noop,
          onRunUtterance: noop,
          onSelectFinding: noop,
          onPushToTalk: noop,
          onTriggerUpload: noop,
          ...handlers,
        })
      );
    });
  }

  it('passes session state, the selected star and the handlers to VoiceCommandHud once past the landing overlay', () => {
    const onPushToTalk = vi.fn();
    const setCommandText = vi.fn();
    const onRunUtterance = vi.fn();

    const baseSession = createEmptyObservationSession('2025-03-07T11:00:00.000Z');
    const afterCmd = executeVoiceOrTextCommand(baseSession, 'Show me this sky a thousand years ago', 'voice').session;
    const finding = starOf('catalog_matched');
    const session: ObservationSession = {
      ...afterCmd,
      selectedFindingId: finding.id,
      machines: { ...afterCmd.machines, voice: 'listening' },
      lastStatusBanner:
        'The app cannot show the sky at another year yet: the view does not move the stars to a chosen year.',
    };

    renderViewport(session, finding, { onPushToTalk, setCommandText, onRunUtterance });

    // While the landing overlay is active, the voice HUD stays hidden so it never overlaps the hero footer
    expect(q('voice-command-hud')).toBeNull();
    choosePhotoOnHome(container);

    expect(q('voice-command-hud')).not.toBeNull();
    expect(q('voice-ptt-btn')?.getAttribute('aria-pressed')).toBe('true');
    expect(q('voice-last-utterance')?.textContent).toBe('Show me this sky a thousand years ago');
    expect(q('voice-status-banner')?.textContent).toContain(
      'The app cannot show the sky at another year yet: the view does not move the stars to a chosen year.'
    );

    act(() => q<HTMLButtonElement>('voice-type-btn')!.click());
    expect(setCommandText).toHaveBeenLastCalledWith('');
    expect(q('ask-palette-head')?.textContent).toBe(`Ask about ${finding.title}`);
    const input = q<HTMLInputElement>('voice-command-input')!;
    expect(input.value).toBe('Take me back');
    typeInto(input, 'Select Rigel');
    expect(setCommandText).toHaveBeenCalledWith('Select Rigel');

    act(() => q<HTMLButtonElement>('voice-ptt-btn')!.click());
    expect(onPushToTalk).toHaveBeenCalledTimes(1);

    act(() => {
      (container.querySelector('form.alb-voice-bar') as HTMLFormElement).dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true })
      );
    });
    expect(onRunUtterance).toHaveBeenCalledWith('Take me back', 'text');
  });

  it('offers no suggestions when the session has no selection, whatever finding App falls back to', () => {
    const base = createSyntheticCalibratedSession();
    const fallback = base.observationSolution!.findings[0]!;
    renderViewport({ ...base, selectedFindingId: null }, fallback);
    choosePhotoOnHome(container);
    act(() => q<HTMLButtonElement>('voice-type-btn')!.click());
    expect(q('ask-palette')).not.toBeNull();
    expect(q('ask-suggestions')).toBeNull();
    expect(q('ask-palette-head')).toBeNull();
  });
});

describe('voice element CSS (hud-4)', () => {
  const start = cssSource.indexOf('MOMENT 07 VOICE');
  const end = cssSource.indexOf('/* ====', cssSource.indexOf('*/', start) + 2);
  const block = cssSource.slice(start, end);

  /** Declarations of the first rule whose selector list is exactly `selector`, in the voice block. */
  function rule(selector: string, within: string = block): Record<string, string> {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = new RegExp(`(^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`).exec(within);
    const out: Record<string, string> = {};
    for (const raw of (match?.[2] ?? '').split(';')) {
      const colon = raw.indexOf(':');
      if (colon > 0) out[raw.slice(0, colon).trim()] = raw.slice(colon + 1).trim();
    }
    return out;
  }

  function mediaBody(header: RegExp): string {
    const m = header.exec(block);
    if (!m) return '';
    let depth = 0;
    for (let i = m.index; i < block.length; i++) {
      if (block[i] === '{') depth++;
      else if (block[i] === '}' && --depth === 0) return block.slice(m.index, i + 1);
    }
    return '';
  }

  it('keeps the voice rules in their own block, with the screen-edge glow gone', () => {
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    expect(cssSource).not.toContain('.alb-voice-edge-glow');
  });

  it('builds every glass surface from the HUD tokens, with no drawn border', () => {
    const surfaces: Array<[string, string, string]> = [
      ['.alb-listen-pill', 'var(--hud-glass-fill)', 'var(--hud-glass-filter)'],
      ['.alb-ask-palette', 'var(--hud-glass-fill)', 'var(--hud-glass-filter)'],
      ['.alb-voice-caption', 'var(--hud-caption-fill)', 'var(--hud-caption-filter)'],
    ];
    for (const [selector, fill, filter] of surfaces) {
      const decl = rule(selector);
      expect(decl.background, selector).toBe(fill);
      expect(decl['backdrop-filter'], selector).toBe(filter);
      expect(decl['-webkit-backdrop-filter'], selector).toBe(filter);
      expect(decl['box-shadow'], selector).toMatch(/var\(--hud-(hairline|caption-hairline)\)/);
      expect(decl['box-shadow'], selector).toContain('var(--hud-specular)');
      expect(decl['border-radius'], selector).toMatch(/^var\(--hud-radius-[a-z]+\)/);
      expect(Object.keys(decl).filter((k) => k === 'border' || k.startsWith('border-') && k !== 'border-radius'), selector).toEqual([]);
    }
  });

  it('sets every font in the block from the HUD stacks', () => {
    const fonts = [...block.matchAll(/font-family:\s*([^;]+);/g)].map((m) => m[1]?.trim());
    expect(fonts.length).toBeGreaterThan(0);
    for (const font of fonts) expect(font).toMatch(/^var\(--hud-font-(sans|mono)\)$/);
    for (const m of block.matchAll(/\bfont:\s*([^;]+);/g)) expect(m[1]).toMatch(/var\(--hud-font-(sans|mono)\)$/);
  });

  it('animates captions and the palette with the HUD motion tokens and turns animation off under reduced motion', () => {
    expect(rule('.alb-voice-word').animation).toMatch(/var\(--hud-dur-caption-word\) var\(--hud-ease-out-expo\)/);
    expect(rule('.alb-voice-word')['animation-delay']).toBe('calc(var(--i, 0) * var(--hud-caption-stagger))');
    expect(rule('.alb-ask-palette').animation).toMatch(/var\(--hud-dur-enter\) var\(--hud-ease-spring\)/);
    const reduced = mediaBody(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(reduced).toMatch(/\.alb-voice-word,\s*\.alb-ask-palette\s*\{[^}]*animation:\s*none;/);
  });

  it('pins the voice element to the bottom and turns the palette into a bottom sheet on a phone', () => {
    expect(rule('.alb-voice-hud').position).toBe('fixed');
    expect(rule('.alb-voice-hud').bottom).toBe('28px');
    expect(rule('.alb-voice-hud').transform).toBeUndefined();
    const phone = mediaBody(/@media\s*\(max-width:\s*720px\)/);
    expect(rule('.alb-voice-hud', phone).bottom).toBe('16px');
    expect(rule('.alb-ask-palette', phone).bottom).toBe('0');
    expect(rule('.alb-ask-palette', phone).top).toBe('auto');
  });

  it('draws key chips and the listening halo from HUD tokens', () => {
    expect(block).toMatch(/\.alb-voice-hud kbd,\s*\.alb-ask-palette kbd\s*\{[^}]*background:\s*var\(--hud-key-fill\);[^}]*color:\s*var\(--hud-key-ink\);/);
    expect(rule('.alb-listen-key.is-listening .alb-listen-ring')['box-shadow']).toBe('0 0 0 4px var(--hud-highlight-halo)');
    expect(block).not.toMatch(/202,\s*221,\s*255/);
  });

  it('shows key hints only where there is a keyboard', () => {
    const touch = mediaBody(/@media\s*\(hover:\s*none\)\s*and\s*\(pointer:\s*coarse\)/);
    expect(rule('.alb-key-only', touch).display).toBe('none');
    expect(rule('.alb-touch-only', touch).display).toBe('inline');
    expect(rule('.alb-touch-only').display).toBe('none');
  });
});
