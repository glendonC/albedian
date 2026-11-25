import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';

// fade-with-words-one (LEAD ANSWER to WEB-UX 22:12 on 24 Nov, on VIDEO's frame strip .lead/video/goto-move/: at the switch
// into the flight the column fades showed for about six frames as empty dark boxes before their words arrived): a fade
// is never drawn without its block. Each fade takes its block's own entrance, the same animation as the block's first
// words (the same opacity on the same frames), and where the block's words leave one after another, it leaves with the
// last of them. A block that comes in whole (the predicted sky's column, the ask bar on the reveal) carries its fade with
// it already; a block whose words stand at once (the views row) needs nothing. The flight's pill row words in one
// stagger after the readout, as the motion record's Go there step 3 has it (motion-seams-one), so its fade does too.

const here = path.dirname(fileURLToPath(import.meta.url));
const motion = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');

/** The declarations of every rule that names this selector, top level or inside the media query named, merged in order. */
function declsOf(selector: string, media?: string): Record<string, string> {
  const found: Record<string, string> = {};
  postcss.parse(motion).walkRules((rule) => {
    const parent = rule.parent;
    const inMedia = parent?.type === 'atrule' ? `@${(parent as postcss.AtRule).name} ${(parent as postcss.AtRule).params}` : undefined;
    if (inMedia !== media) return;
    if (!rule.selectors.map((s) => s.replace(/\s+/g, ' ').trim()).includes(selector)) return;
    rule.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
  });
  return found;
}
const REDUCED = '@media (prefers-reduced-motion: reduce)';

describe('each fade takes its block\'s own entrance: the same animation as its first words', () => {
  const pairs: Array<[string, string, string?]> = [
    // The flight's and Real sky's columns: the words come in at the words' delay, the Simulated tag stands at full
    [
      '.reveal-status:is([data-moment="real-sky"], [data-moment="there"]):not(.is-leaving)::before',
      '.reveal-status:is([data-moment="real-sky"], [data-moment="there"]):not(.is-leaving) > *',
    ],
    // The flight's readout: its words 20 ms after the line's
    ['.flight-readout:not(.is-leaving)::before', '.flight-readout > *'],
    // The open card on the laptop: in with its top line, out with its top line, the last to go
    ['.reveal-card:not(.is-leaving)::before', '.reveal-card__top'],
    ['.reveal-card.is-leaving::before', '.reveal-card.is-leaving .reveal-card__top'],
    // The answer in the card's place: in with the question as heard
    ['.reveal-answer::before', '.reveal-answer__q'],
    // The legend: in with its title, after the marks (and after the lock-on, both ways); out with its title, the last to go
    ['.reveal-legend:not(.is-leaving)::before', '.reveal-legend:not(.is-leaving) > .reveal-legend__title'],
    ['.is-locking .reveal-legend:not(.is-leaving)::before', '.is-locking .reveal-legend:not(.is-leaving) > .reveal-legend__title'],
    ['.is-locking .reveal-legend:not(.is-leaving)::before', '.is-locking .reveal-legend:not(.is-leaving) > .reveal-legend__title', REDUCED],
    ['.reveal-legend.is-leaving::before', '.reveal-legend.is-leaving .reveal-legend__title'],
    // The ask bar after Go there: in with Back to Moment, once the camera is still (on the predicted sky, at the words' delay)
    ['.ask-bar:has(> .look-main)::before', '.ask-bar > .look-main'],
    ['.predsky ~ .ask-bar:has(> .look-main)::before', '.predsky ~ .ask-bar > .look-main'],
    // The flight's pill row on the laptop: in one stagger after the readout; out with its pills at Back to Moment's press
    ['.flight-pills:not(.is-phone)::before', '.flight-pills > *'],
    ['.flight-pills-host.is-leaving .flight-pills:not(.is-phone)::before', '.flight-pills-host.is-leaving .flight-pills > *'],
  ];

  for (const [fade, words, media] of pairs) {
    it(`${fade}${media ? ` (${media})` : ''} enters as ${words}`, () => {
      const want = declsOf(words, media);
      const got = declsOf(fade, media);
      for (const prop of ['animation', 'animation-delay']) {
        if (want[prop] === undefined) continue;
        expect(got[prop], prop).toBe(want[prop]);
      }
      expect(Object.keys(want).some((p) => p.startsWith('animation'))).toBe(true);
    });
  }

  it('leaves the ask bar with its buttons at Go there, while it holds no word that is not leaving', () => {
    expect(declsOf('.ask-bar:not(:has(> :not(.is-leaving)))::before').animation).toBe(declsOf('.ask-bar__ask.is-leaving').animation);
  });

  it("puts the ask bar's Back to Moment rule after its emptying rule, so a bar with Back to Moment takes its entrance", () => {
    const order: string[] = [];
    postcss.parse(motion).walkRules((rule) => {
      if (rule.parent?.type === 'root') order.push(rule.selector.replace(/\s+/g, ' '));
    });
    expect(order.indexOf('.ask-bar:has(> .look-main)::before')).toBeGreaterThan(order.indexOf('.ask-bar:not(:has(> :not(.is-leaving)))::before'));
  });
});

describe('blocks that come in whole or stand at once carry their fade as they are', () => {
  it('the predicted sky\'s column stands at once with its words; the ask bar on the reveal comes in whole', () => {
    expect(declsOf('.predsky .reveal-status').animation).toBe('none');
    expect(declsOf('.ask-bar').animation).toMatch(/^look-mo-words-in /);
  });
});
