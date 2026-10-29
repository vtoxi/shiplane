"""History for the Metrics charts: CPU, memory, disk, load and network, one point per monitor round, kept for 24 hours.

Nothing extra runs: the monitor round already reads these numbers, this just remembers them (about 20 KB in memory and on disk, rewritten atomically).
Rates (CPU %, network bytes per second) come from the difference between two readings; a counter that went backwards (a reboot) leaves a gap
instead of a false spike.
"""
import json
import os
import threading
import time

from . import config

RANGES = {'1h': 3600, '6h': 6 * 3600, '24h': 24 * 3600}
_lock = threading.Lock()
_state = None            # {'last': {...}, 'points': [[t, cpu, ram, disk, load, rx, tx], ...]}, loaded on first use


def _file():
    return os.path.join(config.DATA, 'metrics.json')


def _load():
    global _state
    if _state is None:
        try:
            with open(_file(), encoding='utf-8') as f:
                s = json.load(f)
            ok = isinstance(s, dict) and isinstance(s.get('points'), list) and isinstance(s.get('last', {}), dict)
            _state = s if ok else {'last': {}, 'points': []}
        except (OSError, ValueError):
            _state = {'last': {}, 'points': []}
    return _state


def reset():
    """Forget everything in memory (tests, and after the data folder changes)."""
    global _state
    _state = None


def _pct(used, total):
    return round(100.0 * used / total, 1) if total else None


def record(m, keep=288, now=None):
    """Remember one reading. `m` is what remote/metrics.py prints. Returns the stored point."""
    now = time.time() if now is None else now
    with _lock:
        s = _load()
        last = s.get('last') or {}
        cpu = rx = tx = None
        dt = now - last['t'] if last.get('t') else 0
        if dt > 0:
            total = m['cpu_total'] - last.get('cpu_total', m['cpu_total'])
            idle = m['cpu_idle'] - last.get('cpu_idle', m['cpu_idle'])
            if total > 0 and idle >= 0:
                cpu = round(max(0.0, min(100.0, 100.0 * (1 - idle / total))), 1)
            drx, dtx = m['net_rx'] - last.get('net_rx', m['net_rx']), m['net_tx'] - last.get('net_tx', m['net_tx'])
            rx = int(drx / dt) if drx >= 0 else None
            tx = int(dtx / dt) if dtx >= 0 else None
        point = [int(now), cpu, _pct(m['ram_used'], m['ram_total']), _pct(m['disk_used'], m['disk_total']), round(float(m.get('load', 0)), 2), rx, tx]
        s['points'].append(point)
        del s['points'][:-max(2, int(keep))]
        s['last'] = {k: m[k] for k in ('cpu_total', 'cpu_idle', 'net_rx', 'net_tx')}
        s['last']['t'] = now
        os.makedirs(config.DATA, exist_ok=True)
        tmp = _file() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(s, f, separators=(',', ':'))
        os.replace(tmp, _file())
        return point


def history(span='24h', now=None):
    """Points of the last 1h / 6h / 24h, oldest first: [time, cpu %, memory %, disk %, load, bytes in/s, bytes out/s] (None = no reading)."""
    if span not in RANGES:
        raise ValueError('Choose one of: %s' % ', '.join(RANGES))
    now = time.time() if now is None else now
    with _lock:
        return [list(p) for p in _load()['points'] if now - p[0] <= RANGES[span]]
