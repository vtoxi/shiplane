"""Several servers under one Shiplane: a list of saved connections, a side-by-side health overview, and a one-click "adopt" that prepares a fresh server.

Exactly one server is *active*: it is the one every other page manages (sites, databases, care ...), and the one the uptime checks, backups and auto-heal follow.
Switching writes it into server.json, so the rest of the app needs no changes. The overview reads every server without switching.
Connections (with passwords) live in fleet.json next to server.json, owner-only, never shown again.
"""
import concurrent.futures
import json
import os
import re
import secrets

from . import config, deploy, monitor, perf
from .runner import run, script

FILE = os.path.join(config.CONF, 'fleet.json')
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$')
MAX_SERVERS = 25
CONFIRM = 'adopt'


def _file():
    return os.path.join(config.CONF, 'fleet.json')


def _load():
    try:
        with open(_file(), encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if not d.get('servers'):                                    # the connection already in server.json becomes the first entry
        cur = config.load_server()
        d = {'servers': [dict(cur, id='s1', name=cur.get('host') or 'This server')], 'active': 's1'} if cur.get('host') or cur.get('mode') == 'local' else {'servers': [], 'active': ''}
    return d


def _save(d):
    os.makedirs(config.CONF, exist_ok=True)
    fd = os.open(_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        config._owner_only(fd)
        json.dump(d, f, indent=1)


def _find(d, sid):
    for s in d['servers']:
        if s['id'] == sid:
            return s
    raise LookupError('no such server')


def _public(s, active):
    out = config.public_server({k: v for k, v in s.items() if k not in ('id', 'name')})
    return dict(out, id=s['id'], name=s['name'], active=s['id'] == active)


def list_servers():
    d = _load()
    return {'servers': [_public(s, d['active']) for s in d['servers']], 'active': d['active']}


def save(body):
    """Add a server, or change one when `id` is given (an empty password keeps the saved one)."""
    d = _load()
    name = str(body.get('name') or '').strip()
    if not NAME_RE.match(name):
        raise ValueError('Give the server a name (letters, numbers, spaces, . _ -)')
    if any(s['name'].lower() == name.lower() and s['id'] != body.get('id') for s in d['servers']):
        raise ValueError('There is already a server with that name')
    old = _find(d, body['id']) if body.get('id') else None
    c = dict(body, mode='ssh')
    if old and c.get('auth') == 'password' and not c.get('password') and (c.get('host'), c.get('user')) == (old.get('host'), old.get('user')):
        c['password'] = old.get('password', '')
    entry = dict(config.check_server(c), name=name, id=old['id'] if old else 's' + secrets.token_hex(3))
    if not old:
        if len(d['servers']) >= MAX_SERVERS:
            raise ValueError('At most %d servers' % MAX_SERVERS)
        if any((s.get('host'), s.get('user'), s.get('port')) == (entry['host'], entry['user'], entry['port']) for s in d['servers']):
            raise ValueError('That server is already in the list')
        d['servers'].append(entry)
        if not d['active']:                                     # the very first server is simply the one being managed
            d['active'] = entry['id']
            config.save_server(entry)
    else:
        d['servers'][d['servers'].index(old)] = entry
        if d['active'] == entry['id']:
            config.save_server(entry)
    _save(d)
    return _public(entry, d['active'])


def delete(sid):
    d = _load()
    s = _find(d, sid)
    if d['active'] == sid:
        raise ValueError('This is the server being managed now. Switch to another one first.')
    d['servers'].remove(s)
    _save(d)


def activate(sid):
    d = _load()
    s = _find(d, sid)
    config.save_server(s)
    d['active'] = sid
    _save(d)
    monitor.switch_server()                         # uptime history belongs to one server's sites
    perf.invalidate()
    return _public(s, sid)


def parse_probe(out):
    kv = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    num = lambda k, cast=int: cast(kv[k]) if re.fullmatch(r'[0-9.]+', kv.get(k, '')) else None
    return {'os': kv.get('os', '').replace('_', ' '), 'load': num('load', float), 'cpus': num('cpus'), 'mem': num('mem'), 'disk': num('disk'), 'sites': num('sites'),
            'failed': num('failed'), 'reboot': kv.get('reboot') == '1', 'caddy': kv.get('caddy') == '1'}


def _health(s):
    problems = []
    if s['disk'] is not None and s['disk'] >= 90:
        problems.append('disk %d%% full' % s['disk'])
    if s['mem'] is not None and s['mem'] >= 90:
        problems.append('memory %d%% used' % s['mem'])
    if s['failed']:
        problems.append('%d failed service%s' % (s['failed'], '' if s['failed'] == 1 else 's'))
    if s['cpus'] and s['load'] is not None and s['load'] > s['cpus'] * 2:
        problems.append('high load')
    if not s['caddy']:
        problems.append('not set up for deploys yet (Adopt)')
    return problems


def _probe(entry):
    cfg = {k: v for k, v in entry.items() if k not in ('id', 'name')}
    rc, out = run(cfg, 'bash -s', inp=script('fleet_probe.sh').encode(), timeout=25)
    if rc or 'load=' not in out:
        return {'id': entry['id'], 'name': entry['name'], 'ok': False, 'error': (out.strip()[-200:] or 'could not connect'), 'problems': ['unreachable']}
    info = parse_probe(out)
    return dict(info, id=entry['id'], name=entry['name'], host=entry.get('host', ''), ok=True, error='', problems=_health(info))


def overview():
    d = _load()
    if not d['servers']:
        return []
    with concurrent.futures.ThreadPoolExecutor(min(8, len(d['servers']))) as ex:
        rows = list(ex.map(_probe, d['servers']))
    for r in rows:
        r['active'] = r['id'] == d['active']
    return rows


def _adopt_job(job, cfg):
    rc = deploy.stream_script(job, cfg, 'set -euo pipefail\n' + script('bootstrap.sh'))
    job.ok = rc == 0
    job.say('DONE: the server is ready for deploys' if job.ok else 'FAILED (exit code %s)' % rc)


def start_adopt(sid, confirm):
    if confirm != CONFIRM:
        raise ValueError('Type "adopt" to confirm')
    entry = _find(_load(), sid)
    cfg = {k: v for k, v in entry.items() if k not in ('id', 'name')}
    return deploy.start_job(_adopt_job, cfg)
