// Help next to complex steps: a small "?" that pops up the matching Manual section, with "View in full" to open it in the Manual.
import { h, icon } from './kit.js';
import { renderMarkdown } from './markdown.js';

let manual = null;   // the parsed manual, fetched once on first use
const load = () => (manual = manual || fetch('/static/manual.md').then((r) => (r.ok ? r.text() : Promise.reject(new Error('manual not found')))).then(renderMarkdown));

/** The nodes of one manual part: a whole section (##) or one sub-section (### up to the next heading of that level or higher). */
export function excerpt(parsed, id) {
  for (const s of parsed.sections) {
    if (s.id === id) return [...s.el.children].slice(1);
    const kids = [...s.el.children];
    const i = kids.findIndex((k) => k.id === id);
    if (i >= 0) {
      const level = kids[i].tagName;
      const out = [];
      for (let j = i + 1; j < kids.length && !(kids[j].tagName === 'H2' || kids[j].tagName === 'H3' || (level === 'H4' && kids[j].tagName === 'H4')); j++) out.push(kids[j]);
      return out;
    }
  }
  return null;
}

let openPop = null;
document.addEventListener('click', (e) => { if (openPop && !openPop.contains(e.target)) { openPop.remove(); openPop = null; } });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openPop) { openPop.remove(); openPop = null; } });

/** "?" button for a manual section id (the heading's slug, e.g. 'wildcard-certificates'). */
export function HelpTip(id, label = 'Help') {
  const btn = h('button', { type: 'button', class: 'inline-flex items-center justify-center rounded-full text-subtle hover:text-brand', style: 'width:1.25rem;height:1.25rem;vertical-align:middle', title: `${label}: open the manual`, 'aria-label': `${label}: open the manual` }, icon('info', 'h-4 w-4'));
  btn.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (openPop) { openPop.remove(); openPop = null; }
    const body = h('div', { class: 'space-y-2 overflow-y-auto pr-1 text-sm', style: 'max-height:18rem' }, h('p', { class: 'text-muted' }, 'Loading...'));
    const pop = h('div', { class: 'card shadow-lg', role: 'dialog', 'aria-label': label, style: 'position:fixed;z-index:60;width:min(26rem,calc(100vw - 2rem));padding:1rem' },
      body, h('div', { class: 'mt-3 flex justify-end border-t border-line pt-3' },
        h('a', { href: `#/manual?section=${id}`, class: 'inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline', onClick: () => { pop.remove(); openPop = null; } }, 'View in full', icon('external', 'h-3.5 w-3.5'))));
    pop.addEventListener('click', (ev) => ev.stopPropagation());
    const r = btn.getBoundingClientRect();
    const left = Math.max(16, Math.min(r.left, window.innerWidth - Math.min(416, window.innerWidth - 32) - 16));
    pop.style.left = `${left}px`;
    const below = window.innerHeight - r.bottom, above = r.top;          // open on the side with more room, sized to fit it
    body.style.maxHeight = `${Math.max(120, Math.min(288, (below >= above ? below : above) - 90))}px`;
    if (below >= above) pop.style.top = `${r.bottom + 6}px`; else pop.style.bottom = `${window.innerHeight - r.top + 6}px`;
    document.body.append(pop);
    openPop = pop;
    try {
      const nodes = excerpt(await load(), id);
      body.replaceChildren(...(nodes && nodes.length ? nodes.map((n) => n.cloneNode(true)) : [h('p', { class: 'text-muted' }, 'This part of the manual was not found.')]));
    } catch (err) { body.replaceChildren(h('p', { class: 'text-muted' }, err.message)); }
  });
  return btn;
}

/** A card title with its help icon. */
export const withHelp = (title, id) => h('span', { class: 'inline-flex items-center gap-1.5' }, title, HelpTip(id, typeof title === 'string' ? title : 'Help'));
