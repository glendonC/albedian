import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';

// Every transition and animation in the web's stylesheets takes its time from a motion token, unless the motion record
// approves that literal time in a row of its own (MOTION-READY-web.md: the volume capsule's 250 ms ease-out, the
// restoration compare line's 400 ms glide). A literal time with no row has no source, and the record retires it.

const styles = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../styles');

const APPROVED = [
  'reveal.css .restore-compare:not(.is-drag) .restore-compare__line transition: left 0.4s cubic-bezier(0.08, 0.62, 0.1, 0.98)',
  'reveal.css .restore-compare:not(.is-drag) .restore-compare__pic transition: clip-path 0.4s cubic-bezier(0.08, 0.62, 0.1, 0.98)',
  'reveal.css .sound-control__pop transition: opacity 0.25s ease-out, transform 0.25s ease-out',
];

/** A time written out (0.4s, 250ms), not one inside a name such as a keyframes name. */
const LITERAL_TIME = /(?<![\w-])\d*\.?\d+m?s\b/;

function literalTimes(): string[] {
  const out: string[] = [];
  for (const file of fs.readdirSync(styles).filter((name) => name.endsWith('.css')).sort()) {
    postcss.parse(fs.readFileSync(path.join(styles, file), 'utf8')).walkDecls(/^(transition|animation)(-duration|-delay)?$/, (decl) => {
      if (!LITERAL_TIME.test(decl.value.replace(/var\([^()]*\)/g, ''))) return;
      const selector = ((decl.parent as postcss.Rule | undefined)?.selector ?? '').replace(/\s+/g, ' ');
      out.push(`${file} ${selector} ${decl.prop}: ${decl.value.replace(/\s+/g, ' ')}`);
    });
  }
  return out.sort();
}

describe('the web times its motion from the motion record', () => {
  it('writes no time out in a transition or animation except the record\'s approved rows', () => {
    expect(literalTimes()).toEqual(APPROVED);
  });

  it('lets Ask out loud keep the approved button family\'s transitions, its press scale\'s 150 ms with them', () => {
    // A transition of its own would replace look.css's whole list, and the press would snap to its scale
    const own: string[] = [];
    for (const file of ['reveal.css', 'motion.css']) {
      postcss.parse(fs.readFileSync(path.join(styles, file), 'utf8')).walkDecls(/^transition/, (decl) => {
        const selector = (decl.parent as postcss.Rule | undefined)?.selector ?? '';
        if (/\.ask-bar__(ask|type)\b/.test(selector)) own.push(`${file} ${selector}`);
      });
    }
    expect(own).toEqual([]);
    const look = fs.readFileSync(path.join(styles, 'look.css'), 'utf8');
    expect(look).toMatch(/\.look-second,[\s\S]*?transition:\s*transform var\(--look-dur-press\)/);
  });

  it('times nothing from the hud motion tokens the record retires, which have no source', () => {
    // MOTION-READY-web.md: "Retire, as they have no source: ... hud-tokens.css --hud-dur-* and --hud-ease-*"
    const retired: string[] = [];
    for (const file of fs.readdirSync(styles).filter((name) => name.endsWith('.css')).sort()) {
      const text = fs.readFileSync(path.join(styles, file), 'utf8');
      for (const m of text.matchAll(/--hud-(dur|ease|caption-stagger)[\w-]*/g)) retired.push(`${file} ${m[0]}`);
    }
    expect(retired).toEqual([]);
  });

  it('still finds a time written out, so the check is not empty', () => {
    const found: string[] = [];
    postcss.parse('.a { transition: opacity 0.18s ease; } .b { animation: x-2s var(--t) both; }').walkDecls((decl) => {
      if (LITERAL_TIME.test(decl.value.replace(/var\([^()]*\)/g, ''))) found.push((decl.parent as postcss.Rule | undefined)?.selector ?? '');
    });
    expect(found).toEqual(['.a']);
  });
});
