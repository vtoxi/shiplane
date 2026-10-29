"""Paths, validation patterns and the two small config files (server.json, cloudflare.json)."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
REMOTE = os.path.join(ROOT, 'coderunner', 'remote')
DATA = os.environ.get('CODERUNNER_DATA') or os.path.join(ROOT, 'data')  # activity log and API key hashes
CONF = os.environ.get('CODERUNNER_CONFIG') or ROOT          # where server.json and cloudflare.json live (a volume in Docker)
SERVER_FILE = os.path.join(CONF, 'server.json')
CF_FILE = os.path.join(CONF, 'cloudflare.json')
CF_API = os.environ.get('CF_API_BASE', 'https://api.cloudflare.com/client/v4')

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}$')
HOST_RE = re.compile(r'^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$')
REL_RE = re.compile(r'^\d{8}-\d{6}$')
DB_RE = re.compile(r'^[a-z][a-z0-9_]{0,40}$')
KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
DBPW_RE = re.compile(r'^[A-Za-z0-9._~@#%+=:,^*!-]{8,64}$')     # no quotes, $, backslash or spaces: safe inside the SQL and shell scripts


def db_login(name, user=None, password=None):
    """(user, password) for a new database: the user's choice, validated, or the database name and None (generate one)."""
    user = str(user or '').strip() or name
    if not DB_RE.match(user):
        raise ValueError('User name: start with a letter, then a-z, 0-9 and _ only')
    password = str(password or '')
    if password and not DBPW_RE.match(password):
        raise ValueError('Password: 8 to 64 characters; letters, digits and . _ ~ @ # % + = : , ^ * ! - only')
    return user, password or None
DEFAULT_SERVER = {'mode': 'ssh', 'auth': 'key', 'host': '', 'port': 22, 'user': '', 'key': '~/.ssh/id_ed25519', 'domains': []}


def load_server():
    cfg = dict(DEFAULT_SERVER)
    try:
        with open(SERVER_FILE, encoding='utf-8') as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def check_server(c):
    mode = str(c.get('mode', 'ssh')).strip()
    if mode not in ('ssh', 'local'):
        raise ValueError('Mode must be ssh or local')
    auth = 'password' if c.get('auth') == 'password' else 'key'
    host, user, key = str(c.get('host', '')).strip(), str(c.get('user', '')).strip(), str(c.get('key', '')).strip()
    password = str(c.get('password') or '')
    try:
        port = int(c.get('port') or 22)
    except (TypeError, ValueError):
        port = 0
    if mode == 'ssh':
        if not 1 <= port <= 65535:
            raise ValueError('SSH port must be a number between 1 and 65535')
        if not re.match(r'^[A-Za-z0-9.-]+$', host):
            raise ValueError('Server host looks wrong')
        if not re.match(r'^[a-z_][a-z0-9_-]*$', user):
            raise ValueError('Server user looks wrong')
        if auth == 'key' and (not key or key.startswith('-')):
            raise ValueError('SSH key path looks wrong')
        if auth == 'password' and not password:
            raise ValueError('Type the SSH password')
    doms = [str(d).strip().lower() for d in c.get('domains', []) if str(d).strip()]
    for d in doms:
        if not HOST_RE.match(d):
            raise ValueError('Bad domain: ' + d)
    return {'mode': mode, 'auth': auth, 'host': host, 'port': port if mode == 'ssh' else 22, 'user': user, 'key': key if auth == 'key' else '',
            'password': password if auth == 'password' else '', 'domains': doms}


def resolve_server(c):
    """A submitted connection with the saved password filled in when the field was left empty (the password is write-only)."""
    c = dict(c)
    saved = load_server()
    if c.get('auth') == 'password' and not c.get('password') and saved.get('password') and (c.get('host'), c.get('user')) == (saved.get('host'), saved.get('user')):
        c['password'] = saved['password']
    return check_server(c)


def public_server(c):
    """The connection settings as shown in the UI: the password is never sent back."""
    out = {k: v for k, v in c.items() if k != 'password'}
    out['hasPassword'] = bool(c.get('password'))
    return out


def save_server(c):
    data = resolve_server(c)
    os.makedirs(CONF, exist_ok=True)
    fd = os.open(SERVER_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # may hold a password: owner-only
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def cloudflare():
    """Cloudflare settings. The token may come from the CLOUDFLARE_API_TOKEN environment variable."""
    c = {'api_token': '', 'account_id': '', 'tunnel_id': ''}
    try:
        with open(CF_FILE, encoding='utf-8') as f:
            c.update(json.load(f))
    except (OSError, ValueError):
        pass
    c['api_token'] = os.environ.get('CLOUDFLARE_API_TOKEN') or str(c.get('api_token') or '').strip()
    return c


def save_cloudflare(c):
    """Write cloudflare.json (owner-only: it holds the API token)."""
    os.makedirs(CONF, exist_ok=True)
    fd = os.open(CF_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({k: c[k] for k in ('api_token', 'account_id', 'tunnel_id')}, f, indent=2)


def clear_cloudflare():
    try:
        os.remove(CF_FILE)
    except FileNotFoundError:
        pass


def cloudflare_ready():
    c = cloudflare()
    return bool(c['api_token'] and c['account_id'] and c['tunnel_id'])


def clean_hosts(v):
    hosts = [str(h).strip().lower() for h in (v if isinstance(v, list) else str(v).replace('\n', ',').split(',')) if str(h).strip()]
    if not hosts:
        raise ValueError('Type at least one hostname')
    for h in hosts:
        if not HOST_RE.match(h) or '.' not in h:
            raise ValueError('bad hostname: ' + h)
    return hosts
