// Live view of one migration: every step with its status, a progress bar, the log, and the DNS checklist at the end.
import { h, icon, fill, Button, Badge, Card, Alert, PageHeader, Meter, Console } from '../kit.js';
import { api } from '../api.js';
import { duration } from '../format.js';

const STATUS = {
  pending: ['clock', 'text-subtle', 'Waiting'],
  running: ['loader', 'text-brand animate-spin', 'Running'],
  done: ['check', 'text-ok', 'Done'],
  failed: ['alert', 'text-bad', 'Failed'],
  skipped: ['info', 'text-muted', 'Skipped'],
};

export function MigrationRunPage({ params }) {
  let view = null;
  let timer = null;
  let since = 0;
  const con = Console({ empty: 'Waiting for the first message...' });
  const head = h('div', {});
  const body = h('div', { class: 'space-y-6' });

  function stepRow(s) {
    const [ic, cls, label] = STATUS[s.status];
    return h('li', { class: 'flex items-start gap-3 py-2' },
      h('span', { class: cls + ' mt-0.5', 'aria-label': label }, icon(ic, 'h-4 w-4')),
      h('div', { class: 'min-w-0' }, h('p', { class: 'text-sm' + (s.status === 'pending' ? ' text-muted' : ' font-medium') }, s.label),
        s.detail ? h('p', { class: 'text-xs break-words ' + (s.status === 'failed' ? 'text-bad' : 'text-muted') }, s.detail) : null));
  }

  function checklist(r) {
    const ip = r.server_ip || 'the new server';
    return Card({ title: 'Switch your domains', subtitle: 'The sites are running on the new server but visitors still reach the old one until DNS changes.' },
      h('ol', { class: 'list-decimal space-y-2 pl-5 text-sm' },
        h('li', {}, `Open each site on the new server first (hosts-file trick or the server address ${ip}) and check it works, including the database.`),
        h('li', {}, 'Lower the DNS TTL on the domains a day ahead if you can.'),
        h('li', {}, `Change the A record of ${r.domains.length ? r.domains.join(', ') : 'each domain'} to ${ip} (or map them in Cloudflare).`),
        h('li', {}, 'Keep the old hosting for a few days, then cancel it.')));
  }

  function draw() {
    if (!view) return;
    const total = view.total || 1;
    const groups = [...new Set(view.steps.map((s) => s.group))];
    const running = view.status === 'running';
    head.replaceChildren(PageHeader({
      crumbs: [{ label: 'Migrate', href: '#/migrate' }, { label: 'Progress' }], title: view.title,
      description: h('span', { class: 'inline-flex items-center gap-2' }, Badge(running ? 'Running' : view.status === 'done' ? 'Finished' : 'Needs attention', running ? 'brand' : view.status === 'done' ? 'ok' : 'bad', { dot: true }),
        h('span', { class: 'text-sm text-muted' }, `${view.done} of ${view.total} steps - ${duration((view.finished || Date.now() / 1000) - view.started)}`)),
    }));
    const stepsCard = Card({ title: 'Steps' }, Meter({ value: view.done, max: total, warnAt: 101, badAt: 101 }),
      h('div', { class: 'space-y-4 pt-4' }, groups.map((g) => h('div', {}, h('p', { class: 'text-xs font-medium uppercase tracking-wide text-subtle' }, g),
        h('ul', { class: 'divide-y divide-line' }, view.steps.filter((s) => s.group === g).map(stepRow))))));
    fill(body,
      !running && view.status === 'failed' ? Alert({ tone: 'warn', title: 'Some steps need attention' }, 'The rest of the migration still ran. Fix the failed steps below, then run the migration again for just those items.') : null,
      stepsCard,
      view.report ? [
        view.report.manual.length ? Card({ title: 'Needs your attention' }, h('ul', { class: 'list-disc space-y-1 pl-5 text-sm' }, view.report.manual.map((m) => h('li', {}, m)))) : null,
        checklist(view.report)] : null,
      Card({ title: 'Log', subtitle: 'Passwords are never written here' }, con.el),
      h('div', {}, Button({ label: 'Back to Migrate', href: '#/migrate' })));
  }

  async function poll() {
    try {
      view = await api(`/api/migrate/run?id=${params.id}&since=${since}`);
      since = view.next;
      if (view.lines.length) con.push(view.lines);
      draw();
      if (view.status !== 'running') return;
    } catch (e) {
      body.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the migration' }, e.message));
      return;
    }
    timer = setTimeout(poll, 1500);
  }

  poll();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, head, body), destroy: () => clearTimeout(timer) };
}
