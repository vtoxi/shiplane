"""Small JSON settings store (data/settings.json, owner-only): alert channels, backup schedule, security policy."""
import json
import os
import threading

from . import config

_lock = threading.RLock()


def _file():
    return os.path.join(config.DATA, 'settings.json')


def load():
    try:
        with open(_file(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get(section):
    return load().get(section) or {}


def put(section, value):
    with _lock:
        data = load()
        data[section] = value
        os.makedirs(config.DATA, exist_ok=True)
        tmp = _file() + '.tmp'
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, _file())
