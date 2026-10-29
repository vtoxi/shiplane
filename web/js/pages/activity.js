import { h, Button, Badge, Table, EmptyState, Alert, PageHeader, Input, icon, Skeleton } from '../kit.js';
import { api } from '../api.js';

const TONE = { deploy: 'brand', delete: 'bad', 'db-drop': 'bad', 'cf-unmap': 'warn', reboot: 'warn', stop: 'warn', 'apt-upgrade': 'info' };

export function ActivityPage() {
  let events = null;
  let error = null;
  let q = '';
  const box = h('div', {});
  const search = Input({ placeholder: 'Filter activity...', onInput: (e) => { q = e.target.value.toLowerCase(); draw(); } });

  async function load() {
    try { events = (await api('/api/activity')).events; error = null; } catch (e) { error = e.message; }
    draw();
  }

  function draw() {
    if (error) { box.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the activity log' }, error)); return; }
    if (!events) { box.replaceChildren(h('div', { class: 'p-6' }, Skeleton(4))); return; }
    const rows = events.filter((e) => !q || (e.kind + e.target + e.detail + e.who).toLowerCase().includes(q));
    box.replaceChildren(Table({
      columns: [
        { label: 'When', class: 'whitespace-nowrap text-muted', render: (e) => e.t },
        { label: 'Action', render: (e) => Badge(e.kind, TONE[e.kind] || 'neutral') },
        { label: 'Target', render: (e) => h('span', { class: 'font-medium' }, e.target || '-') },
        { label: 'Details', class: 'text-muted', render: (e) => e.detail },
        { label: 'By', class: 'text-muted', render: (e) => e.who },
        { label: '', render: (e) => (e.ok ? '' : Badge('failed', 'bad')) },
      ],
      rows,
      empty: EmptyState({ icon: 'activity', title: events.length ? 'Nothing matches' : 'No activity yet', body: events.length ? 'Try another filter.' : 'Deploys, restarts, rollbacks and other changes made with this app show up here. Secret values are never logged.' }),
    }));
  }

  draw();
  load();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Activity', description: 'What was changed with this app, newest first. Kept on this computer.', actions: Button({ label: 'Refresh', icon: 'refresh', onClick: load }) }),
    h('section', { class: 'card' }, h('div', { class: 'border-b border-line px-4 py-3 sm:w-96' }, search), box)) };
}
