import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';

// jsdom does not cascade a stylesheet's pointer-events or inherit it, so a button inside a screen that lets the pointer
// through (pointer-events none) looks pressable to a test while a person's press goes to the photo under it. This reads
// the value a browser would give: among the top-level rules of the stylesheets the reveal and the working screen import
// (look.css, reveal.css, motion.css) that set pointer-events and match the element, the most specific wins, the later
// on a tie; an inline style beats them; with none of these, or inherit, the parent's value; auto at the top.

const here = path.dirname(fileURLToPath(import.meta.url));
const SHEETS = ['look.css', 'reveal.css', 'motion.css'].map((name) => fs.readFileSync(path.resolve(here, '../../styles', name), 'utf8'));

interface PointerRule {
  selector: string;
  specificity: number;
  order: number;
  value: string;
}

/** Ids, then classes, attributes and pseudo-classes, then types; :not() and :is() count as their argument. */
function specificity(selector: string): number {
  const s = selector.replace(/:(not|is)\(([^()]*)\)/g, ' $2 ');
  const simple = /#[\w-]+|\.[\w-]+|\[[^\]]*\]|::?[\w-]+(\([^)]*\))?/g;
  const ids = (s.match(/#[\w-]+/g) ?? []).length;
  const classes = (s.match(/\.[\w-]+|\[[^\]]*\]|(?<!:):[\w-]+(\([^)]*\))?/g) ?? []).length;
  const types = (s.replace(simple, ' ').match(/[a-zA-Z][\w-]*/g) ?? []).length;
  return ids * 10000 + classes * 100 + types;
}

const RULES: PointerRule[] = [];
let order = 0;
for (const sheet of SHEETS) {
  postcss.parse(sheet).walkDecls('pointer-events', (decl) => {
    const rule = decl.parent;
    if (rule?.type !== 'rule' || rule.parent?.type !== 'root') return;
    for (const selector of (rule as postcss.Rule).selectors) RULES.push({ selector, specificity: specificity(selector), order: order++, value: decl.value.trim() });
  });
}

function matches(element: Element, selector: string): boolean {
  try {
    return element.matches(selector);
  } catch {
    // A pseudo-element (::after) is not the element itself
    return false;
  }
}

/** The pointer-events value a browser computes for this element from the stylesheets above and its inline style. */
export function winningPointer(element: Element): string {
  for (let node: Element | null = element; node; node = node.parentElement) {
    const inline = (node as HTMLElement).style?.pointerEvents;
    if (inline && inline !== 'inherit') return inline;
    let best: PointerRule | null = null;
    for (const rule of RULES) {
      if (!matches(node, rule.selector)) continue;
      if (!best || rule.specificity > best.specificity || (rule.specificity === best.specificity && rule.order > best.order)) best = rule;
    }
    if (best && best.value !== 'inherit') return best.value;
  }
  return 'auto';
}

/** Every button in the container that a press cannot reach, by its text or label. */
export function unpressable(container: Element): string[] {
  return [...container.querySelectorAll('button')]
    .filter((button) => winningPointer(button) === 'none')
    .map((button) => button.textContent?.trim() || button.getAttribute('aria-label') || '?');
}
