// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import {
  createEmptyObservationSession,
  executeVoiceOrTextCommand,
} from '@albedian/observation';

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

describe('VoiceCommandHud (experience-3, moment 07 pattern)', () => {
  it('renders the Siri-style screen-edge glow only when voiceState is listening', () => {
    const noop = () => {};
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
        })
      );
    });
    expect(container.querySelector('[data-testid="voice-edge-glow"]')).toBeNull();
    const pttOff = container.querySelector('[data-testid="voice-ptt-btn"]') as HTMLButtonElement;
    expect(pttOff.getAttribute('aria-pressed')).toBe('false');
    expect(pttOff.textContent).toBe('Push to talk');
    expect(pttOff.classList.contains('is-listening')).toBe(false);

    act(() => {
      root.render(
        createElement(VoiceCommandHud, {
          voiceState: 'listening',
          lastStatusBanner: null,
          lastUtterance: null,
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onPushToTalk: noop,
        })
      );
    });
    const glow = container.querySelector('[data-testid="voice-edge-glow"]');
    expect(glow).not.toBeNull();
    expect(glow?.getAttribute('aria-hidden')).toBe('true');
    const pttOn = container.querySelector('[data-testid="voice-ptt-btn"]') as HTMLButtonElement;
    expect(pttOn.getAttribute('aria-pressed')).toBe('true');
    expect(pttOn.textContent).toBe('Listening');
    expect(pttOn.classList.contains('is-listening')).toBe(true);
  });

  it('renders lastStatusBanner and lastUtterance only when non-empty and marks the banner as a polite status region', () => {
    const noop = () => {};
    act(() => {
      root.render(
        createElement(VoiceCommandHud, {
          voiceState: 'off',
          lastStatusBanner: '   ',
          lastUtterance: 'What star is that?',
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onPushToTalk: noop,
        })
      );
    });
    expect(container.querySelector('[data-testid="voice-status-banner"]')).toBeNull();

    act(() => {
      root.render(
        createElement(VoiceCommandHud, {
          voiceState: 'speaking',
          lastStatusBanner: 'Tabit (Pi3 Orionis) is 8.02 parsecs away [Bailer-Jones 2021, Gaia DR3].',
          lastUtterance: '   ',
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onPushToTalk: noop,
        })
      );
    });
    const bannerOnly = container.querySelector('[data-testid="voice-status-banner"]') as HTMLElement;
    expect(bannerOnly).not.toBeNull();
    expect(bannerOnly.getAttribute('role')).toBe('status');
    expect(bannerOnly.getAttribute('aria-live')).toBe('polite');
    expect(bannerOnly.textContent).toContain('Tabit (Pi3 Orionis) is 8.02 parsecs away');
    expect(container.querySelector('[data-testid="voice-last-utterance"]')).toBeNull();

    act(() => {
      root.render(
        createElement(VoiceCommandHud, {
          voiceState: 'speaking',
          lastStatusBanner: 'Tabit (Pi3 Orionis) is 8.02 parsecs away [Bailer-Jones 2021, Gaia DR3].',
          lastUtterance: 'How far away is Tabit?',
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onPushToTalk: noop,
        })
      );
    });
    const utt = container.querySelector('[data-testid="voice-last-utterance"]');
    expect(utt).not.toBeNull();
    expect(utt?.textContent).toBe('How far away is Tabit?');
  });

  it('wires Push to talk, text input changes, and form submission to onPushToTalk, setCommandText, and onRunUtterance', () => {
    const onPushToTalk = vi.fn();
    const setCommandText = vi.fn();
    const onRunUtterance = vi.fn();

    act(() => {
      root.render(
        createElement(VoiceCommandHud, {
          voiceState: 'off',
          lastStatusBanner: null,
          lastUtterance: null,
          commandText: '   ',
          setCommandText,
          onRunUtterance,
          onPushToTalk,
        })
      );
    });

    const hud = container.querySelector('[data-testid="voice-command-hud"]') as HTMLElement;
    expect(hud.getAttribute('data-hud-interactive')).toBe('true');

    const pttBtn = container.querySelector('[data-testid="voice-ptt-btn"]') as HTMLButtonElement;
    act(() => pttBtn.click());
    expect(onPushToTalk).toHaveBeenCalledTimes(1);

    const submitBtn = container.querySelector('[data-testid="voice-command-submit"]') as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);

    const form = container.querySelector('form.alb-voice-bar') as HTMLFormElement;
    act(() => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(onRunUtterance).not.toHaveBeenCalled();

    const input = container.querySelector('[data-testid="voice-command-input"]') as HTMLInputElement;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(input, '  Select Betelgeuse  ');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(setCommandText).toHaveBeenCalledWith('  Select Betelgeuse  ');

    act(() => {
      root.render(
        createElement(VoiceCommandHud, {
          voiceState: 'off',
          lastStatusBanner: null,
          lastUtterance: null,
          commandText: '  Select Betelgeuse  ',
          setCommandText,
          onRunUtterance,
          onPushToTalk,
        })
      );
    });
    expect(submitBtn.disabled).toBe(false);

    act(() => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(onRunUtterance).toHaveBeenCalledTimes(1);
    expect(onRunUtterance).toHaveBeenCalledWith('Select Betelgeuse', 'text');
  });

  it('wires SpatialObservatoryViewport props and session.lastStatusBanner into VoiceCommandHud once past the landing overlay', () => {
    const onPushToTalk = vi.fn();
    const setCommandText = vi.fn();
    const onRunUtterance = vi.fn();
    const noop = () => {};

    const baseSession = createEmptyObservationSession('2025-03-07T11:00:00.000Z');
    const afterCmd = executeVoiceOrTextCommand(
      baseSession,
      'Show me this sky a thousand years ago',
      'voice'
    ).session;
    const session: ObservationSession = {
      ...afterCmd,
      machines: {
        ...afterCmd.machines,
        voice: 'listening',
      },
      lastStatusBanner:
        'The app cannot show the sky at another year yet: the view does not move the stars to a chosen year.',
    };

    act(() => {
      root.render(
        createElement(SpatialObservatoryViewport, {
          session,
          setSession: noop,
          selectedFinding: null,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl: null,
          setUploadedMediaUrl: noop,
          commandText: 'Take me back',
          setCommandText,
          onRunUtterance,
          onSelectFinding: noop,
          onPushToTalk,
          onTriggerUpload: noop,
        })
      );
    });

    // While the landing overlay is active, the voice HUD stays hidden so it never overlaps the hero footer
    expect(container.querySelector('[data-testid="voice-command-hud"]')).toBeNull();

    // Clicking Launch Albedian enters the observatory and reveals the voice HUD
    const launchBtn = container.querySelector('[data-testid="enter-observatory-btn"]') as HTMLButtonElement;
    act(() => launchBtn.click());

    expect(container.querySelector('[data-testid="voice-command-hud"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="voice-edge-glow"]')).not.toBeNull();
    const utt = container.querySelector('[data-testid="voice-last-utterance"]');
    expect(utt?.textContent).toBe('Show me this sky a thousand years ago');
    const banner = container.querySelector('[data-testid="voice-status-banner"]');
    expect(banner?.textContent).toContain(
      'The app cannot show the sky at another year yet: the view does not move the stars to a chosen year.'
    );

    const input = container.querySelector('[data-testid="voice-command-input"]') as HTMLInputElement;
    expect(input.value).toBe('Take me back');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(input, 'Select Rigel');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(setCommandText).toHaveBeenCalledWith('Select Rigel');

    const pttBtn = container.querySelector('[data-testid="voice-ptt-btn"]') as HTMLButtonElement;
    act(() => pttBtn.click());
    expect(onPushToTalk).toHaveBeenCalledTimes(1);

    const form = container.querySelector('form.alb-voice-bar') as HTMLFormElement;
    act(() => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(onRunUtterance).toHaveBeenCalledWith('Take me back', 'text');
  });

  it('defines perimeter edge glow, bottom HUD, and narrow-width phone rules in albedian.css', () => {
    expect(cssSource).toMatch(/\.alb-voice-edge-glow\s*\{[\s\S]*?position:\s*fixed;/);
    expect(cssSource).toMatch(/\.alb-voice-edge-glow\s*\{[\s\S]*?inset:\s*0;/);
    expect(cssSource).toMatch(/\.alb-voice-edge-glow\s*\{[\s\S]*?pointer-events:\s*none;/);
    expect(cssSource).toMatch(/\.alb-voice-hud\s*\{[\s\S]*?position:\s*fixed;/);
    expect(cssSource).toMatch(/\.alb-voice-hud\s*\{[\s\S]*?bottom:\s*28px;/);
    expect(cssSource).toMatch(/\.alb-voice-status-banner\s*\{[\s\S]*?border:/);
    expect(cssSource).toMatch(/\.alb-voice-bar\s*\{[\s\S]*?display:\s*flex;/);
    expect(cssSource).toMatch(/\.alb-voice-ptt-btn\.is-listening\s*\{[\s\S]*?background:\s*#8ab4d8;/);
    expect(cssSource).toMatch(/@media\s*\(max-width:\s*720px\)[\s\S]*?\.alb-voice-hud\s*\{[\s\S]*?left:\s*12px;[\s\S]*?right:\s*12px;/);
  });
});
