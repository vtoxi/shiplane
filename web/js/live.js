// Live pieces: SVG charts, the streaming log viewer and the deploy step tracker. Built on kit.js, no libraries.
import { h, Button, Badge, Input, icon } from './kit.js';

const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

let tipEl = null;
function tipBox() {
  if (!tipEl) { tipEl = h('div', { class: 'pointer-events-none fixed z-[70] hidden max-w-xs rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs shadow-lg', role: 'tooltip' }); document.body.append(tipEl); }
  return tipEl;
}
const when = (t) => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: t % 60 ? '2-digit' : undefined });

/** One shared tooltip for every chart: lines of text next to the pointer. hide() when the pointer leaves. */
export const tooltip = {
  show(x, y, lines) {
    const t = tipBox();
    t.replaceChildren(...lines.map((l, i) => h('div', { class: i ? 'text-ink' : 'text-muted' }, l)));
    t.classList.remove('hidden');
    const w = t.offsetWidth, hgt = t.offsetHeight;
    t.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x + 14)) + 'px';
    t.style.top = (y - hgt - 12 < 8 ? y + 18 : y - hgt - 12) + 'px';
  },
  hide() { if (tipEl) tipEl.classList.add('hidden'); },
};

/** Area chart of numbers (oldest first). `max` fixes the top of the scale (e.g. 100 for percent); null points leave a gap marker.
 *  With `times` (epoch seconds) and/or `fmt(value, index)` the chart shows a guide line and a tooltip under the pointer (touch works too).
 *  `marks` (default) draws a red bar for a missing value (a failed uptime check); with marks=false missing readings are simply skipped. */
export function Spark({ values, max, tone = 'text-brand', height = 56, times, fmt, marks = true }) {
  const w = 200, pad = 2, top = max || Math.max(1, ...values.filter((v) => v != null)) * 1.15;
  const wrap = svg('svg', { viewBox: `0 0 ${w} ${height}`, preserveAspectRatio: 'none', class: `w-full ${tone}`, style: `height:${height}px`, role: 'img' });
  if (values.length < 2) return wrap;
  const x = (i) => (i / (values.length - 1)) * w;
  const y = (v) => height - pad - (Math.min(v, top) / top) * (height - pad * 2);
  const pts = values.map((v, i) => [x(i), v == null ? height - pad : y(v)]).filter((p, i) => marks || values[i] != null);
  if (pts.length < 2) return wrap;
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  wrap.append(svg('path', { d: `${line} L${w} ${height} L0 ${height} Z`, fill: 'currentColor', opacity: '0.12' }),
    svg('path', { d: line, fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round' }));
  if (marks) values.forEach((v, i) => { if (v == null) wrap.append(svg('line', { x1: x(i), x2: x(i), y1: 0, y2: height, stroke: 'rgb(220 38 38)', 'stroke-width': '1.5', 'vector-effect': 'non-scaling-stroke' })); });
  if (times || fmt) {
    const guide = svg('line', { y1: 0, y2: height, stroke: 'currentColor', 'stroke-width': '1', opacity: '0', 'vector-effect': 'non-scaling-stroke' });
    wrap.append(guide);
    wrap.style.cursor = 'crosshair';
    wrap.style.touchAction = 'pan-y';
    const move = (e) => {
      const r = wrap.getBoundingClientRect();
      const i = Math.round(Math.min(1, Math.max(0, (e.clientX - r.left) / (r.width || 1))) * (values.length - 1));
      guide.setAttribute('x1', x(i)); guide.setAttribute('x2', x(i)); guide.setAttribute('opacity', '0.45');
      tooltip.show(e.clientX, e.clientY, [times && times[i] ? when(times[i]) : `Point ${i + 1} of ${values.length}`, fmt ? fmt(values[i], i) : values[i] == null ? 'no data' : String(values[i])]);
    };
    const leave = () => { guide.setAttribute('opacity', '0'); tooltip.hide(); };
    wrap.addEventListener('pointermove', move);
    wrap.addEventListener('pointerdown', move);
    wrap.addEventListener('pointerleave', leave);
    wrap.addEventListener('pointercancel', leave);
  }
  return wrap;
}

/** A card with a title, the current value and a Spark under it (pass times/fmt for the tooltip). */
export function MetricChart({ label, value, hint, values, max, tone, times, fmt, marks }) {
  return h('div', { class: 'card overflow-hidden' },
    h('div', { class: 'flex items-baseline justify-between px-4 pt-4' },
      h('span', { class: 'text-xs font-medium uppercase tracking-wide text-muted' }, label), h('span', { class: 'text-xs text-muted' }, hint || '')),
    h('p', { class: 'px-4 pb-2 text-2xl font-semibold tracking-tight' }, value),
    Spark({ values, max, tone, times, fmt, marks }));
}

/** Streams a log with Server-Sent Events. Reconnects by itself (the server ends a stream after 30 minutes). */
export function LogViewer({ kind, name, height = 'h-96' }) {
  const MAX = 2000;
  let lines = [], paused = false, pending = [], filter = '', es = null, closed = false;
  const pre = h('pre', { class: `console ${height} max-h-none`, tabindex: 0 });
  const status = h('span', {});
  const setStatus = (t, tone) => status.replaceChildren(Badge(t, tone, { dot: true }));
  const draw = () => {
    const f = filter.toLowerCase();
    const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
    pre.textContent = (f ? lines.filter((l) => l.toLowerCase().includes(f)) : lines).join('\n') || 'Waiting for log lines...';
    if (stick) pre.scrollTop = pre.scrollHeight;
  };
  const add = (l) => { lines.push(l); if (lines.length > MAX) lines = lines.slice(-MAX); };
  function connect() {
    if (closed) return;
    setStatus('Connecting', 'neutral');
    es = new EventSource(`/api/logs/stream?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`);
    es.onopen = () => setStatus(paused ? 'Paused' : 'Live', paused ? 'warn' : 'ok');
    es.onmessage = (e) => { const l = JSON.parse(e.data); if (paused) pending.push(l); else { add(l); schedule(); } };
    es.addEventListener('end', () => { es.close(); setStatus('Ended', 'neutral'); });
    es.onerror = () => { es.close(); setStatus('Reconnecting', 'warn'); setTimeout(connect, 3000); };
  }
  let t = 0;
  const schedule = () => { if (!t) t = setTimeout(() => { t = 0; draw(); }, 120); };   // batch bursts into one repaint
  const pause = Button({ label: 'Pause', icon: 'stop', size: 'sm', onClick: () => {
    paused = !paused;
    pause.lastChild.textContent = paused ? 'Resume' : 'Pause';
    if (!paused) { pending.forEach(add); pending = []; draw(); }
    setStatus(paused ? 'Paused' : 'Live', paused ? 'warn' : 'ok');
  } });
  const search = Input({ placeholder: 'Filter lines...', onInput: (e) => { filter = e.target.value; draw(); } });
  const el = h('div', { class: 'space-y-3' },
    h('div', { class: 'flex flex-wrap items-center gap-2' }, status, h('div', { class: 'ml-auto flex flex-wrap items-center gap-2' }, h('div', { class: 'w-44' }, search), pause,
      Button({ label: 'Clear', icon: 'trash', size: 'sm', variant: 'ghost', onClick: () => { lines = []; draw(); } }),
      Button({ label: 'Download', icon: 'download', size: 'sm', variant: 'ghost', onClick: () => {
        const a = h('a', { href: URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain' })), download: `${name}.log` }); a.click(); URL.revokeObjectURL(a.href);
      } }))), pre);
  draw();
  connect();
  return { el, destroy() { closed = true; if (es) es.close(); } };
}

const STEPS = [
  ['Pack and upload', /^(Packing|Uploading)/],
  ['Unpack release', /^unpacking release|installing node/],
  ['Database', /creating postgres database/],
  ['Switch and reload', /^RESULT /],
  ['Cloudflare', /^Mapping in Cloudflare/],
  ['Done', /^DONE|^FAILED/],
];

/** Step list that lights up as deploy log lines arrive: call `feed(lines)` and `finish(ok)`. */
export function DeploySteps() {
  let reached = -1, failed = false;
  const list = h('ol', { class: 'space-y-2 text-sm' });
  const draw = (ok) => list.replaceChildren(...STEPS.map(([label], i) => {
    const done = i < reached || (ok === true && i <= reached), active = i === reached && ok == null && !failed;
    const bad = failed && i === reached;
    return h('li', { class: 'flex items-center gap-2.5 ' + (done || active || bad ? 'text-ink' : 'text-subtle') },
      bad ? icon('x', 'h-4 w-4 text-bad') : done ? icon('check', 'h-4 w-4 text-ok') : active ? icon('loader', 'h-4 w-4 animate-spin text-brand') : h('span', { class: 'h-4 w-4 rounded-full border border-line' }), label);
  }));
  draw();
  return { el: list,
    feed(lines) { for (const l of lines) STEPS.forEach(([, re], i) => { if (re.test(l.trim()) && i > reached) reached = i; }); draw(); },
    finish(ok) { failed = !ok; if (ok) reached = STEPS.length - 1; draw(ok); } };
}
