import { h, Alert, Button, PageHeader, Input, Skeleton } from '../kit.js';
import { renderMarkdown } from '../markdown.js';

// One section at a time, picked from the menu (or found with the search), so the manual is never one endless scroll.
export function ManualPage() {
  const content = h('article', { class: 'min-w-0' }, h('div', { class: 'p-6' }, Skeleton(5)));
  const toc = h('nav', { class: 'flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:space-y-0.5 lg:overflow-visible lg:pb-0', 'aria-label': 'Manual sections' });
  const pager = h('div', { class: 'mt-8 flex justify-between gap-2 border-t border-line pt-4' });
  let secs = [], intro = [], current = 0;
  const search = Input({ placeholder: 'Search the manual...', onInput: (e) => filter(e.target.value.toLowerCase()) });
  search.setAttribute('data-search', '');

  function select(i) {
    current = i;
    secs.forEach((s, n) => { s.el.hidden = n !== i; s.link.setAttribute('aria-current', String(n === i)); s.link.classList.toggle('bg-sunken', n === i); s.link.classList.toggle('font-medium', n === i); });
    const prev = secs[i - 1], next = secs[i + 1];
    pager.replaceChildren(prev ? Button({ label: prev.title, size: 'sm', onClick: () => select(i - 1) }) : h('span'), next ? Button({ label: next.title, size: 'sm', variant: 'primary', onClick: () => select(i + 1) }) : h('span'));
    window.scrollTo({ top: 0 });
  }

  function filter(q) {
    if (!q) { secs.forEach((s) => { s.link.hidden = false; }); select(current); return; }
    const hits = secs.map((s, n) => [s, n]).filter(([s]) => s.el.textContent.toLowerCase().includes(q));
    secs.forEach((s) => { s.link.hidden = !hits.some(([x]) => x === s); s.el.hidden = !(hits.length && hits[0][0] === s); });
    if (hits.length) select(hits[0][1]);
  }

  fetch('/static/manual.md').then((r) => (r.ok ? r.text() : Promise.reject(new Error('manual not found')))).then((md) => {
    const parsed = renderMarkdown(md);
    secs = parsed.sections; intro = parsed.intro;
    secs.forEach((s, i) => {
      s.link = h('a', { href: '#/manual', class: 'block whitespace-nowrap rounded-md lg:whitespace-normal px-2.5 py-1.5 text-sm text-muted hover:bg-sunken hover:text-ink', onClick: (e) => { e.preventDefault(); search.value = ''; secs.forEach((x) => { x.link.hidden = false; }); select(i); } }, s.title);
      toc.append(s.link);
    });
    content.replaceChildren(...intro, ...secs.map((s) => s.el), pager);
    // #/manual?section=<heading id> (help pop-ups, links) opens the section holding that heading and scrolls to it
    const want = (location.hash.match(/[?&]section=([\w-]+)/) || [])[1];
    const at = want ? secs.findIndex((x) => x.id === want || x.el.querySelector(`[id="${want}"]`)) : -1;
    select(at >= 0 ? at : 0);
    if (at >= 0 && secs[at].id !== want) { const el = secs[at].el.querySelector(`[id="${want}"]`); if (el) setTimeout(() => el.scrollIntoView({ block: 'start' }), 0); }
  }).catch((e) => content.replaceChildren(Alert({ tone: 'bad', title: 'Could not load the manual' }, e.message)));

  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Manual', description: 'How everything in this app works, step by step.' }),
    h('div', { class: 'grid grid-cols-1 gap-6 lg:grid-cols-[14rem_1fr] lg:gap-8' },
      h('aside', { class: 'min-w-0 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:self-start lg:overflow-y-auto' }, h('div', { class: 'mb-3' }, search), toc),
      h('section', { class: 'card min-w-0 px-6 pb-8 pt-2 sm:px-10' }, content))) };
}
