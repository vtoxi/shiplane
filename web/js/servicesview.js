// The Services and Containers cards of the Server page: see what each one costs in memory, stop what you do not need, start it again, and choose whether it starts at boot.
import { h, Badge, Button, Card, EmptyState, Logo, logoId, confirmDialog } from './kit.js';
import { api } from './api.js';
import { bytes } from './format.js';
import { store, refresh } from './store.js';
import { attempt } from './util.js';

const HINT = { cloudflared: 'Tunnel to Cloudflare', caddy: 'Web server / reverse proxy', docker: 'Runs your containers', postgresql: 'Postgres database', fail2ban: 'Blocks repeated SSH attempts', ufw: 'Firewall (manage it in Server care)',
  'mssql-server': 'Microsoft SQL Server', mariadb: 'MariaDB / MySQL database', mysql: 'MySQL database', 'redis-server': 'Redis cache and queue' };
const CONSEQUENCE = { caddy: 'Every site becomes unreachable until it is started again.', cloudflared: 'Every site behind the Cloudflare tunnel becomes unreachable.', docker: 'Every container stops (SQL Server, Portainer and the rest).',
  postgresql: 'Apps that use Postgres stop working.', mariadb: 'Apps that use MariaDB or MySQL stop working.', mysql: 'Apps that use MySQL stop working.', 'mssql-server': 'Apps that use SQL Server stop working.',
  'redis-server': 'Apps that use Redis may fail or slow down.', fail2ban: 'SSH is no longer protected from repeated login attempts.' };
const label = (n) => (/^php\d/.test(n) ? `PHP ${n.slice(3, 6)} (FPM)` : n);
const hint = (n) => HINT[n] || (/^php\d/.test(n) ? 'Runs PHP sites that use this version' : '');
const mem = (n) => (n ? bytes(n) : null);
const bar = (value, max) => h('div', { class: 'meter mt-2', role: 'meter', 'aria-valuenow': max ? Math.round((100 * value) / max) : 0, 'aria-valuemin': 0, 'aria-valuemax': 100 },
  h('span', { class: 'bg-brand', style: `width:${max ? Math.min(100, Math.round((100 * value) / max)) : 0}%` }));

async function ask(title, message, confirmLabel) {
  return (await confirmDialog({ title, message, confirmLabel, tone: 'primary' })).confirmed;
}

export function ServicesCard(v, { showLive }) {
  const info = v.service_info || {};
  const local = store.session.server.mode === 'local';
  const saved = (n) => (info[n] && info[n].mem) || 0;
  const act = async (name, action, ok) => { if (await attempt(api('/api/server/service', { name, action }), ok || ((r) => r.text))) await refresh({ quiet: true }); };

  async function stop(n) {
    const freed = mem(saved(n));
    if (await ask(`Stop ${label(n)}?`, `${freed ? `This frees about ${freed} of memory right away. ` : ''}${CONSEQUENCE[n] || ''} It stays stopped until you start it again.`, 'Stop')) await act(n, 'stop');
  }
  async function boot(n, on) {
    if (await ask(on ? `Start ${label(n)} at boot?` : `Do not start ${label(n)} at boot?`, on ? 'It will start by itself whenever the server starts.' : 'After a restart of the server it stays off until you start it. It keeps running right now.', on ? 'Start at boot' : 'Turn off')) await act(n, on ? 'enable' : 'disable');
  }

  const rows = Object.entries(v.services).map(([n, s]) => {
    const i = info[n] || {};
    const installed = s !== 'unknown';
    const running = s === 'active';
    const portal = local && (n === 'caddy' || n === 'cloudflared');
    const noStop = n === 'ufw';
    return h('li', { class: 'flex flex-wrap items-center justify-between gap-3 px-5 py-3' },
      h('div', { class: 'min-w-0' }, h('p', { class: 'flex items-center gap-2 font-medium' }, Logo(logoId(n), 'h-4 w-4'), label(n), mem(i.mem) ? h('span', { class: 'ml-2 text-xs font-normal text-muted' }, mem(i.mem)) : null), h('p', { class: 'text-xs text-muted' }, hint(n))),
      installed ? h('div', { class: 'flex flex-wrap items-center gap-2' },
        Badge(running ? 'Running' : s === 'failed' ? 'Failed' : 'Stopped', running ? 'ok' : s === 'failed' ? 'bad' : 'neutral', { dot: true }),
        noStop ? null : h('button', { type: 'button', class: 'rounded-md border border-line px-2 py-1 text-xs text-muted hover:border-brand hover:text-ink', title: 'Click to change whether it starts when the server starts',
          onClick: () => boot(n, i.enabled !== 'enabled') }, i.enabled === 'enabled' ? 'Starts at boot' : 'Off at boot'),
        Button({ label: 'Logs', icon: 'file', size: 'sm', variant: 'ghost', onClick: () => showLive('service', n) }),
        noStop ? null : running
          ? Button({ label: 'Stop', icon: 'stop', size: 'sm', disabled: portal, title: portal ? 'This page is reached through it: stop it from Shiplane on your computer instead' : 'Stop and free its memory', onClick: () => stop(n) })
          : Button({ label: 'Start', icon: 'play', size: 'sm', variant: 'primary', onClick: () => act(n, 'start') }),
        running ? Button({ label: 'Restart', icon: 'refresh', size: 'sm', onClick: async () => {
          if (await ask(`Restart ${label(n)}?`, n === 'cloudflared' || n === 'caddy' ? 'Sites are unreachable for a few seconds.' : 'Dependent apps may briefly lose their connection.', 'Restart')) await act(n, 'restart');
        } }) : null) : Badge('Not installed', 'neutral'));
  });
  return Card({ title: 'Services', subtitle: 'Memory is what each one uses now. Stop what you do not need to get it back at once; Start brings it back.', bodyClass: '' }, h('ul', { class: 'divide-y divide-line' }, rows));
}

export function ContainersCard(v, { showLive }) {
  const info = v.container_info || {};
  const total = v.ram_total || 1;
  const act = async (name, action) => { if (await attempt(api('/api/server/container', { name, action }), (r) => r.text || 'Done')) await refresh({ quiet: true }); };
  if (!v.containers.length) return Card({ title: 'Containers', bodyClass: '' }, EmptyState({ icon: 'box', title: v.services.docker === 'active' ? 'No containers' : 'Docker is not running', body: v.services.docker === 'active' ? '' : 'Start Docker in Services to see and start your containers.' }));
  return Card({ title: 'Containers', subtitle: 'Stop one you do not need to free its memory. A stopped container is kept: Start brings it back with its data.', bodyClass: '' },
    h('ul', { class: 'divide-y divide-line' }, v.containers.map((line) => {
      const [name, status] = line.split('|');
      const i = info[name] || {};
      const up = /^Up/.test(status);
      const auto = i.policy && i.policy !== 'no';
      return h('li', { class: 'px-5 py-3' }, h('div', { class: 'flex flex-wrap items-center justify-between gap-3' },
        h('div', { class: 'min-w-0' }, h('p', { class: 'flex items-center gap-2 font-medium' }, Logo(logoId(name), 'h-4 w-4'), name, mem(i.mem) ? h('span', { class: 'ml-2 text-xs font-normal text-muted' }, mem(i.mem)) : null), h('p', { class: 'text-xs text-muted' }, status)),
        h('div', { class: 'flex flex-wrap items-center gap-2' }, Badge(up ? 'Running' : 'Stopped', up ? 'ok' : 'neutral', { dot: true }),
          h('button', { type: 'button', class: 'rounded-md border border-line px-2 py-1 text-xs text-muted hover:border-brand hover:text-ink', title: 'Click to change whether it starts again after a reboot or a Docker restart',
            onClick: async () => { if (await ask(auto ? `Do not start ${name} automatically?` : `Start ${name} automatically?`, auto ? 'After a reboot it stays off until you start it.' : 'It will start by itself after a reboot, unless you stopped it by hand.', 'Confirm')) await act(name, auto ? 'autostart-off' : 'autostart-on'); } }, auto ? 'Starts automatically' : 'Manual start'),
          Button({ label: 'Logs', icon: 'file', size: 'sm', variant: 'ghost', onClick: () => showLive('container', name) }),
          up ? Button({ label: 'Stop', icon: 'stop', size: 'sm', onClick: async () => { if (await ask(`Stop ${name}?`, `${mem(i.mem) ? `This frees about ${mem(i.mem)} of memory right away. ` : ''}Apps that use it stop working until you start it again. Its data is kept.`, 'Stop')) await act(name, 'stop'); } })
            : Button({ label: 'Start', icon: 'play', size: 'sm', variant: 'primary', onClick: () => act(name, 'start') }),
          up ? Button({ label: 'Restart', icon: 'refresh', size: 'sm', onClick: async () => { if (await ask(`Restart ${name}?`, 'Dependent apps may briefly lose their connection.', 'Restart')) await act(name, 'restart'); } }) : null)),
        up && i.mem ? bar(i.mem, total) : null);
    })));
}
