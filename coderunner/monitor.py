"""Live view of the server: resource samples, streamed logs and a per-site uptime check (history kept in data/health.json)."""
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import activity, alerts, config, deploy, heal, metricslog, serverops, settings, watch
from .runner import q, run, script

OPTIONS = (60, 120, 300, 600, 900, 1800, 3600)   # seconds between site checks the user can choose
DEFAULT_INTERVAL = 300
HEALTH_FILE = os.path.join(config.DATA, 'health.json')
CODES = {}              # host -> (HTTP status of the last probe, or 'err'; time)
STATE = {}              # site -> {host, points: [[ts, ms or None]], up, fails}
_lock = threading.Lock()


def schedule():
    """The uptime-check settings: {interval seconds, enabled}. Stored in settings.json, read every round so changes apply at once."""
    s = settings.get('monitor')
    interval = s.get('interval')
    return {'interval': interval if interval in OPTIONS else DEFAULT_INTERVAL, 'enabled': s.get('enabled', True) is not False}


def set_schedule(interval, enabled):
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        interval = 0
    if interval not in OPTIONS:
        raise ValueError('Choose one of: %s minutes' % ', '.join(str(o // 60) for o in OPTIONS))
    settings.put('monitor', {'interval': interval, 'enabled': bool(enabled)})
    return schedule()


def keep(interval):
    """How many results make 24 hours at this interval."""
    return max(24, 86400 // interval)


def metrics(cfg):
    rc, out = run(cfg, 'python3 -', inp=script('metrics.py').encode(), timeout=30)
    if rc or '{' not in out:
        raise RuntimeError(out.strip()[-300:] or 'could not read metrics')
    d = json.loads(out[out.index('{'):out.rindex('}') + 1])
    d['t'] = time.time()
    return d


# ---------------------------------------------------------------- live logs
def log_command(kind, name):
    """Remote command that follows a log. `timeout` bounds a stream whose client vanished; the browser reconnects."""
    if kind == 'site':
        deploy._name(name)
        return ('if test -f /etc/systemd/system/%s.service; then exec sudo timeout 1800 journalctl -u %s -n 100 -f --no-pager; '
                'elif sudo docker inspect shiplane-%s >/dev/null 2>&1; then exec sudo timeout 1800 docker logs --tail 100 -f shiplane-%s 2>&1; '
                'else echo "static site: there is no service log"; fi' % (name, name, name, name))
    if kind == 'service':
        if name not in serverops.SERVICES:
            raise ValueError('unknown service')
        return 'exec sudo timeout 1800 journalctl -u %s -n 100 -f --no-pager' % name
    if kind == 'container':
        if not serverops.CONTAINER_RE.match(name):
            raise ValueError('bad container name')
        return 'exec sudo timeout 1800 docker logs --tail 100 -f %s 2>&1' % q(name)
    raise ValueError('unknown log kind')


# ------------------------------------------------------------- site health
def _load():
    try:
        with open(HEALTH_FILE, encoding='utf-8') as f:
            for name, s in json.load(f).items():
                STATE[name] = dict(s, fails=0)
    except (OSError, ValueError):
        pass


def _save():
    os.makedirs(config.DATA, exist_ok=True)
    tmp = HEALTH_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({n: {k: s[k] for k in ('host', 'points', 'up')} for n, s in STATE.items()}, f)
    os.replace(tmp, HEALTH_FILE)


def switch_server():
    """The managed server changed: uptime history and the metrics ring described the old one, so start them afresh."""
    with _lock:
        STATE.clear()
        CODES.clear()
        try:
            _save()
        except OSError:
            pass
    metricslog.reset()


def _request(host, method):
    return urllib.request.Request('https://%s/' % host, method=method, headers={'User-Agent': 'coderunner-health'})


def probe(host):
    """Milliseconds for a public check, or None when the site is unreachable or answers 5xx (a 401/404 still means it is up).
    Uses HEAD (headers only, about 1 KB), and falls back to GET only for servers that refuse HEAD. The HTTP status is remembered in CODES."""
    t = time.time()
    code = 200
    try:
        try:
            urllib.request.urlopen(_request(host, 'HEAD'), timeout=10)
        except urllib.error.HTTPError as e:
            if e.code not in (405, 501):
                raise
            urllib.request.urlopen(_request(host, 'GET'), timeout=10).read(1)
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        CODES[host] = ('err', time.time())
        return None
    CODES[host] = (code, time.time())
    return None if code >= 500 else max(1, int((time.time() - t) * 1000))


def check_once(sites, probe=probe, interval=None):
    now = int(time.time())
    limit = keep(interval or schedule()['interval'])
    with _lock:
        for name in [n for n in STATE if n not in sites]:
            del STATE[name]   # deleted sites drop out
    for name, host in sites.items():
        _record(name, host, probe(host), now, limit)
    with _lock:
        _save()


def _record(name, host, ms, now, limit):
    with _lock:
        s = STATE.setdefault(name, {'host': host, 'points': [], 'up': True, 'fails': 0})
        s['host'] = host
        s['points'] = (s['points'] + [[now, ms]])[-limit:]
        s['fails'] = 0 if ms else s['fails'] + 1
        if ms and not s['up']:
            s['up'] = True
            activity.log('site-up', name, host, 'monitor', True)
            alerts.notify('[Shiplane] UP again: %s (%s)' % (name, host), site=name)
        elif s['fails'] >= 2 and s['up']:   # two misses in a row, so one blip does not raise an alarm
            s['up'] = False
            activity.log('site-down', name, host, 'monitor', False)
            alerts.notify('[Shiplane] DOWN: %s (%s) failed two checks in a row' % (name, host), site=name)


def check_now(name, host, probe=probe):
    """Check one site right away (the Check now button): recorded like a scheduled check. {up, ms, code}."""
    ms = probe(host)
    _record(name, host, ms, int(time.time()), keep(schedule()['interval']))
    with _lock:
        _save()
    return {'up': bool(ms), 'ms': ms, 'code': (CODES.get(host) or (None,))[0]}


RES = {'disk': 0, 'mem': 0, 'disk_on': False, 'mem_on': False}   # consecutive high readings, alert state


def check_resources(cfg, fetch=metrics):
    """Alert when the disk (85%) or memory (92%) stays high for two checks; say so again when it recovers (75% / 80%)."""
    m = fetch(cfg)
    for key, used, total, hi, lo, label in (('disk', m['disk_used'], m['disk_total'], 85, 75, 'Disk'), ('mem', m['ram_used'], m['ram_total'], 92, 80, 'Memory')):
        p = 100.0 * used / max(1, total)
        RES[key] = RES[key] + 1 if p >= hi else 0
        if RES[key] >= 2 and not RES[key + '_on']:
            RES[key + '_on'] = True
            alerts.notify('[Shiplane] %s is %d%% full on %s' % (label, p, cfg.get('host') or 'the server'))
        elif p < lo and RES[key + '_on']:
            RES[key + '_on'] = False
            alerts.notify('[Shiplane] %s is back to %d%%' % (label, p))
    return m


def last_result(host):
    """(status, milliseconds) of the newest uptime check of a host when it is recent enough to trust, else None."""
    plan, got = schedule(), CODES.get(host)
    if not plan['enabled'] or not got or time.time() - got[1] > plan['interval'] * 1.5:
        return None
    for s in STATE.values():
        if s['host'] == host and s['points']:
            return got[0], s['points'][-1][1] or 0
    return None


deploy.PUBLIC_LOOKUP = last_result


def _sites(cfg):
    sites = [s for s in deploy.status(cfg)['sites'] if s.get('hostname') and '*' not in s['hostname']]
    alerts.OWNERS.clear()
    alerts.OWNERS.update({s['name']: (s.get('meta') or {}).get('deployedBy', '') for s in sites})     # who deployed what, from each site's saved details
    return {s['name']: s['hostname'] for s in sites}


def run_round(load_cfg):
    """One round of checks. Returns how many seconds to wait before the next one."""
    plan = schedule()
    if plan['enabled']:
        try:
            check_once(_sites(load_cfg()), interval=plan['interval'])
        except Exception:
            pass   # server unreachable: try again next round
    try:
        cfg = load_cfg()
        m = check_resources(cfg)
        try:
            metricslog.record(m, keep=keep(plan['interval']))      # the charts remember what this round just read
        except Exception:
            pass
        watch.tick(cfg, m)                                          # so do the smart alerts
    except Exception:
        pass
    try:
        with _lock:
            answering = {n: s['up'] for n, s in STATE.items()}
        heal.tick(load_cfg(), up=answering)         # auto-heal uses the same shared status
    except Exception:
        pass
    return plan['interval']


def start(load_cfg):
    _load()

    def loop():
        while True:
            time.sleep(run_round(load_cfg))

    threading.Thread(target=loop, daemon=True).start()


def health():
    out = {}
    with _lock:
        for name, s in STATE.items():
            pts = s['points']
            ok = [p for p in pts if p[1]]
            out[name] = {'host': s['host'], 'up': s['up'], 'last_ms': pts[-1][1] if pts else None, 'checks': len(pts),
                         'uptime': round(100.0 * len(ok) / len(pts), 2) if pts else None, 'points': pts[-60:]}
    return dict(schedule(), sites=out)
