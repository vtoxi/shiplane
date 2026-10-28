// A small, safe Markdown renderer for the Manual page (headings, lists, tables, code, quotes, links).
// It builds DOM nodes; nothing is ever inserted as HTML.
import { h } from './kit.js';

export const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

const CODE = 'rounded bg-sunken px-1.5 py-0.5 font-mono text-[0.85em]';

function inline(text) {
  const out = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) out.push(h('code', { class: CODE }, t.slice(1, -1)));
    else if (m[2]) out.push(h('strong', { class: 'font-semibold' }, t.slice(2, -2)));
    else if (m[3]) out.push(h('em', {}, t.slice(1, -1)));
    else {
      const [, label, url] = /\[([^\]]+)\]\(([^)\s]+)\)/.exec(t);
      if (url.startsWith('#/')) out.push(h('a', { href: url, class: 'text-brand hover:underline' }, label));
      else if (url.startsWith('#')) out.push(h('a', { href: '#/manual', class: 'text-brand hover:underline', onClick: (e) => { e.preventDefault(); const el = document.getElementById(url.slice(1)); if (el) el.scrollIntoView({ behavior: 'smooth' }); } }, label));
      else if (/^https?:\/\//.test(url)) out.push(h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'text-brand hover:underline' }, label));
      else out.push(label);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Returns { sections: [{id, title, el}], intro: [nodes] }: one section per "## " heading. */
export function renderMarkdown(src) {
  const lines = src.replace(/\r\n/g, '\n').split('\n');
  const intro = [];
  const sections = [];
  let target = intro;
  let i = 0;
  const push = (node) => target.push(node);
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l || '');
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = /^```(\w*)/.exec(line);
    if (fence) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      push(h('pre', { class: 'console my-4 max-h-none' }, buf.join('\n')));
      continue;
    }
    const hd = /^(#{1,4})\s+(.*)$/.exec(line);
    if (hd) {
      const level = hd[1].length, title = hd[2].trim();
      if (level === 2) {
        const sec = { id: slug(title), title, el: h('section', { id: slug(title), class: 'scroll-mt-6' }, h('h2', { class: 'mb-3 mt-10 border-b border-line pb-2 text-lg' }, title)) };
        sections.push(sec);
        target = { push: (n) => sec.el.append(n) };
      } else {
        push(h(level === 1 ? 'h1' : level === 3 ? 'h3' : 'h4', { id: slug(title), class: level === 1 ? 'text-2xl' : level === 3 ? 'mb-2 mt-6 text-base scroll-mt-6' : 'mb-1 mt-4 text-sm font-semibold scroll-mt-6' }, title));
      }
      i++;
      continue;
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { push(h('hr', { class: 'my-6 border-line' })); i++; continue; }
    if (line.trim().startsWith('|') && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]));
      push(h('div', { class: 'my-4 overflow-x-auto rounded-lg border border-line' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, head.map((c) => h('th', {}, inline(c))))),
        h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c) => h('td', { class: 'py-2.5 align-top' }, inline(c)))))))));
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      push(h('div', { class: 'alert alert-info my-4' }, h('div', {}, inline(buf.join(' ')))));
      continue;
    }
    const li = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(line);
    if (li) {
      const ordered = /\d/.test(li[2]);
      const list = h(ordered ? 'ol' : 'ul', { class: `my-3 space-y-1.5 pl-6 ${ordered ? 'list-decimal' : 'list-disc'}` });
      let lastItem = null;
      while (i < lines.length) {
        const m = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(lines[i]);
        if (!m) break;
        if (m[1].length >= 2 && lastItem) {
          let sub = lastItem.querySelector(':scope > ul');
          if (!sub) { sub = h('ul', { class: 'mt-1.5 list-disc space-y-1 pl-5' }); lastItem.append(sub); }
          sub.append(h('li', {}, inline(m[3])));
        } else { lastItem = h('li', {}, inline(m[3])); list.append(lastItem); }
        i++;
      }
      push(list);
      continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*>|\s*([-*]|\d+\.)\s|\s*\|)/.test(lines[i])) buf.push(lines[i++].trim());
    if (!buf.length) { i++; continue; }
    push(h('p', { class: 'my-3 leading-7' }, inline(buf.join(' '))));
  }
  return { intro, sections };
}
