"""Smart alerts: problems that are about to happen, or that nobody would notice, reported once and cleared when they are gone.

Runs inside the monitor round and reuses what that round already fetched (metrics, the shared status), so it adds no background thread and
at most one small command per hour. State is one small JSON file (data/care.json, at most about 10 KB).
"""
import json
import os
import re
import threading
import time

from . import activity, alerts, config, deploy, maintenance, settings
from .runner import run

KINDS = {   # id -> (label shown in Settings)
    'disk_forecast': 'The disk will be full within a week at the current rate',
    'cert_expiry': 'A site certificate expires within 14 days (or is broken)',
    'crash_loop': 'A site keeps crashing and restarting',
    'high_load': 'The server is overloaded for several minutes',
    'oom': 'Linux ran out of memory and stopped a program',
    'reboot': 'A restart is needed to finish security updates',
}
SAMPLES = 120            # disk samples kept: 30 days at four a day
SAMPLE_EVERY = 6 * 3600
MIN_SPAN = 2 * 86400     # a trend needs at least two days of samples
HORIZON_DAYS = 7
CERT_DAYS = 14
LOAD_ROUNDS = 3          # overloaded for this many rounds in a row
CRASHES = 3              # restarts between two rounds that count as a loop
COOLDOWN = 86400         # a condition that stays true is repeated once a day, no more
_lock = threading.Lock()


def _file():
    return os.path.join(config.DATA, 'care.json')


def _load():
    try:
        with open(_file(), encoding='utf-8') as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state):
    os.makedirs(config.DATA, exist_ok=True)
    tmp = _file() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, separators=(',', ':'))
    os.replace(tmp, _file())


# ------------------------------------------------------------------ settings
def enabled():
    """Which alert kinds are on (all by default)."""
    saved = settings.get('watch')
    return {k: saved.get(k, True) is not False for k in KINDS}


def set_enabled(values):
    unknown = [k for k in values if k not in KINDS]
    if unknown:
        raise ValueError('Unknown alert: ' + ', '.join(sorted(unknown)))
    current = enabled()
    current.update({k: bool(v) for k, v in values.items()})
    settings.put('watch', current)
    return current


# --------------------------------------------------------------- pure rules
def forecast(samples, total, now):
    """Days until the disk is full if it keeps filling at the trend of `samples` [[time, used bytes]...]; None when it is not growing
    or there is not yet enough history to say."""
    pts = [(t, u) for t, u in samples if now - t <= SAMPLES * SAMPLE_EVERY]
    if len(pts) < 8 or pts[-1][0] - pts[0][0] < MIN_SPAN:
        return None
    n = len(pts)
    mt, mu = sum(t for t, _ in pts) / n, sum(u for _, u in pts) / n
    var = sum((t - mt) ** 2 for t, _ in pts)
    slope = sum((t - mt) * (u - mu) for t, u in pts) / var if var else 0.0       # bytes per second
    if slope <= 0:
        return None
    return max(0.0, (total * 0.97 - pts[-1][1]) / slope / 86400)


def _signal(state, key, active, message, ok_message, now, send):
    """Send `message` when a condition starts (and once a day while it lasts), `ok_message` when it ends. Returns the text sent or ''."""
    known = state.setdefault('alerts', {})
    if key not in known and not active:
        return ''                                                                   # nothing was ever wrong: keep no record
    cur = known.get(key, {'active': False, 'at': 0})
    text = ''
    if active and (not cur['active'] or now - cur['at'] >= COOLDOWN):
        text, cur = message, {'active': True, 'at': now}
    elif not active and cur['active']:
        text, cur = ok_message, {'active': False, 'at': now}
    state['alerts'][key] = cur
    if text:
        send(text)
        activity.log('alert', key, text[:200], 'monitor', not active)
    return text


# --------------------------------------------------------------------- rules
def _disk(state, m, now, on, send):
    samples = state.setdefault('disk', [])
    if not samples or now - samples[-1][0] >= SAMPLE_EVERY:
        samples.append([int(now), int(m['disk_used'])])
        del samples[:-SAMPLES]
    days = forecast(samples, m['disk_total'], now) if on else None
    full_now = 100.0 * m['disk_used'] / max(1, m['disk_total']) >= 85            # the resource alert already says that
    soon = days is not None and days < HORIZON_DAYS and not full_now
    return _signal(state, 'disk_forecast', soon, '[Shiplane] The disk will be full in about %d day(s) at the current rate. Clean up (Maintenance, Disk & cleanup) or add space.' % max(1, int(days or 0)),
                   '[Shiplane] Disk growth is back to a safe pace', now, send)


def _load_rule(state, m, now, on, send):
    cpus = max(1, int(m.get('cpus') or 1))
    ratio = float(m.get('load', 0)) / cpus
    streak = state.get('load_streak', 0)
    streak = streak + 1 if ratio > 2 else 0 if ratio < 1 else streak
    state['load_streak'] = streak
    return _signal(state, 'high_load', on and streak >= LOAD_ROUNDS, '[Shiplane] The server is overloaded (load %.1f on %d CPU%s) for several minutes. A site may be stuck or traffic is high.' % (m.get('load', 0), cpus, '' if cpus == 1 else 's'),
                   '[Shiplane] The server load is back to normal', now, send)


def _crash(state, status, now, on, send):
    seen = state.setdefault('restarts', {})
    sent = []
    for s in status.get('sites', []):
        name = s['name']
        try:
            count = int(s.get('restarts') or 0)
        except (TypeError, ValueError):
            continue
        prev = seen.get(name)
        seen[name] = count
        looping = on and s.get('has_service') and prev is not None and count - prev >= CRASHES
        sent.append(_signal(state, 'crash_loop:' + name, bool(looping), '[Shiplane] %s keeps crashing: it restarted %d times in the last few minutes. Check its logs.' % (name, count - (prev or 0)),
                            '[Shiplane] %s is stable again' % name, now, send))
    for gone in [n for n in seen if n not in {s['name'] for s in status.get('sites', [])}]:
        del seen[gone]
    return ' '.join(t for t in sent if t)


def _certs(state, cfg, now, on, send):
    if not on:
        return ''
    sent = []
    for d in maintenance.domain_health(cfg):
        bad = d['dns'] is not None and (d['cert_days'] is None or d['cert_days'] < CERT_DAYS)
        what = 'its certificate is broken (%s)' % (d['tls_error'] or 'no certificate') if d['cert_days'] is None else 'its certificate expires in %d day(s)' % d['cert_days']
        sent.append(_signal(state, 'cert_expiry:' + d['host'], bad, '[Shiplane] %s: %s.' % (d['host'], what), '[Shiplane] %s: certificate is fine again' % d['host'], now, send))
    return ' '.join(t for t in sent if t)


HOURLY = ('echo "OOM=$(journalctl -k --since \'-65 min\' --no-pager 2>/dev/null | grep -c \'Out of memory\')"; '
          'journalctl -k --since \'-65 min\' --no-pager 2>/dev/null | grep -o \'Killed process [0-9]* ([^)]*)\' | tail -3; '
          'test -f /var/run/reboot-required && echo REBOOT=1 || echo REBOOT=0')


def _hourly(state, cfg, now, on, send, runner=run):
    rc, out = runner(cfg, HOURLY, timeout=40)
    if rc:
        return ''
    oom = re.search(r'OOM=(\d+)', out)
    victims = ', '.join(re.findall(r'\(([^)]+)\)', ' '.join(re.findall(r'Killed process \d+ \([^)]*\)', out)))) or 'a program'
    sent = _signal(state, 'oom', on['oom'] and bool(oom and int(oom.group(1))), '[Shiplane] The server ran out of memory and Linux stopped %s. Add swap (Server care) or more memory.' % victims,
                   '[Shiplane] No memory kills in the last hour', now, send)
    reboot = _signal(state, 'reboot', on['reboot'] and 'REBOOT=1' in out, '[Shiplane] The server needs a restart to finish security updates (Server page).', '[Shiplane] The restart has been done', now, send)
    return ' '.join(t for t in (sent, reboot) if t)


def tick(cfg, metrics, now=None, send=alerts.notify, status=None, runner=run):
    """One pass, called by every monitor round. Returns the messages sent (for tests and the activity view)."""
    now = time.time() if now is None else now
    on = enabled()
    sent = []
    with _lock:
        state = _load()
        for key in [k for k in state.get('alerts', {}) if k.split(':')[0] in KINDS and not on[k.split(':')[0]]]:
            del state['alerts'][key]                                               # switched off: forget it quietly, no 'all clear' for something you muted
        sent.append(_disk(state, metrics, now, on['disk_forecast'], send))
        sent.append(_load_rule(state, metrics, now, on['high_load'], send))
        sent.append(_crash(state, status if status is not None else deploy.status(cfg), now, on['crash_loop'], send))
        last = state.setdefault('last', {})
        if now - last.get('hourly', 0) >= 3600:
            last['hourly'] = now
            sent.append(_hourly(state, cfg, now, on, send, runner))
        if now - last.get('daily', 0) >= 86400:
            last['daily'] = now
            sent.append(_certs(state, cfg, now, on['cert_expiry'], send))
        _save(state)
    return [t for t in sent if t]
