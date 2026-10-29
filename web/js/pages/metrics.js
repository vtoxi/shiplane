// Metrics: live charts while the page is open, and the last 1 / 6 / 24 hours remembered by the server. Hover (or touch) any chart for exact values.
import { h, fill, Alert, EmptyState, PageHeader, Tabs, Skeleton } from '../kit.js';
import { MetricChart } from '../live.js';
import { store } from '../store.js';
import { api } from '../api.js';
import { bytes } from '../format.js';

const TABS = [{ id: 'live', label: 'Live' }, { id: '1h', label: '1 hour' }, { id: '6h', label: '6 hours' }, { id: '24h', label: '24 hours' }];
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
// idx = column in a history point: [time, cpu, memory, disk, load, bytes in/s, bytes out/s]
const CHARTS = [
  { label: 'CPU', idx: 1, max: 100, tone: 'text-brand', show: (v) => `${v.toFixed(0)}%` },
  { label: 'Memory', idx: 2, max: 100, tone: 'text-warn', show: (v) => `${v.toFixed(0)}%` },
  { label: 'Disk', idx: 3, max: 100, tone: 'text-info', show: (v) => `${v.toFixed(1)}%` },
  { label: 'Load', idx: 4, tone: 'text-brand', show: (v) => v.toFixed(2) },
  { label: 'Network in', idx: 5, tone: 'text-ok', show: (v) => `${bytes(v)}/s` },
  { label: 'Network out', idx: 6, tone: 'text-info', show: (v) => `${bytes(v)}/s` },
];

function summary(values, show) {
  const real = values.filter((v) => v != null);
  if (!real.length) return { now: '...', hint: 'no data yet' };
  const avg = real.reduce((a, b) => a + b, 0) / real.length;
  return { now: show(real[real.length - 1]), hint: `avg ${show(avg)}, peak ${show(Math.max(...real))}` };
}

function card(def, times, values) {
  const s = summary(values, def.show);
  const el = MetricChart({ label: def.label, value: s.now, hint: s.hint, values, max: def.max, tone: def.tone, times, marks: false, fmt: (v) => (v == null ? `${def.label}: no reading` : `${def.label} ${def.show(v)}`) });
  if (times.length > 1) el.append(h('div', { class: 'flex justify-between px-4 pb-3 pt-1 text-xs text-subtle' }, h('span', {}, clock(times[0])), h('span', {}, clock(times[times.length - 1]))));
  return el;
}

export function MetricsPage({ query = {} } = {}) {
  let tab = TABS.some((t) => t.id === query.range) ? query.range : 'live';
  let timer = null;
  let live = null;
  const grid = h('div', { class: 'grid gap-4 sm:grid-cols-2 xl:grid-cols-3' });
  const note = h('p', { class: 'text-sm text-muted' });
  const body = h('div', { class: 'space-y-4' });
  const every = Math.max(2, (store.session.perf && store.session.perf.live_poll) || 5);

  function stop() { clearInterval(timer); timer = null; }

  // ------------------------------------------------------------- live: one sample every few seconds while this page is open
  async function startLive() {
    live = { prev: null, rows: [] };
    note.textContent = `Sampled every ${every} seconds while this page is open (last ${Math.round((every * 60) / 60)} minutes). Nothing is recorded when you leave.`;
    const sample = async () => {
      if (document.hidden) return;
      let m;
      try { m = await api('/api/metrics'); } catch (e) { return; }
      const p = live.prev;
      const dt = p ? Math.max(1, m.t - p.t) : 0;
      const dtot = p ? m.cpu_total - p.cpu_total : 0;
      const idle = p ? m.cpu_idle - p.cpu_idle : 0;
      live.rows.push([m.t, p && dtot > 0 && idle >= 0 ? Math.max(0, 100 * (1 - idle / dtot)) : null, (100 * m.ram_used) / m.ram_total, (100 * m.disk_used) / m.disk_total, m.load,
        p && m.net_rx >= p.net_rx ? (m.net_rx - p.net_rx) / dt : null, p && m.net_tx >= p.net_tx ? (m.net_tx - p.net_tx) / dt : null]);
      if (live.rows.length > 60) live.rows.shift();
      live.prev = m;
      draw(live.rows);
    };
    await sample();
    timer = setInterval(sample, every * 1000);
  }

  // ------------------------------------------------------------------ history: what the uptime round remembered
  async function loadHistory() {
    fill(body, Skeleton(4));
    try {
      const r = await api(`/api/metrics/history?range=${tab}`);
      note.textContent = `One point every ${Math.round(r.interval / 60)} minute${r.interval === 60 ? '' : 's'}, recorded by the uptime check (Settings, Uptime checks). Kept for 24 hours.`;
      fill(body, note, grid);
      if (r.points.length < 2) {
        fill(grid);
        fill(body, note, EmptyState({ icon: 'activity', title: 'Collecting data', body: 'The first points appear after two uptime checks. Use the Live tab to see the server right now.' }));
        return;
      }
      draw(r.points);
    } catch (e) { fill(body, Alert({ tone: 'bad', title: 'Could not read the history' }, e.message)); }
  }

  function draw(rows) {
    if (!grid.isConnected) fill(body, note, grid);
    const times = rows.map((r) => r[0]);
    fill(grid, ...CHARTS.map((def) => card(def, times, rows.map((r) => r[def.idx]))));
  }

  function select(id) {
    tab = id;
    stop();
    history.replaceState(null, '', `#/metrics?range=${id}`);
    fill(tabs, Tabs({ tabs: TABS, active: tab, onChange: select }));
    fill(body, Skeleton(4));
    return tab === 'live' ? startLive().then(() => { if (!grid.isConnected) fill(body, note, grid); }) : loadHistory();
  }

  const tabs = h('div', {});
  fill(tabs, Tabs({ tabs: TABS, active: tab, onChange: select }));
  select(tab);
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Metrics', description: 'How busy the server is. Hover or touch a chart for exact values.' }), tabs, body), destroy: stop };
}
