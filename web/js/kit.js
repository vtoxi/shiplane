// Component kit: small, reusable building blocks. Everything is built with h() (real DOM nodes, never innerHTML)
// and styled only with the classes defined in frontend/src/input.css, so the look stays consistent.
import { duration } from './format.js';
import { LOGOS } from './logos.js';

// ---------------------------------------------------------------- DOM helper
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'for') el.htmlFor = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v === true) el.setAttribute(k, '');
    else if (k in el && typeof v !== 'object') { try { el[k] = v; } catch (e) { el.setAttribute(k, v); } }
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

export function append(el, kids) {
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const clear = (el) => { el.replaceChildren(); return el; };

/** Replace the content of el with kids. Unlike el.replaceChildren, null/false are skipped and arrays are flattened (replaceChildren would print them as text). */
export const fill = (el, ...kids) => append(clear(el), kids);

// --------------------------------------------------------------------- icons
// 24x24 stroke icons. String = path; {c:[cx,cy,r]} circle; {r:[x,y,w,h,rx]} rect; {e:[cx,cy,rx,ry]} ellipse.
const ICONS = {
  sites: [{ r: [3, 3, 7, 7, 1] }, { r: [14, 3, 7, 7, 1] }, { r: [14, 14, 7, 7, 1] }, { r: [3, 14, 7, 7, 1] }],
  rocket: ['M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z', 'm12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z', 'M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0', 'M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5'],
  cloud: ['M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z'],
  server: [{ r: [2, 2, 20, 8, 2] }, { r: [2, 14, 20, 8, 2] }, 'M6 6h.01', 'M6 18h.01'],
  database: [{ e: [12, 5, 9, 3] }, 'M3 5v14a9 3 0 0 0 18 0V5', 'M3 12a9 3 0 0 0 18 0'],
  activity: ['M22 12h-4l-3 9L9 3l-3 9H2'],
  book: ['M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20'],
  settings: ['M4 21v-7', 'M4 10V3', 'M12 21v-9', 'M12 8V3', 'M20 21v-5', 'M20 12V3', 'M1 14h6', 'M9 8h6', 'M17 16h6'],
  refresh: ['M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8', 'M21 3v5h-5', 'M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16', 'M8 16H3v5'],
  plus: ['M5 12h14', 'M12 5v14'],
  minus: ['M5 12h14'],
  up: ['m18 15-6-6-6 6'],
  x: ['M18 6 6 18', 'm6 6 12 12'],
  search: [{ c: [11, 11, 8] }, 'm21 21-4.3-4.3'],
  trash: ['M3 6h18', 'M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6', 'M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2', 'M10 11v6', 'M14 11v6'],
  external: ['M15 3h6v6', 'M10 14 21 3', 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'],
  play: ['M5 3l14 9-14 9V3z'],
  stop: [{ r: [5, 5, 14, 14, 2] }],
  undo: ['M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8', 'M3 3v5h5'],
  file: ['M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z', 'M14 2v4a2 2 0 0 0 2 2h4', 'M10 9H8', 'M16 13H8', 'M16 17H8'],
  check: [{ c: [12, 12, 10] }, 'm9 12 2 2 4-4'],
  alert: ['m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3', 'M12 9v4', 'M12 17h.01'],
  info: [{ c: [12, 12, 10] }, 'M12 16v-4', 'M12 8h.01'],
  chevron: ['m9 18 6-6-6-6'],
  folder: ['M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z'],
  sun: [{ c: [12, 12, 4] }, 'M12 2v2', 'M12 20v2', 'm4.93 4.93 1.41 1.41', 'm17.66 17.66 1.41 1.41', 'M2 12h2', 'M20 12h2', 'm6.34 17.66-1.41 1.41', 'm19.07 4.93-1.41 1.41'],
  moon: ['M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z'],
  globe: [{ c: [12, 12, 10] }, 'M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20', 'M2 12h20'],
  terminal: ['m4 17 6-6-6-6', 'M12 19h8'],
  cpu: [{ r: [4, 4, 16, 16, 2] }, { r: [9, 9, 6, 6, 0] }, 'M15 2v2', 'M15 20v2', 'M9 2v2', 'M9 20v2', 'M2 15h2', 'M2 9h2', 'M20 15h2', 'M20 9h2'],
  disk: ['M22 12H2', 'M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z', 'M6 16h.01', 'M10 16h.01'],
  link: ['M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71', 'M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'],
  upload: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M17 8 12 3 7 8', 'M12 3v12'],
  download: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M7 10l5 5 5-5', 'M12 15V3'],
  shield: ['M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z'],
  lock: [{ r: [3, 11, 18, 11, 2] }, 'M7 11V7a5 5 0 0 1 10 0v4'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  loader: ['M21 12a9 9 0 1 1-6.219-8.56'],
  power: ['M18.36 6.64a9 9 0 1 1-12.73 0', 'M12 2v10'],
  copy: [{ r: [9, 9, 13, 13, 2] }, 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'],
  box: ['M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z', 'm3.3 7 8.7 5 8.7-5', 'M12 22V12'],
  clock: [{ c: [12, 12, 10] }, 'M12 6v6l4 2'],
  eye: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z', { c: [12, 12, 3] }],
  eyeoff: ['M9.9 4.24A9.1 9.1 0 0 1 12 5c6.5 0 10 7 10 7a13.2 13.2 0 0 1-1.67 2.68', 'M6.61 6.61A13.5 13.5 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.39-1.61', 'M2 2l20 20', 'M14.12 14.12a3 3 0 1 1-4.24-4.24'],
};

export function icon(name, cls = 'h-4 w-4') {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: cls + ' shrink-0' })) svg.setAttribute(k, v);
  for (const s of ICONS[name] || []) {
    let node;
    if (typeof s === 'string') { node = document.createElementNS(NS, 'path'); node.setAttribute('d', s); }
    else if (s.c) { node = document.createElementNS(NS, 'circle'); node.setAttribute('cx', s.c[0]); node.setAttribute('cy', s.c[1]); node.setAttribute('r', s.c[2]); }
    else if (s.e) { node = document.createElementNS(NS, 'ellipse'); ['cx', 'cy', 'rx', 'ry'].forEach((a, i) => node.setAttribute(a, s.e[i])); }
    else { node = document.createElementNS(NS, 'rect'); ['x', 'y', 'width', 'height', 'rx'].forEach((a, i) => node.setAttribute(a, s.r[i])); }
    svg.append(node);
  }
  return svg;
}

// ------------------------------------------------------------------- buttons
export function Button({ label, icon: ic, variant = 'secondary', size = 'md', onClick, disabled, href, title, type = 'button', iconOnly = false, newTab = false, class: extra = '' }) {
  const cls = ['btn', 'btn-' + variant, size === 'sm' ? 'btn-sm' : '', iconOnly ? 'btn-icon' : '', extra].filter(Boolean).join(' ');
  const kids = [ic ? icon(ic, size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4') : null, label];
  if (href) return h('a', { class: cls, href, title, target: newTab ? '_blank' : null, rel: newTab ? 'noopener noreferrer' : null }, kids);
  return h('button', { class: cls, type, disabled, title, 'aria-label': iconOnly ? title : null, onClick }, kids);
}

/** Disable a button and show a spinner while an async action runs. */
export async function busy(btn, fn) {
  const spin = icon('loader', 'h-4 w-4 animate-spin');
  btn.disabled = true;
  btn.prepend(spin);
  try { return await fn(); } finally { spin.remove(); btn.disabled = false; }
}

// ------------------------------------------------------------ badges / status
export function Badge(text, tone = 'neutral', { dot = false, icon: ic } = {}) {
  return h('span', { class: `badge badge-${tone}` }, dot ? h('span', { class: 'dot' }) : null, ic ? icon(ic, 'h-3 w-3') : null, text);
}

export function serviceBadge(s) {
  if (!s.has_service) return Badge('static', 'neutral');
  return Badge(s.service || 'unknown', s.service === 'active' ? 'ok' : 'bad', { dot: true });
}

export function publicBadge(s) {
  if (s.public == null) return Badge('not checked', 'neutral');
  const ok = typeof s.public === 'number' && s.public >= 200 && s.public < 400;
  const bad = s.public === 'err' || s.public >= 500;
  return h('span', { class: 'inline-flex items-center gap-2' },
    Badge(s.public === 'err' ? 'unreachable' : 'HTTP ' + s.public, ok ? 'ok' : bad ? 'bad' : 'warn', { dot: true }),
    h('span', { class: 'text-xs text-subtle' }, (s.public_ms || 0) + ' ms'));
}

export const typeLabel = { static: 'Static', php: 'PHP', dotnet: '.NET', node: 'Node', command: 'Commands', docker: 'Docker', app: 'App' };

/** The logo of a technology, in its brand colour (very dark brands follow the text colour so they stay visible in dark mode). Null when there is none. */
/** A provider's logo (or a plain icon when there is none), its name and an optional status badge: the header of a connection block. */
export function BrandTitle(logo, name, { fallback = 'link', badge = null } = {}) {
  return h('div', { class: 'flex items-center gap-2.5' },
    h('span', { class: 'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sunken' }, Logo(logo, 'h-5 w-5') || icon(fallback, 'h-4 w-4')),
    h('span', { class: 'font-medium' }, name), badge);
}

export function Logo(id, cls = 'h-5 w-5') {
  const l = LOGOS[id];
  if (!l) return null;
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  const r = parseInt(l.c.slice(0, 2), 16), g = parseInt(l.c.slice(2, 4), 16), b = parseInt(l.c.slice(4, 6), 16);
  const dark = (0.299 * r + 0.587 * g + 0.114 * b) < 70;
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', cls + ' shrink-0');
  svg.setAttribute('fill', dark ? 'currentColor' : '#' + l.c);
  const p = document.createElementNS(NS, 'path'); p.setAttribute('d', l.p); svg.append(p);
  return svg;
}

/** Which logo goes with a service, container or app name (nginx, php8.3-fpm, redis-server, ...). */
export function logoId(name) {
  const n = String(name || '').toLowerCase();
  const map = [[/^php/, 'php'], [/postgres/, 'postgres'], [/maria/, 'mariadb'], [/mysql/, 'mysql'], [/redis/, 'redis'], [/mssql|sqlserver/, 'mssql'], [/docker/, 'docker'], [/caddy/, 'caddy'], [/cloudflare/, 'cloudflare'],
    [/nginx/, 'nginx'], [/portainer/, 'portainer'], [/node|^npm/, 'node'], [/python/, 'python'], [/dotnet|aspnet/, 'dotnet'], [/wordpress/, 'wordpress'], [/laravel/, 'laravel'], [/composer/, 'composer'],
    [/github/, 'github'], [/gitlab/, 'gitlab'], [/^git$/, 'git'], [/ubuntu/, 'ubuntu']];
  const hit = map.find(([re]) => re.test(n));
  return hit && LOGOS[hit[1]] ? hit[1] : '';
}

// What a site is built with. The framework (Angular, Laravel, ...) is shown when it is known; a framework without its own logo shows its language's logo.
export const frameworkLabel = { angular: 'Angular', react: 'React', vue: 'Vue', next: 'Next.js', nuxt: 'Nuxt', svelte: 'Svelte', astro: 'Astro', vite: 'Vite', gatsby: 'Gatsby', remix: 'Remix', express: 'Express',
  nest: 'NestJS', django: 'Django', flask: 'Flask', fastapi: 'FastAPI', symfony: 'Symfony', codeigniter: 'CodeIgniter', drupal: 'Drupal', joomla: 'Joomla', prestashop: 'PrestaShop', strapi: 'Strapi',
  ghost: 'Ghost', preact: 'Preact', electron: 'Electron', laravel: 'Laravel', wordpress: 'WordPress' };
const FW_LANGUAGE = { angular: 'node', react: 'node', vue: 'node', next: 'node', nuxt: 'node', svelte: 'node', astro: 'node', vite: 'node', gatsby: 'node', remix: 'node', express: 'node', nest: 'node', strapi: 'node',
  ghost: 'node', preact: 'node', electron: 'node', laravel: 'php', symfony: 'php', codeigniter: 'php', drupal: 'php', joomla: 'php', prestashop: 'php', wordpress: 'php', django: 'python', flask: 'python', fastapi: 'python' };
export const typeBadge = (t, framework) => {
  const lang = t === 'app' ? 'node' : t;
  const logo = (framework && (Logo(framework, 'h-3.5 w-3.5') || Logo(FW_LANGUAGE[framework] || lang, 'h-3.5 w-3.5'))) || Logo(lang, 'h-3.5 w-3.5');
  return h('span', { class: 'badge badge-neutral', title: framework ? `${frameworkLabel[framework] || framework} (${typeLabel[t] || t} site)` : undefined }, logo, framework ? (frameworkLabel[framework] || framework) : (typeLabel[t] || t));
};

// ------------------------------------------------------------------- surfaces
export function Card({ title, subtitle, actions, class: extra = '', bodyClass = 'card-body' } = {}, ...children) {
  const head = (title || actions) ? h('div', { class: 'card-header' },
    h('div', {}, title ? h('h2', { class: 'text-sm' }, title) : null, subtitle ? h('p', { class: 'mt-0.5 text-xs text-muted' }, subtitle) : null),
    actions ? h('div', { class: 'flex shrink-0 items-center gap-2' }, actions) : null) : null;
  return h('section', { class: 'card ' + extra }, head, h('div', { class: bodyClass }, children));
}

export function Alert({ tone = 'info', title }, ...children) {
  const ic = { ok: 'check', warn: 'alert', bad: 'alert', info: 'info' }[tone];
  return h('div', { class: `alert alert-${tone}`, role: tone === 'bad' ? 'alert' : 'status' },
    icon(ic, 'mt-0.5 h-4 w-4'),
    h('div', { class: 'min-w-0' }, title ? h('p', { class: 'font-medium' }, title) : null, children.length ? h('div', { class: title ? 'mt-0.5 opacity-90' : '' }, children) : null));
}

export function EmptyState({ icon: ic = 'box', title, body, action }) {
  return h('div', { class: 'flex flex-col items-center px-6 py-14 text-center' },
    h('div', { class: 'mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-sunken text-muted' }, icon(ic, 'h-6 w-6')),
    h('h3', { class: 'text-base' }, title),
    body ? h('p', { class: 'mt-1.5 max-w-md text-sm text-muted' }, body) : null,
    action ? h('div', { class: 'mt-5' }, action) : null);
}

export function PageHeader({ title, description, actions, crumbs }) {
  return h('header', { class: 'flex flex-wrap items-start justify-between gap-4' },
    h('div', { class: 'min-w-0' },
      crumbs ? h('nav', { class: 'mb-1 flex items-center gap-1.5 text-xs text-muted', 'aria-label': 'Breadcrumb' },
        crumbs.map((c, i) => [i ? icon('chevron', 'h-3 w-3') : null, c.href ? h('a', { href: c.href, class: 'hover:text-ink' }, c.label) : h('span', { class: 'text-ink' }, c.label)])) : null,
      h('h1', { class: 'truncate text-xl' }, title),
      description ? h('p', { class: 'mt-1 text-sm text-muted' }, description) : null),
    actions ? h('div', { class: 'flex flex-wrap items-center gap-2' }, actions) : null);
}

/** A figure card. With `href` (or `onClick`) the whole card is a link/button to the details behind the number. */
export function Stat({ label, value, hint, icon: ic, meter, href, onClick }) {
  const kids = [
    h('div', { class: 'flex items-center justify-between text-muted' }, h('span', { class: 'text-xs font-medium uppercase tracking-wide' }, label),
      h('span', { class: 'flex items-center gap-1' }, ic ? icon(ic, 'h-4 w-4') : null, href || onClick ? icon('chevron', 'h-3.5 w-3.5 text-subtle') : null)),
    h('p', { class: 'mt-2 text-2xl font-semibold tracking-tight' }, value),
    meter ? Meter(meter) : null,
    hint ? h('p', { class: 'mt-1 text-xs text-muted' }, hint) : null];
  const live = 'card block w-full p-4 text-left transition hover:border-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand';
  if (href) return h('a', { class: live, href, 'aria-label': `${label}: ${value}. Open details` }, kids);
  if (onClick) return h('button', { type: 'button', class: live, onClick, 'aria-label': `${label}: ${value}. Show details` }, kids);
  return h('div', { class: 'card p-4' }, kids);
}

export function Meter({ value, max, warnAt = 80, badAt = 92 }) {
  const p = max ? Math.min(100, Math.round((value / max) * 100)) : 0;
  const tone = p >= badAt ? 'bg-bad' : p >= warnAt ? 'bg-warn' : 'bg-brand';
  return h('div', { class: 'meter mt-3', role: 'meter', 'aria-valuenow': p, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('span', { class: tone, style: `width:${p}%` }));
}

export function Skeleton(lines = 3) {
  return h('div', { class: 'space-y-3' }, Array.from({ length: lines }, (_, i) => h('div', { class: 'skeleton h-5', style: `width:${90 - i * 12}%` })));
}

export function KeyValue(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).map(([k, v]) => [h('dt', {}, k), h('dd', {}, v == null || v === '' ? h('span', { class: 'text-subtle' }, 'none') : v)]));
}

// --------------------------------------------------------------------- tables
export function Table({ columns, rows, onRowClick, empty }) {
  if (!rows.length) return empty || EmptyState({ title: 'Nothing here yet' });
  return h('div', { class: 'overflow-x-auto' }, h('table', { class: 'table' },
    h('thead', {}, h('tr', {}, columns.map((c) => h('th', { class: c.class || '' }, c.label)))),
    h('tbody', {}, rows.map((r) => h('tr', onRowClick ? { 'data-click': '', tabindex: 0, onClick: () => onRowClick(r), onKeydown: (e) => { if (e.key === 'Enter') onRowClick(r); } } : {},
      columns.map((c) => h('td', { class: c.class || '' }, c.render ? c.render(r) : r[c.key])))))));
}

// ---------------------------------------------------------------- form fields
let uid = 0;
export const nextId = (p = 'f') => `${p}-${++uid}`;

export function Field({ label, hint, error, id }, control) {
  return h('div', {}, label ? h('label', { class: 'label', for: id }, label) : null, control,
    error ? h('p', { class: 'field-error' }, error) : null, hint ? h('p', { class: 'hint' }, hint) : null);
}

export function Input({ value = '', placeholder, onInput, id, type = 'text', mono = false, disabled = false, name, autocomplete = 'off', min }) {
  return h('input', { class: 'input' + (mono ? ' font-mono text-xs' : ''), value, placeholder, onInput, id, type, disabled, name, autocomplete, min, spellcheck: 'false' });
}

export function Textarea({ value = '', placeholder, onInput, id, rows = 4 }) {
  return h('textarea', { class: 'input textarea', value, placeholder, onInput, id, rows, spellcheck: 'false' });
}

export function Select({ options, value, onChange, id }) {
  const el = h('select', { class: 'input select', id, onChange: (e) => onChange && onChange(e.target.value) },
    options.map((o) => h('option', { value: o.value, selected: o.value === value }, o.label)));
  return el;
}

export function Switch({ checked = false, onChange, label, description, id = nextId('sw') }) {
  const btn = h('button', { type: 'button', role: 'switch', class: 'switch', id, 'aria-checked': String(checked) });
  const wrap = h('div', { class: 'flex items-start justify-between gap-4' },
    h('label', { for: id, class: 'min-w-0 cursor-pointer' }, h('span', { class: 'block text-sm font-medium' }, label), description ? h('span', { class: 'mt-0.5 block text-xs text-muted' }, description) : null),
    btn);
  wrap.get = () => btn.getAttribute('aria-checked') === 'true';
  wrap.set = (v) => btn.setAttribute('aria-checked', String(!!v));
  btn.addEventListener('click', () => { wrap.set(!wrap.get()); if (onChange) onChange(wrap.get()); });
  return wrap;
}

export function Segmented({ options, value, onChange, label = 'Options' }) {
  const wrap = h('div', { class: 'segmented', role: 'group', 'aria-label': label });
  let cur = value;
  const paint = () => wrap.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === cur)));
  options.forEach((o) => wrap.append(h('button', { type: 'button', 'data-v': o.value, onClick: () => { cur = o.value; paint(); if (onChange) onChange(cur); } }, o.label)));
  wrap.get = () => cur;
  wrap.set = (v) => { cur = v; paint(); };
  paint();
  return wrap;
}

/** Tab bar; on phone widths a full-width dropdown instead (a row of tabs there scrolls sideways and hides most of them). t.text overrides a node label there. */
export function Tabs({ tabs, active, onChange }) {
  if (window.matchMedia && window.matchMedia('(max-width: 639px)').matches) {
    const text = (t) => (t.text || (typeof t.label === 'string' ? t.label : t.label.textContent)) + (t.count != null ? ` (${t.count})` : '');
    return h('div', { class: 'mb-2', role: 'tablist' }, Select({ options: tabs.map((t) => ({ value: t.id, label: text(t) })), value: active, onChange }));
  }
  const wrap = h('div', { class: 'tabs overflow-x-auto whitespace-nowrap', style: 'overflow-y:hidden', role: 'tablist' });
  tabs.forEach((t) => wrap.append(h('button', { type: 'button', role: 'tab', class: 'tab', 'aria-selected': String(t.id === active), onClick: () => onChange(t.id) }, t.label, t.count != null ? h('span', { class: 'ml-1.5 text-xs text-subtle' }, t.count) : null)));
  return wrap;
}

/** Add/remove KEY=VALUE rows. get() returns {KEY: value} for the non-empty rows. */
export function KeyValueEditor({ value = {}, keyPlaceholder = 'NAME', valuePlaceholder = 'value', secret = false } = {}) {
  const list = h('div', { class: 'space-y-2' });
  const addRow = (k = '', v = '') => {
    const row = h('div', { class: 'flex items-center gap-2' },
      Input({ value: k, placeholder: keyPlaceholder, mono: true }),
      Input({ value: v, placeholder: valuePlaceholder, mono: true, type: secret ? 'password' : 'text' }),
      Button({ icon: 'x', iconOnly: true, variant: 'ghost', title: 'Remove', onClick: () => row.remove() }));
    list.append(row);
  };
  Object.entries(value).forEach(([k, v]) => addRow(k, v));
  const paste = h('textarea', { class: 'input textarea font-mono text-xs', rows: 6, placeholder: 'KEY=value\n# comments and "quotes" are fine', spellcheck: 'false' });
  const pasteBox = h('div', { class: 'mt-2 hidden space-y-2' }, paste, Button({ label: 'Add these variables', size: 'sm', variant: 'primary', onClick: () => {
    let n = 0;
    for (const line of paste.value.split(/\r?\n/)) {
      const m = line.trim().replace(/^export\s+/, '').match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
      if (!m) continue;
      let v = m[2].trim();
      if (/^(".*"|'.*')$/.test(v)) v = v.slice(1, -1); else v = v.replace(/\s+#.*$/, '');
      const row = [...list.children].find((r) => r.children[0].value.trim() === m[1]);      // an existing row of the same name is updated
      if (row) row.children[1].value = v; else addRow(m[1], v);
      n++;
    }
    paste.value = ''; pasteBox.classList.add('hidden');
    toast(n ? `${n} variable${n === 1 ? '' : 's'} added below` : 'No KEY=value lines found', { tone: n ? 'ok' : 'warn' });
  } }));
  const el = h('div', {}, list, h('div', { class: 'mt-2 flex gap-2' }, Button({ label: 'Add variable', icon: 'plus', size: 'sm', variant: 'ghost', onClick: () => addRow() }),
    Button({ label: 'Paste .env', icon: 'file', size: 'sm', variant: 'ghost', onClick: () => pasteBox.classList.toggle('hidden') })), pasteBox);
  el.get = () => Object.fromEntries([...list.children].map((r) => [r.children[0].value.trim(), r.children[1].value]).filter(([k]) => k));
  return el;
}

// -------------------------------------------------------------------- console
export function Console({ lines = [], empty = 'Nothing to show yet.' } = {}) {
  const el = h('pre', { class: 'console', tabindex: 0 }, lines.join('\n') || empty);
  const api = {
    el,
    set(text) { el.textContent = Array.isArray(text) ? text.join('\n') : text; el.scrollTop = el.scrollHeight; },
    push(more) { el.textContent += (el.textContent && el.textContent !== empty ? '\n' : '') + more.join('\n'); el.scrollTop = el.scrollHeight; },
  };
  return api;
}

// ------------------------------------------------------------- overlays/toasts
function toastHost() {
  let host = document.getElementById('toasts');
  if (!host) { host = h('div', { id: 'toasts', class: 'pointer-events-none fixed bottom-4 right-4 z-[60] flex flex-col gap-2', 'aria-live': 'polite' }); document.body.append(host); }
  return host;
}

export function toast(message, { tone = 'info', title, timeout = 6000 } = {}) {
  const ic = { ok: 'check', warn: 'alert', bad: 'alert', info: 'info' }[tone];
  const color = { ok: 'text-ok', warn: 'text-warn', bad: 'text-bad', info: 'text-info' }[tone];
  const el = h('div', { class: 'toast', role: tone === 'bad' ? 'alert' : 'status' },
    h('span', { class: color }, icon(ic, 'mt-0.5 h-4 w-4')),
    h('div', { class: 'min-w-0 flex-1' }, title ? h('p', { class: 'font-medium' }, title) : null, h('p', { class: 'whitespace-pre-line break-words text-muted' }, message)),
    h('button', { class: 'text-subtle hover:text-ink', 'aria-label': 'Dismiss', onClick: () => el.remove() }, icon('x', 'h-4 w-4')));
  toastHost().append(el);
  if (timeout) setTimeout(() => el.remove(), timeout);
  return el;
}

export function openModal({ title, description, body, actions = [], size = 'md', onClose }) {
  const dlg = h('dialog', { class: 'modal' + (size === 'lg' ? ' !max-w-3xl' : ''), 'aria-labelledby': 'modal-title' },
    h('div', { class: 'flex items-start justify-between gap-4 px-6 pt-5' },
      h('div', {}, h('h2', { id: 'modal-title', class: 'text-base' }, title), description ? h('p', { class: 'mt-1 text-sm text-muted' }, description) : null),
      h('button', { class: 'rounded-md p-1 text-subtle hover:bg-sunken hover:text-ink', 'aria-label': 'Close', onClick: () => dlg.close() }, icon('x', 'h-4 w-4'))),
    body ? h('div', { class: 'px-6 py-4' }, body) : null,
    actions.length ? h('div', { class: 'flex justify-end gap-2 border-t border-line px-6 py-4' }, actions) : h('div', { class: 'pb-3' }));
  dlg.addEventListener('close', () => { dlg.remove(); if (onClose) onClose(); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  document.body.append(dlg);
  dlg.showModal();
  return { el: dlg, close: () => dlg.close() };
}

/**
 * Confirmation modal. Resolves {confirmed, values}. `requireText` makes the user type a word to enable the button;
 * `switches` adds extra options: [{id, label, description, default}].
 */
export function confirmDialog({ title, message, confirmLabel = 'Confirm', tone = 'danger', requireText, switches = [] }) {
  return new Promise((resolve) => {
    let done = false;
    const sw = switches.map((s) => ({ id: s.id, el: Switch({ label: s.label, description: s.description, checked: !!s.default }) }));
    const typed = requireText ? Input({ placeholder: requireText, mono: true, onInput: () => { ok.disabled = typed.value !== requireText; } }) : null;
    const ok = Button({ label: confirmLabel, variant: tone === 'danger' ? 'danger' : 'primary', disabled: !!requireText, onClick: () => { done = true; resolve({ confirmed: true, values: Object.fromEntries(sw.map((s) => [s.id, s.el.get()])) }); m.close(); } });
    const m = openModal({
      title,
      description: message,
      body: h('div', { class: 'space-y-4' },
        requireText ? h('div', {}, h('label', { class: 'label' }, ['Type ', h('span', { class: 'font-mono text-bad' }, requireText), ' to confirm']), typed) : null,
        sw.map((s) => s.el)),
      actions: [Button({ label: 'Cancel', onClick: () => m.close() }), ok],
      onClose: () => { if (!done) resolve({ confirmed: false, values: {} }); },
    });
    if (typed) typed.focus();
  });
}

/** Show text (command output, config) in a modal with a copy button. */
export function showText({ title, text, description }) {
  const c = Console({ lines: [text || '(empty)'] });
  openModal({ title, description, size: 'lg', body: c.el, actions: [Button({ label: 'Copy', icon: 'copy', onClick: () => { navigator.clipboard && navigator.clipboard.writeText(text || ''); toast('Copied', { tone: 'ok', timeout: 1500 }); } })] });
}

export { duration };
