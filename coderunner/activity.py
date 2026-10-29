"""Append-only activity log (data/activity.jsonl): who did what, when. Never stores secrets or values."""
import json
import os
import threading
import time

from . import config

_FILE = os.path.join(config.DATA, 'activity.jsonl')
_lock = threading.Lock()


def log(kind, target='', detail='', who='', ok=True):
    entry = {'t': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()) + ' UTC', 'kind': kind, 'target': target,
             'detail': detail, 'who': who, 'ok': bool(ok)}
    with _lock:
        os.makedirs(config.DATA, exist_ok=True)
        with open(_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')


def recent(n=200):
    try:
        with open(_FILE, encoding='utf-8') as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
