"""API keys for programmatic access (for example a future mobile app): `Authorization: Bearer crk_...`.

Only a SHA-256 hash is stored (data/apikeys.json); the key itself is shown once when it is created.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from . import config

_FILE = os.path.join(config.DATA, 'apikeys.json')
_lock = threading.Lock()


def _load():
    try:
        with open(_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(keys):
    os.makedirs(config.DATA, exist_ok=True)
    with open(_FILE, 'w', encoding='utf-8') as f:
        json.dump(keys, f, indent=1)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


SCOPES = {'admin': 'Everything an admin can do', 'viewer': 'Read-only', 'deploy': 'Only deploy: upload a zip, start a deploy, follow its job and read the status (for CI)'}


def _public(k):
    return {'id': k['id'], 'name': k['name'], 'created': k['created'], 'last_used': k.get('last_used', ''),
            'scope': k.get('scope', 'admin'), 'expires': k.get('expires', 0)}


def create(name, scope='admin', days=0):
    """scope: admin / viewer / deploy. days: 0 = never expires."""
    name = str(name).strip()[:40]
    if scope not in SCOPES:
        raise ValueError('Scope must be one of: ' + ', '.join(SCOPES))
    days = int(days or 0)
    if not 0 <= days <= 3650:
        raise ValueError('Expiry must be 0 (never) to 3650 days')
    if not name:
        raise ValueError('Give the key a name, for example "phone"')
    token = 'crk_' + secrets.token_urlsafe(32)
    entry = {'id': secrets.token_hex(4), 'name': name, 'hash': _hash(token), 'created': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()), 'last_used': '',
             'scope': scope, 'expires': int(time.time()) + days * 86400 if days else 0}
    with _lock:
        keys = _load()
        keys.append(entry)
        _save(keys)
    return token, _public(entry)


def verify(token):
    """The key's public entry (with its scope) when the token is valid and not expired, else None."""
    h = _hash(token)
    with _lock:
        keys = _load()
        for k in keys:
            if hmac.compare_digest(k['hash'], h):
                if k.get('expires') and k['expires'] < time.time():
                    return None
                k['last_used'] = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
                _save(keys)
                return _public(k)
    return None


def list_keys():
    return [_public(k) for k in _load()]


def revoke(key_id):
    with _lock:
        keys = _load()
        left = [k for k in keys if k['id'] != key_id]
        if len(left) == len(keys):
            raise LookupError('no such key')
        _save(left)
