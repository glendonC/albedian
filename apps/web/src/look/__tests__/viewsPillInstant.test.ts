import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';

// views-pill-instant-one (gap ten, DECIDED-BY-LEAD 21:18 on 24 Nov, closed on MOTION's four variants): the views pill
// changes at once, its words and fill together in one frame, the press's own motion kept; an exception to the look's
// colour duration, made because every cross-fade measured left the pill unreadable for some frames.

const here = path.dirname(fileURLToPath(import.meta.url));
const styles = path.resolve(here, '../../styles');
const look = fs.readFileSync(path.join(styles, 'look.css'), 'utf8');
const squash = (value: string) => value.replace(/\s+/g, ' ').trim();

/** Each top-level rule's selectors and its transition, in the order the stylesheet writes them. */
function transitions(css: string): Array<{ selectors: string[]; transition: string; index: number }> {
  const out: Array<{ selectors: string[]; transition: string; index: number }> = [];
  let index = 0;
  postcss.parse(css).walkRules((rule) => {
    index += 1;
    if (rule.parent?.type !== 'root') return;
    rule.walkDecls('transition', (decl) => void out.push({ selectors: rule.selectors.map(squash), transition: squash(decl.value), index }));
  });
  return out;
}

describe('the views pill changes at once (views-pill-instant-one)', () => {
  it("keeps only the press's transform in the views item's own transition, after the family's shared list", () => {
    const all = transitions(look);
    const shared = all.find((t) => t.selectors.includes('.look-views__item') && t.selectors.length > 1)!;
    expect(shared.transition).toContain('background-color var(--look-dur-colour)');
    const own = all.filter((t) => t.selectors.length === 1 && t.selectors[0] === '.look-views__item');
    expect(own).toHaveLength(1);
    expect(own[0]!.transition).toBe('transform var(--look-dur-press) var(--look-ease-standard)');
    // Later in the stylesheet at the same weight, so it is the one the pill uses
    expect(own[0]!.index).toBeGreaterThan(shared.index);
  });

  it('leaves the rest of the button family on the colour duration, and no other stylesheet times the views item', () => {
    const shared = transitions(look).find((t) => t.selectors.includes('.look-second'))!;
    expect(shared.transition).toBe(
      'transform var(--look-dur-press) var(--look-ease-standard), background-color var(--look-dur-colour) var(--look-ease-standard), color var(--look-dur-colour) var(--look-ease-standard), border-color var(--look-dur-colour) var(--look-ease-standard)'
    );
    const elsewhere: string[] = [];
    for (const file of fs.readdirSync(styles).filter((name) => name.endsWith('.css') && name !== 'look.css')) {
      for (const t of transitions(fs.readFileSync(path.join(styles, file), 'utf8'))) {
        if (t.selectors.some((s) => /look-views__item/.test(s))) elsewhere.push(`${file} ${t.selectors.join(', ')}`);
      }
    }
    expect(elsewhere).toEqual([]);
  });

  it('still turns every transition off under reduced motion, the views item with them', () => {
    const media = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/.exec(look)?.[1] ?? '';
    expect(media).toContain('.look-views__item');
    expect(media).toContain('transition: none !important');
  });
});
