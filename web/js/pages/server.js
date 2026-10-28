import { fill, h, Button, Card, Stat, Alert, PageHeader, Console, icon, confirmDialog, showText, toast, Skeleton, openModal } from '../kit.js';
import { MetricChart, LogViewer } from '../live.js';
import { ServicesCard, ContainersCard } from '../servicesview.js';
import { store, subscribe, refresh } from '../store.js';
import { bytes, duration, pct } from '../format.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';

export function ServerPage() {
  let info = null;
  let infoError = null;
  const top = h('div', { class: 'space-y-6' });
  const detail = h('div', { class: 'space-y-6' });

  async function loadInfo() {
    info = null; infoError = null; drawDetail();
    try { info = await api('/api/server/info'); } catch (e) { infoError = e.message; }
    drawDetail();
  }

  // live resource charts: one sample every 5 s, the last 5 minutes kept
  const live = h('div', { class: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-4' }, [0, 1, 2, 3].map(() => h('div', { class: 'card p-4' }, Skeleton(2))));
  const buf = { t: [], cpu: [], ram: [], rx: [], tx: [] };
  const every = Math.max(2, (store.session.perf && store.session.perf.live_poll) || 5);       // seconds between samples: the profile decides
  let prev = null;
  const push = (k, v) => { buf[k].push(v); if (buf[k].length > 60) buf[k].shift(); };
  const span = () => { const m = Math.round((every * 60) / 60); return m >= 2 ? `last ${m} min` : `last ${every * 60} s`; };
  async function sample() {
    if (document.hidden) return;
    let m;
    try { m = await api('/api/metrics'); } catch (e) { return; }
    if (prev) {
      const dt = Math.max(1, m.t - prev.t), dtot = m.cpu_total - prev.cpu_total;
      push('cpu', dtot > 0 ? Math.max(0, 100 * (1 - (m.cpu_idle - prev.cpu_idle) / dtot)) : 0);
      push('rx', Math.max(0, (m.net_rx - prev.net_rx) / dt));
      push('tx', Math.max(0, (m.net_tx - prev.net_tx) / dt));
    }
    push('ram', (100 * m.ram_used) / m.ram_total);
    push('t', m.t);
    prev = m;
    const last = (k) => buf[k][buf[k].length - 1];
    live.replaceChildren(
      MetricChart({ label: 'CPU', value: buf.cpu.length ? `${last('cpu').toFixed(0)}%` : '...', hint: `load ${m.load.toFixed(2)}`, values: buf.cpu, max: 100, times: buf.t.slice(-buf.cpu.length), fmt: (v) => `CPU ${v.toFixed(0)}%` }),
      MetricChart({ label: 'Memory', value: `${last('ram').toFixed(0)}%`, hint: `${bytes(m.ram_used)} of ${bytes(m.ram_total)}`, values: buf.ram, max: 100, tone: 'text-warn', times: buf.t.slice(-buf.ram.length), fmt: (v) => `Memory ${v.toFixed(0)}%` }),
      MetricChart({ label: 'Network in', value: buf.rx.length ? `${bytes(last('rx'))}/s` : '...', hint: span(), values: buf.rx, tone: 'text-ok', times: buf.t.slice(-buf.rx.length), fmt: (v) => `Incoming ${bytes(v)}/s` }),
      MetricChart({ label: 'Network out', value: buf.tx.length ? `${bytes(last('tx'))}/s` : '...', hint: span(), values: buf.tx, tone: 'text-info', times: buf.t.slice(-buf.tx.length), fmt: (v) => `Outgoing ${bytes(v)}/s` }));
  }
  const timer = setInterval(sample, every * 1000);
  sample();
  const showLive = (kind, name) => { const v = LogViewer({ kind, name }); openModal({ title: `${name} logs`, description: 'Live: new lines appear as they are written.', body: v.el, size: 'lg', onClose: v.destroy }); };

  const showCmd = async (promise) => { const r = await attempt(promise); if (r) showText({ title: 'Output', text: r.text }); return r; };

  function drawTop() {
    const st = store.status;
    if (!st) { top.replaceChildren(Card({}, Skeleton(4))); return; }
    const v = st.server;
    top.replaceChildren(
      h('div', { class: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-4' },
        Stat({ label: 'Memory', value: bytes(v.ram_total - v.ram_avail), hint: `of ${bytes(v.ram_total)} (${pct(v.ram_total - v.ram_avail, v.ram_total)}%)`, icon: 'cpu', meter: { value: v.ram_total - v.ram_avail, max: v.ram_total }, href: '#/care?open=memory' }),
        Stat({ label: 'Disk', value: bytes(v.disk_total - v.disk_free), hint: `of ${bytes(v.disk_total)}`, icon: 'disk', meter: { value: v.disk_total - v.disk_free, max: v.disk_total }, href: '#/care?open=disk' }),
        Stat({ label: 'Swap', value: bytes(v.swap_total - v.swap_free), hint: `of ${bytes(v.swap_total)}`, icon: 'activity', meter: { value: v.swap_total - v.swap_free, max: v.swap_total || 1 }, href: '#/care?open=memory' }),
        Stat({ label: 'Uptime', value: duration(v.uptime), hint: `${v.os}, kernel ${v.kernel}`, icon: 'clock', href: '#/care?open=reboot' })),
      ServicesCard(v, { showLive }),
      ContainersCard(v, { showLive }));
  }

  function drawDetail() {
    if (infoError) { detail.replaceChildren(Alert({ tone: 'bad', title: 'Could not read server details' }, infoError, h('div', { class: 'mt-3' }, Button({ label: 'Try again', size: 'sm', icon: 'refresh', onClick: loadInfo })))); return; }
    if (!info) { detail.replaceChildren(Card({ title: 'Security and updates' }, Skeleton(4))); return; }
    const upgrade = Button({ label: 'Install updates', icon: 'download', variant: 'primary', size: 'sm', onClick: async () => {
      const c = await confirmDialog({ title: 'Install all pending updates?', message: 'Runs apt upgrade on the server. Services may restart; a reboot may be needed afterwards.', confirmLabel: 'Install updates', tone: 'primary' });
      if (!c.confirmed) return;
      const r = await attempt(api('/api/server/upgrade', {}));
      if (!r) return;
      const con = Console({ lines: [] });
      updatesBody.replaceChildren(con.el);
      const res = await watchJob(r.job, con);
      toast(res.ok ? 'Updates installed' : 'Update failed, read the log', { tone: res.ok ? 'ok' : 'bad' });
      loadInfo();
    } });
    const updatesBody = h('div', {}, info.updates_count ? h('div', {}, Console({ lines: info.updates }).el) : h('p', { class: 'text-sm text-muted' }, 'Everything is up to date.'));
    fill(detail,
      info.reboot_required ? Alert({ tone: 'warn', title: 'A reboot is needed', }, 'Updates were installed that need a restart to take effect. Reboot from the Maintenance card below when it suits you.') : null,
      h('div', { class: 'grid gap-6 lg:grid-cols-2' },
        Card({ title: 'Updates', subtitle: info.updates_count ? `${info.updates_count} packages can be updated` : 'No updates pending', actions: info.updates_count ? upgrade : null }, updatesBody),
        Card({ title: 'Firewall (ufw)' }, Console({ lines: [info.ufw || 'not available'] }).el)),
      h('div', { class: 'grid gap-6 lg:grid-cols-2' },
        Card({ title: 'Brute-force protection (fail2ban)', subtitle: 'SSH jail' }, Console({ lines: [info.fail2ban || 'not available'] }).el),
        Card({ title: 'Listening ports', subtitle: 'Only SSH should be reachable from outside; the rest are local' }, Console({ lines: [info.listening || ''] }).el)),
      h('div', { class: 'grid gap-6 lg:grid-cols-2' },
        Card({ title: 'Top processes by memory' }, Console({ lines: [info.top || ''] }).el),
        Card({ title: 'Recent logins' }, Console({ lines: [info.last_logins || ''] }).el)),
      info.docker_disk ? Card({ title: 'Docker disk usage' }, Console({ lines: [info.docker_disk] }).el) : null,
      h('section', { class: 'card border-bad/40' },
        h('div', { class: 'card-header' }, h('div', {}, h('h2', { class: 'text-sm text-bad' }, 'Maintenance'), h('p', { class: 'mt-0.5 text-xs text-muted' }, 'All sites go offline for about a minute while the server restarts. Services start again on their own.'))),
        h('div', { class: 'card-body' }, Button({ label: 'Reboot server', icon: 'power', variant: 'danger-soft', onClick: async () => {
          const c = await confirmDialog({ title: 'Reboot the server?', message: 'Every site and database is unavailable until it is back.', confirmLabel: 'Reboot now', requireText: 'reboot' });
          if (c.confirmed) await attempt(api('/api/server/reboot', { confirm: 'reboot' }), (r) => r.text);
        } }))));
  }

  const unsub = subscribe(drawTop);
  if (!store.status) refresh();
  drawTop();
  loadInfo();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Server', description: 'Health, services, security and maintenance.', actions: Button({ label: 'Refresh', icon: 'refresh', onClick: () => { refresh(); loadInfo(); } }) }), live, top, detail), destroy: () => { unsub(); clearInterval(timer); } };
}
