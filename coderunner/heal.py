"""Auto-heal: bring back a site's service or a core service that died, without anyone logging in.

Careful by design: it never touches a site you stopped on purpose, waits longer after every try (1, 2, 4, 8, 16 minutes), gives up after five tries
in an hour (and says so), and only restarts a service that answers badly after the uptime check has failed twice. Runs inside the monitor round on
the shared status, so it adds no background thread and no extra server calls while everything is healthy.
"""
import time

from . import activity, alerts, config, deploy, serverops, settings, watch
from .runner import q, run

KINDS = {
    'sites_down': 'Restart a site whose service stopped or failed',
    'sites_hung': 'Restart a site that is running but no longer answers',
    'services': 'Restart core services that died (web server, tunnel, database, fail2ban)',
}
CORE = ('cloudflared', 'caddy', 'docker', 'postgresql', 'fail2ban', 'mssql-server', 'mariadb', 'mysql', 'redis-server')     # ufw is a switch, not a running program: never "restarted"
DELAYS = (60, 120, 240, 480, 960)                                         # seconds to wait after the 1st, 2nd... try
MAX_TRIES = 5                                                             # per hour, then give up until it recovers
MAX_HUNG_TRIES = 2
TRANSIENT = ('activating', 'reloading', 'deactivating')


def enabled():
    saved = settings.get('heal')
    return {k: saved.get(k, True) is not False for k in KINDS}


def set_enabled(values):
    unknown = [k for k in values if k not in KINDS]
    if unknown:
        raise ValueError('Unknown option: ' + ', '.join(sorted(unknown)))
    current = enabled()
    current.update({k: bool(v) for k, v in values.items()})
    settings.put('heal', current)
    return current


# ------------------------------------------------------- stopped on purpose
def _mutate(fn):
    with watch._lock:
        state = watch._load()
        fn(state.setdefault('heal', {}))
        watch._save(state)


def pause(name):
    """The user stopped this site: leave it alone until it is started again."""
    _mutate(lambda h: h.setdefault('paused', []).count(name) or h['paused'].append(name))


def resume(name):
    """The user started, restarted, redeployed or deleted it: watch it afresh (an earlier "gave up" no longer applies)."""
    def go(h):
        if name in h.get('paused', []):
            h['paused'].remove(name)
        h.get('targets', {}).pop(name if name.startswith('svc:') else 'site:' + name, None)
    _mutate(go)


def on_site_action(name, action):
    """Called by deploy for every site action, so the user's intent is remembered."""
    if action == 'stop':
        pause(name)
    elif action in ('start', 'restart', 'activate', 'deploy', 'delete'):
        resume(name)


deploy.ON_SITE_ACTION = on_site_action


def on_service_action(name, action):
    """A core service was stopped, started, restarted, enabled or disabled from the Server page: respect it."""
    key = 'svc:' + name
    if action in ('stop', 'disable'):
        pause(key)
    elif action in ('start', 'restart', 'enable'):
        resume(key)


serverops.ON_SERVICE_ACTION = on_service_action


# ------------------------------------------------------------------ the pass
def candidates(status, heal, on, up):
    """What needs attention right now: [(key, unit, reason)]. Also learns which core services have been running (only those can "die")."""
    paused = set(heal.get('paused', []))
    seen = set(heal.setdefault('seen_active', []))
    out = []
    for s in status.get('sites', []):
        name = s['name']
        if not s.get('has_service') or s.get('type') == 'docker' or name in paused or name in deploy.PROTECTED or not config.NAME_RE.match(name):
            continue
        svc = s.get('service')
        if svc in TRANSIENT:
            continue
        if svc in ('failed', 'inactive') and on['sites_down']:
            out.append(('site:' + name, name + '.service', 'down'))
        elif svc == 'active' and up.get(name) is False and on['sites_hung']:
            out.append(('site:' + name, name + '.service', 'hung'))
    for name, state in (status.get('server', {}).get('services') or {}).items():
        if name not in CORE and not serverops.PHP_FPM_RE.match(name):
            continue
        if state == 'active':
            seen.add(name)
        elif name in seen and 'svc:' + name not in paused and state not in TRANSIENT and on['services']:
            out.append(('svc:' + name, name + '.service', 'down'))
    heal['seen_active'] = sorted(seen)
    return out


def _act(cfg, unit, reason, runner):
    """restart (a running but unresponsive site) or reset + start (a dead one), then report whether it is active again."""
    verb = 'restart' if reason == 'hung' else 'start'
    rc, out = runner(cfg, 'sudo systemctl reset-failed %s 2>/dev/null; sudo systemctl %s %s && sleep 3; systemctl is-active %s' % (q(unit), verb, q(unit), q(unit)), timeout=60)
    return rc == 0 and out.strip().splitlines()[-1:] == ['active']


def tick(cfg, status=None, up=None, now=None, runner=run, notify=alerts.notify):
    """One pass. Returns the messages sent."""
    now = time.time() if now is None else now
    status = status if status is not None else deploy.status(cfg)
    on, up, sent = enabled(), up or {}, []
    with watch._lock:
        state = watch._load()
        heal = state.setdefault('heal', {})
        targets = heal.setdefault('targets', {})
        todo = candidates(status, heal, on, up)
        needing = {key for key, _, _ in todo}
        for key in [k for k in targets if k not in needing]:               # healthy (or deleted, or paused) again: forget the history
            if targets[key].get('gave_up'):
                sent.append('[Shiplane] %s recovered' % key.split(':', 1)[1])
            del targets[key]
        for key, unit, reason in todo:
            t = targets.setdefault(key, {'tries': [], 'gave_up': False})
            t['tries'] = [x for x in t['tries'] if now - x < 3600]
            cap = MAX_HUNG_TRIES if reason == 'hung' else MAX_TRIES
            if t['gave_up']:
                continue
            if len(t['tries']) >= cap:
                t['gave_up'] = True
                sent.append('[Shiplane] Could not bring %s back after %d automatic tries. It needs a look (logs on its Site page).' % (key.split(':', 1)[1], len(t['tries'])))
                activity.log('auto-heal', key.split(':', 1)[1], 'gave up after %d tries' % len(t['tries']), 'monitor', False)
                continue
            if t['tries'] and now - t['tries'][-1] < DELAYS[min(len(t['tries']) - 1, len(DELAYS) - 1)]:
                continue                                                        # still waiting out the back-off
            ok = _act(cfg, unit, reason, runner)
            t['tries'].append(now)
            name = key.split(':', 1)[1]
            activity.log('auto-heal', name, '%s (%s): %s' % (reason, 'try %d' % len(t['tries']), 'running again' if ok else 'still not running'), 'monitor', ok)
            if len(t['tries']) == 1:
                sent.append('[Shiplane] %s %s; restarted it automatically. %s' % (name, 'was not answering' if reason == 'hung' else 'was down', 'It is running again.' if ok else 'It is still not running; trying again shortly.'))
        watch._save(state)
    for text in sent:
        notify(text)
    return sent
