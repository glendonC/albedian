// @vitest-environment jsdom
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';
import type { EvidenceClass } from '@albedian/contracts';
import { LOOK_EVIDENCE, LOOK_GLYPHS } from '@albedian/design-system';
import {
  EvidenceGlyph,
  EvidenceTag,
  IconButton,
  LOOK_ICON_NAMES,
  LOOK_ICON_PATHS,
  LookIcon,
  Logo,
  MainAction,
  SecondAction,
  SmallMainAction,
  ViewPills,
} from '../index.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});
const render = (element: ReactElement) => act(() => root.render(element));
const CLASSES: EvidenceClass[] = ['observed', 'catalog_matched', 'predicted', 'simulated'];

describe('EvidenceTag: every class as a glyph plus a word', () => {
  it.each(CLASSES)('draws the %s glyph and its word, with the long label for a pointer or a screen reader', (c) => {
    render(createElement(EvidenceTag, { evidenceClass: c }));
    const tag = container.querySelector('.look-tag')!;
    expect(tag.getAttribute('data-evidence-class')).toBe(c);
    expect(tag.textContent).toBe(LOOK_EVIDENCE[c].word);
    const glyph = tag.querySelector('svg.look-glyph')!;
    expect(glyph.getAttribute('data-evidence-class')).toBe(c);
    expect(glyph.getAttribute('aria-hidden')).toBe('true');
    expect(glyph.children.length).toBe(LOOK_GLYPHS[c].parts.length);
    expect(glyph.getAttribute('viewBox')).toBe(LOOK_GLYPHS[c].viewBox);
    for (const part of glyph.querySelectorAll('[stroke="currentColor"]')) {
      expect(part.getAttribute('stroke-width')).toBe(String(LOOK_GLYPHS[c].strokeWidth));
    }
    expect(tag.getAttribute('title')).toContain(getEpistemicBadgeDescriptor(c).label);
  });

  it('gives each class its own modifier class, named whole', () => {
    const seen = CLASSES.map((c) => {
      render(createElement(EvidenceTag, { evidenceClass: c }));
      return [...container.querySelector('.look-tag')!.classList].find((k) => k.startsWith('look-tag--'));
    });
    expect(new Set(seen).size).toBe(4);
  });

  it.each(CLASSES)('styles the %s tag with its own colour and tint from the stylesheet', (c) => {
    const token = { observed: 'observed', catalog_matched: 'catalog', predicted: 'predicted', simulated: 'simulated' }[c];
    render(createElement(EvidenceTag, { evidenceClass: c }));
    const modifier = [...container.querySelector('.look-tag')!.classList].find((k) => k.startsWith('look-tag--'))!;
    const css = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../styles/look.css'), 'utf8');
    const rule = new RegExp(`\\.${modifier}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? '';
    expect(rule.replace(/\s+/g, ' ')).toContain(`background: var(--look-ev-${token}-tint);`);
    expect(rule.replace(/\s+/g, ' ')).toContain(`color: var(--look-ev-${token});`);
  });

  it('draws a glyph on its own with the same parts', () => {
    render(createElement(EvidenceGlyph, { evidenceClass: 'predicted' }));
    const circle = container.querySelector('svg.look-glyph circle')!;
    expect(circle.getAttribute('stroke-dasharray')).toBe('2.4 2.4');
    expect(circle.getAttribute('fill')).toBe('none');
  });
});

describe('MainAction: the one main button, its circle apart', () => {
  it('is a real button with its word and a turned arrow in the circle', () => {
    const onClick = vi.fn();
    render(createElement(MainAction, { label: 'Go there', onClick }));
    const button = container.querySelector('button.look-main')!;
    expect(button.getAttribute('type')).toBe('button');
    expect(button.classList.contains('look-size-compact')).toBe(true);
    expect(button.querySelector('.look-main__label')!.textContent).toBe('Go there');
    const circle = button.querySelector('.look-main__circle')!;
    expect(circle.getAttribute('aria-hidden')).toBe('true');
    expect(circle.querySelector('svg.look-i')!.classList.contains('look-turn')).toBe(true);
    act(() => (button as HTMLButtonElement).click());
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('takes the iPhone web view size, 44 px, as wide as its word', () => {
    render(createElement(MainAction, { label: 'Start', size: 'phone' }));
    expect(container.querySelector('button.look-main')!.classList.contains('look-size-phone')).toBe(true);
    render(createElement(ViewPills, { label: 'View', views: [{ id: 'a', label: 'A' }], current: 'a', onSelect: () => {}, size: 'phone' }));
    expect(container.querySelector('.look-views')!.classList.contains('look-size-phone')).toBe(true);
  });

  it('turns only the arrow: another icon sits still', () => {
    render(createElement(MainAction, { label: 'Upload', icon: 'upload-simple', size: 'home' }));
    const button = container.querySelector('button.look-main')!;
    expect(button.classList.contains('look-size-home')).toBe(true);
    expect(button.querySelector('svg.look-i')!.classList.contains('look-turn')).toBe(false);
  });

  it('does nothing when disabled', () => {
    const onClick = vi.fn();
    render(createElement(MainAction, { label: 'Go there', onClick, disabled: true }));
    const button = container.querySelector('button.look-main') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    act(() => button.click());
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('SmallMainAction and SecondAction', () => {
  it('draws the small main action as a filled pill with its dark circle inside', () => {
    render(createElement(SmallMainAction, { label: 'Open' }));
    const button = container.querySelector('button.look-small-main')!;
    expect(button.textContent).toBe('Open');
    expect(button.querySelector('.look-small-main__circle svg.look-i')).not.toBeNull();
  });

  it('draws the second action as an outlined pill, with a filled circle only when given an icon', () => {
    render(createElement(SecondAction, { label: 'Credits' }));
    expect(container.querySelector('button.look-second')!.classList.contains('look-second--with-circle')).toBe(false);
    expect(container.querySelector('.look-second__circle')).toBeNull();
    render(createElement(SecondAction, { label: 'Ask out loud', icon: 'microphone' }));
    const button = container.querySelector('button.look-second')!;
    expect(button.classList.contains('look-second--with-circle')).toBe(true);
    expect(button.querySelector('.look-second__circle svg.look-i')).not.toBeNull();
    expect(button.textContent).toBe('Ask out loud');
  });
});

describe('ViewPills: joined outlined pills, the current one filled', () => {
  const views = [
    { id: 'photo', label: 'Your photo' },
    { id: 'sky', label: 'Real sky' },
    { id: '3d', label: '3D', disabled: true },
  ];

  it('marks the current view pressed and filled, and chooses another by click', () => {
    const onSelect = vi.fn();
    render(createElement(ViewPills, { label: 'View', views, current: 'photo', onSelect }));
    const group = container.querySelector('.look-views')!;
    expect(group.getAttribute('role')).toBe('group');
    expect(group.getAttribute('aria-label')).toBe('View');
    const items = [...group.querySelectorAll('button.look-views__item')] as HTMLButtonElement[];
    expect(items.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false']);
    expect(items.map((b) => b.classList.contains('is-current'))).toEqual([true, false, false]);
    expect(items[2]!.disabled).toBe(true);
    act(() => items[1]!.click());
    expect(onSelect).toHaveBeenCalledWith('sky');
  });

  it('moves focus with the arrow keys, skipping a view that is off and wrapping at the ends', () => {
    render(createElement(ViewPills, { label: 'View', views, current: 'photo', onSelect: () => {} }));
    const items = [...container.querySelectorAll('button.look-views__item')] as HTMLButtonElement[];
    items[0]!.focus();
    let lastPrevented = false;
    const press = (key: string) =>
      act(() => {
        const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
        document.activeElement!.dispatchEvent(event);
        lastPrevented = event.defaultPrevented;
      });
    press('ArrowRight');
    expect(document.activeElement).toBe(items[1]);
    // An arrow that moves between the views does not also scroll the page
    expect(lastPrevented).toBe(true);
    press('a');
    expect(lastPrevented).toBe(false);
    expect(document.activeElement).toBe(items[1]);
    press('ArrowRight');
    expect(document.activeElement).toBe(items[0]);
    press('ArrowLeft');
    expect(document.activeElement).toBe(items[1]);
    press('Home');
    expect(document.activeElement).toBe(items[0]);
    press('End');
    expect(document.activeElement).toBe(items[1]);
  });
});

describe('ViewPills: every key it handles, from the first, a middle and the last view', () => {
  // Four views, the second off: the keys move among one, three and four, skipping two and wrapping at the ends
  const views = [
    { id: 'one', label: 'One' },
    { id: 'two', label: 'Two', disabled: true },
    { id: 'three', label: 'Three' },
    { id: 'four', label: 'Four' },
  ];
  const KEYS: Array<[key: string, from: string, to: string]> = [
    ['ArrowRight', 'One', 'Three'],
    ['ArrowRight', 'Three', 'Four'],
    ['ArrowRight', 'Four', 'One'],
    ['ArrowDown', 'One', 'Three'],
    ['ArrowDown', 'Three', 'Four'],
    ['ArrowDown', 'Four', 'One'],
    ['ArrowLeft', 'One', 'Four'],
    ['ArrowLeft', 'Three', 'One'],
    ['ArrowLeft', 'Four', 'Three'],
    ['ArrowUp', 'One', 'Four'],
    ['ArrowUp', 'Three', 'One'],
    ['ArrowUp', 'Four', 'Three'],
    ['Home', 'One', 'One'],
    ['Home', 'Three', 'One'],
    ['Home', 'Four', 'One'],
    ['End', 'One', 'Four'],
    ['End', 'Three', 'Four'],
    ['End', 'Four', 'Four'],
  ];
  it.each(KEYS)('%s from %s moves to %s, and the page does not scroll', (key, from, to) => {
    render(createElement(ViewPills, { label: 'View', views, current: 'one', onSelect: () => {} }));
    const named = (label: string) =>
      [...container.querySelectorAll<HTMLButtonElement>('button.look-views__item')].find((b) => b.textContent === label)!;
    named(from).focus();
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    act(() => {
      named(from).dispatchEvent(event);
    });
    expect(document.activeElement).toBe(named(to));
    expect(event.defaultPrevented).toBe(true);
  });
});

describe('IconButton: an outlined circle with no word, so its name is its label', () => {
  it('names itself for a screen reader and hides the drawing', () => {
    render(createElement(IconButton, { label: 'Close', icon: 'x' }));
    const button = container.querySelector('button.look-icon')!;
    expect(button.getAttribute('aria-label')).toBe('Close');
    expect(button.getAttribute('aria-pressed')).toBeNull();
    expect(button.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
    expect(button.textContent).toBe('');
  });

  it('says on or off when it is a switch', () => {
    render(createElement(IconButton, { label: 'Sound', icon: 'speaker-high', pressed: true }));
    const button = container.querySelector('button.look-icon')!;
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.classList.contains('is-on')).toBe(true);
  });
});

describe('Logo: the name always as the logo', () => {
  it('reads as Albedian and draws the word and the mark at the size given', () => {
    render(createElement(Logo, { sizePx: 132 }));
    const logo = container.querySelector('.look-logo') as HTMLElement;
    expect(logo.getAttribute('role')).toBe('img');
    expect(logo.getAttribute('aria-label')).toBe('Albedian');
    expect(logo.style.getPropertyValue('--look-logo-fs')).toBe('132px');
    expect(logo.querySelector('.look-logo__word')!.textContent).toBe('albedian');
    expect(logo.querySelector('svg.look-logo__mark circle')).not.toBeNull();
  });

  it('gives each logo on a page its own mask, so two never share a cut-out', () => {
    render(createElement('div', null, createElement(Logo, {}), createElement(Logo, { sizePx: 52 })));
    const masks = [...container.querySelectorAll('mask')].map((m) => m.id);
    expect(masks.length).toBe(2);
    expect(new Set(masks).size).toBe(2);
    for (const circle of container.querySelectorAll('circle')) {
      expect(masks.map((id) => `url(#${id})`)).toContain(circle.getAttribute('mask'));
    }
  });
});

describe('LookIcon: only the Phosphor icons the approved screens use', () => {
  it('draws each as one path in the Phosphor 256 box, hidden from screen readers', () => {
    expect([...LOOK_ICON_NAMES].sort()).toEqual([
      'arrow-left',
      'arrow-right',
      'arrow-square-out',
      'microphone',
      'speaker-high',
      'upload-simple',
      'warning-circle',
      'x',
    ]);
    for (const name of LOOK_ICON_NAMES) {
      render(createElement(LookIcon, { name }));
      const svg = container.querySelector('svg.look-i')!;
      expect(svg.getAttribute('viewBox')).toBe('0 0 256 256');
      expect(svg.getAttribute('aria-hidden')).toBe('true');
      expect(svg.querySelectorAll('path').length).toBe(1);
    }
  });

  it('draws each from its Phosphor file kept beside the licence, path for path, the file listed with its sha256', () => {
    const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../public/notices/phosphor');
    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.svg'));
    expect(files.map((f) => f.slice(0, -4)).sort()).toEqual([...LOOK_ICON_NAMES].sort());
    const record = JSON.parse(fs.readFileSync(path.join(dir, 'PROVENANCE.json'), 'utf8')) as {
      files: Array<{ path: string; sha256: string }>;
    };
    for (const name of LOOK_ICON_NAMES) {
      const bytes = fs.readFileSync(path.join(dir, `${name}.svg`));
      const paths = [...bytes.toString('utf8').matchAll(/<path d="([^"]+)"\/>/g)].map((m) => m[1]);
      expect(paths, name).toEqual([LOOK_ICON_PATHS[name]]);
      const listed = record.files.find((f) => f.path === `${name}.svg`);
      expect(listed?.sha256, name).toBe(crypto.createHash('sha256').update(bytes).digest('hex'));
    }
  });
});
