// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HomeScreen } from '../HomeScreen.js';
import { carriesFiles, droppedFile } from '../homeDrop.js';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { HOME_SCREEN } from './homeEntry.js';

// A photo dragged onto the window (arrive, approved 2026-11-12, screen 2): anywhere on the home's window takes the
// file; the home dims and blurs behind a 1 px rounded frame inset 20 px, with a 112 px filled circle holding the upload
// icon and "Drop to upload" under it. The values are the approved page's (#l-drop in .cache/uiux/arrive/index.html).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const homeCss = fs.readFileSync(path.resolve(here, '../../styles/home.css'), 'utf8');

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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const noop = () => {};
const q = (selector: string) => container.querySelector(selector);
const DROP = '[data-testid="home-drop"]';

function renderHome(props: Partial<Parameters<typeof HomeScreen>[0]> = {}) {
  act(() => {
    root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: noop, onFileChosen: noop, ...props }));
  });
}

interface Carried {
  types: string[];
  files: File[];
  dropEffect?: string;
}
const photo = (name = 'night.jpg') => new File([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], name, { type: 'image/jpeg' });
const withFiles = (...files: File[]): Carried => ({ types: ['Files'], files });
const withText = (): Carried => ({ types: ['text/plain'], files: [] });

/** One drag event on the window, as a browser sends it, carrying `data`; returns it so a test reads what was done to it. */
function drag(type: 'dragenter' | 'dragover' | 'dragleave' | 'drop', data: Carried | null, target: EventTarget = window): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: data });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

/** The declarations of the one rule with this selector, as postcss reads them. */
function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let rules = 0;
  postcss.parse(homeCss).walkRules((rule) => {
    if (rule.selector !== selector || rule.parent?.type !== 'root') return;
    rules += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(rules).toBe(1);
  return found;
}

describe('what a drag carries', () => {
  it('counts as files only when the browser says the drag holds files', () => {
    expect(carriesFiles(withFiles(photo()))).toBe(true);
    // While a file is over the window a browser names the kind and withholds the list
    expect(carriesFiles({ types: ['Files'], files: [] })).toBe(true);
    expect(carriesFiles(withText())).toBe(false);
    expect(carriesFiles({ types: ['text/uri-list', 'text/html'], files: [] })).toBe(false);
    expect(carriesFiles({ types: [], files: [] })).toBe(false);
    expect(carriesFiles(null)).toBe(false);
    expect(carriesFiles(undefined)).toBe(false);
  });

  it('takes the first file of a drop, or none', () => {
    const first = photo('first.jpg');
    expect(droppedFile(withFiles(first, photo('second.jpg')))).toBe(first);
    expect(droppedFile(withFiles())).toBeNull();
    expect(droppedFile(withText())).toBeNull();
    // A list that holds a file under a drag the browser does not call files is not taken
    expect(droppedFile({ types: ['text/plain'], files: [photo()] })).toBeNull();
    expect(droppedFile(null)).toBeNull();
  });
});

describe('a photo dragged onto the home', () => {
  it('shows nothing until a file is over the window', () => {
    renderHome();
    expect(q(DROP)).toBeNull();
    expect(q(HOME_SCREEN)!.textContent).toBe('CreditsalbedianUpload');
  });

  it('shows the approved overlay, word for word, over the home it leaves in place', () => {
    renderHome();
    drag('dragenter', withFiles(photo()));
    const drop = q(DROP)!;
    expect(drop.textContent).toBe('Drop to upload');
    expect(drop.querySelector('.home-drop__frame')).not.toBeNull();
    const circle = drop.querySelector('.home-drop__circle')!;
    expect(circle.getAttribute('aria-hidden')).toBe('true');
    expect(circle.querySelector('svg.look-i path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS['upload-simple']);
    expect(drop.querySelector('.home-drop__words')!.textContent).toBe('Drop to upload');
    // The home is still there behind it: the overlay is its last child, and the home's own words are unchanged
    const home = q(HOME_SCREEN)!;
    expect(home.lastElementChild).toBe(drop);
    expect(home.querySelector('button.look-main')!.textContent).toBe('Upload');
    expect(home.textContent).toBe('CreditsalbedianUploadDrop to upload');
  });

  it('takes a drag anywhere on the window, not only over the home element', () => {
    renderHome();
    drag('dragenter', withFiles(photo()), document.body);
    expect(q(DROP)).not.toBeNull();
  });

  it('shows nothing for a drag that holds no file (text, a link), and leaves that drag alone', () => {
    renderHome();
    expect(drag('dragenter', withText()).defaultPrevented).toBe(false);
    expect(q(DROP)).toBeNull();
    expect(drag('dragover', withText()).defaultPrevented).toBe(false);
    expect(drag('drop', withText()).defaultPrevented).toBe(false);
    expect(q(DROP)).toBeNull();
  });

  it('tells the browser a file may be dropped, as a copy', () => {
    renderHome();
    drag('dragenter', withFiles(photo()));
    const data = withFiles(photo());
    const over = drag('dragover', data);
    expect(over.defaultPrevented).toBe(true);
    expect(data.dropEffect).toBe('copy');
  });

  it('stays while the file crosses from one part of the window to another, and goes when it leaves the window', () => {
    renderHome();
    const data = withFiles(photo());
    // Crossing into a child sends the child's enter before the parent's leave
    drag('dragenter', data);
    drag('dragenter', data, document.body);
    drag('dragleave', data);
    expect(q(DROP)).not.toBeNull();
    drag('dragleave', data, document.body);
    expect(q(DROP)).toBeNull();
    // A leave with no enter before it does not go below none: the next file shows the overlay at once
    drag('dragleave', data);
    drag('dragenter', data);
    expect(q(DROP)).not.toBeNull();
    // and that one enter is undone by one leave
    drag('dragleave', data);
    expect(q(DROP)).toBeNull();
  });

  it('hands the dropped file on once, as the picker does, closes, and stops the browser opening the file', () => {
    const onFileChosen = vi.fn();
    renderHome({ onFileChosen });
    const file = photo('dropped.jpg');
    drag('dragenter', withFiles(file));
    const dropped = drag('drop', withFiles(file, photo('second.jpg')));
    expect(dropped.defaultPrevented).toBe(true);
    expect(onFileChosen).toHaveBeenCalledTimes(1);
    expect(onFileChosen).toHaveBeenCalledWith(file);
    expect(q(DROP)).toBeNull();
    // The count starts again: one enter and one leave after a drop show and hide it
    drag('dragenter', withFiles(photo()));
    expect(q(DROP)).not.toBeNull();
    drag('dragleave', withFiles(photo()));
    expect(q(DROP)).toBeNull();
  });

  it('closes and chooses nothing when the drop holds no file after all', () => {
    const onFileChosen = vi.fn();
    renderHome({ onFileChosen });
    drag('dragenter', withFiles(photo()));
    const dropped = drag('drop', withFiles());
    expect(dropped.defaultPrevented).toBe(true);
    expect(onFileChosen).not.toHaveBeenCalled();
    expect(q(DROP)).toBeNull();
  });

  it('takes no drop while Credits is open in its place, and takes one again after Back', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const onFileChosen = vi.fn();
    renderHome({ onFileChosen });
    act(() => (q('button.look-second') as HTMLButtonElement).click());
    expect(q(HOME_SCREEN)).toBeNull();
    expect(drag('dragenter', withFiles(photo())).defaultPrevented).toBe(false);
    expect(q(DROP)).toBeNull();
    expect(drag('drop', withFiles(photo())).defaultPrevented).toBe(false);
    expect(onFileChosen).not.toHaveBeenCalled();
    act(() => ([...container.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === 'Back') as HTMLButtonElement).click());
    const file = photo('after.jpg');
    drag('dragenter', withFiles(file));
    expect(q(DROP)).not.toBeNull();
    drag('drop', withFiles(file));
    expect(onFileChosen).toHaveBeenCalledWith(file);
  });

  it('stops listening when the home leaves', () => {
    const onFileChosen = vi.fn();
    renderHome({ onFileChosen });
    act(() => root.render(createElement('div')));
    expect(drag('drop', withFiles(photo())).defaultPrevented).toBe(false);
    expect(onFileChosen).not.toHaveBeenCalled();
  });
});

describe("the overlay's rules are the approved page's", () => {
  it('dims and blurs the home, above everything on it, and takes no pointer of its own', () => {
    expect(declarationsOf('.home-drop')).toEqual({
      position: 'absolute',
      inset: '0',
      'z-index': '5',
      background: 'rgba(0, 0, 0, 0.6)',
      '-webkit-backdrop-filter': 'blur(8px)',
      'backdrop-filter': 'blur(8px)',
      'pointer-events': 'none',
    });
  });

  it('draws the 1 px rounded frame 20 px inside the window', () => {
    expect(declarationsOf('.home-drop__frame')).toEqual({
      position: 'absolute',
      inset: '20px',
      border: '1px solid rgba(244, 243, 239, 0.45)',
      'border-radius': '28px',
    });
  });

  it('centres a 112 px filled circle with a 36 px icon, and the words 22 px under it at 17 px', () => {
    expect(declarationsOf('.home-drop__mid')).toEqual({
      position: 'absolute',
      left: '50%',
      top: '50%',
      transform: 'translate(-50%, -50%)',
      display: 'grid',
      'justify-items': 'center',
      gap: '22px',
    });
    expect(declarationsOf('.home-drop__circle')).toEqual({
      '--look-ic': '36px',
      display: 'grid',
      'place-items': 'center',
      width: '112px',
      height: '112px',
      'border-radius': '50%',
      background: 'var(--look-ink)',
      color: 'var(--look-night)',
    });
    expect(declarationsOf('.home-drop__words')).toEqual({
      font: '400 17px / 1 var(--look-font-sans)',
      'letter-spacing': '-0.01em',
    });
  });

  it('moves nothing: the overlay has no transition and no animation', () => {
    const rules = homeCss.replace(/\/\*[\s\S]*?\*\//g, '').match(/\.home-drop[^{]*\{[^}]*\}/g) ?? [];
    expect(rules).toHaveLength(5);
    for (const rule of rules) {
      expect(rule).not.toMatch(/transition|animation/);
    }
  });
});
