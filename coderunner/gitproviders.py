"""Sign in to GitHub or GitLab to pick a repository (including private ones) and connect it in one click.

Two ways in: the device login (the user types a short code on github.com / gitlab.com; needs an OAuth app client id from Settings or the
environment) or a pasted read-only token. The token lives in memory only, for at most 30 minutes, and is forgotten as soon as the repository
is connected. Connecting adds a read-only deploy key and a push webhook to the repository; the server then pulls over SSH with that key.
"""
import base64
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import settings

TTL, MAX_SESSIONS, MAX_BYTES = 30 * 60, 5, 2_000_000
REPO_RE = re.compile(r'^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){1,5}$')
BRANCH_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$')
PROVIDERS = {
    'github': {'name': 'GitHub', 'web': 'https://github.com', 'api': 'https://api.github.com', 'env': 'SHIPLANE_GITHUB_CLIENT_ID', 'scope': 'repo admin:repo_hook'},
    'gitlab': {'name': 'GitLab', 'web': 'https://gitlab.com', 'api': 'https://gitlab.com/api/v4', 'env': 'SHIPLANE_GITLAB_CLIENT_ID', 'scope': 'api'},
}
_lock = threading.RLock()
SESSIONS = {}          # id -> {'provider', 'token', 'user', 'at', 'device'}


# -------------------------------------------------------------------------- http
def fetch(method, url, headers=None, data=None, form=False):
    """One bounded JSON request: (status, parsed body). Replaced in tests."""
    raw = None
    h = {'User-Agent': 'shiplane', 'Accept': 'application/json'}
    h.update(headers or {})
    if data is not None:
        raw = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
        h['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, raw, h, method=method), timeout=15) as r:
            code, body = r.status, r.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read(MAX_BYTES)
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError('Could not reach %s: %s' % (urllib.parse.urlsplit(url).netloc, getattr(e, 'reason', e)))
    try:
        return code, json.loads(body.decode('utf-8', 'replace') or 'null')
    except ValueError:
        return code, None


def _provider(pid):
    if pid not in PROVIDERS:
        raise ValueError('Unknown provider')
    return PROVIDERS[pid]


def client_id(pid):
    return os.environ.get(_provider(pid)['env']) or str(settings.get('git_oauth').get(pid) or '')


def save_client_ids(values):
    clean = {}
    for pid in PROVIDERS:
        v = str((values or {}).get(pid) or '').strip()
        if v and not re.fullmatch(r'[A-Za-z0-9._-]{4,100}', v):
            raise ValueError('%s client id looks wrong' % PROVIDERS[pid]['name'])
        clean[pid] = v
    settings.put('git_oauth', clean)


def providers():
    saved = settings.get('git_oauth')
    return [{'id': k, 'name': v['name'], 'device': bool(client_id(k)), 'client_id': str(saved.get(k) or ''), 'from_env': bool(os.environ.get(v['env']))} for k, v in PROVIDERS.items()]


# ---------------------------------------------------------------------- sessions
def _purge(now):
    for k in [k for k, v in SESSIONS.items() if now - v['at'] > TTL]:
        SESSIONS.pop(k, None)
    while len(SESSIONS) > MAX_SESSIONS:
        SESSIONS.pop(min(SESSIONS, key=lambda k: SESSIONS[k]['at']), None)


def _new(pid, **extra):
    now = time.time()
    with _lock:
        _purge(now)
        if len(SESSIONS) >= MAX_SESSIONS:
            SESSIONS.pop(min(SESSIONS, key=lambda k: SESSIONS[k]['at']), None)
        sid = secrets.token_urlsafe(18)
        SESSIONS[sid] = dict({'provider': pid, 'token': '', 'user': '', 'at': now, 'device': None}, **extra)
        return sid


def _get(sid):
    with _lock:
        _purge(time.time())
        s = SESSIONS.get(str(sid))
    if not s:
        raise LookupError('Your sign-in expired: sign in again')
    s['at'] = time.time()          # in use: the 30 minutes start again
    return s


def forget(sid):
    with _lock:
        SESSIONS.pop(str(sid), None)


def _auth(s):
    return {'Authorization': 'Bearer ' + s['token']}      # both services accept a bearer token


def _api(s, method, path, data=None):
    code, body = fetch(method, PROVIDERS[s['provider']]['api'] + path, _auth(s), data)
    if code in (401, 403):
        raise PermissionError('%s refused the request (%s): the sign-in or token may lack permission' % (PROVIDERS[s['provider']]['name'], code))
    return code, body


# ------------------------------------------------------------------------ sign in
def device_start(pid):
    p, cid = _provider(pid), client_id(pid)
    if not cid:
        raise ValueError('Device login is not set up for %s: add its OAuth client id in Settings, or paste an access token instead' % p['name'])
    path = '/login/device/code' if pid == 'github' else '/oauth/authorize_device'
    code, body = fetch('POST', p['web'] + path, data={'client_id': cid, 'scope': p['scope']}, form=True)
    if code != 200 or not isinstance(body, dict) or 'device_code' not in body:
        raise RuntimeError('%s did not start the login (%s)' % (p['name'], code))
    sid = _new(pid, device={'code': body['device_code'], 'interval': int(body.get('interval') or 5)})
    return {'session': sid, 'user_code': body.get('user_code'), 'url': body.get('verification_uri'), 'interval': int(body.get('interval') or 5), 'expires_in': int(body.get('expires_in') or 900)}


def device_poll(sid):
    s = _get(sid)
    if s['token']:
        return {'status': 'ok', 'user': s['user']}
    p, d = PROVIDERS[s['provider']], s['device']
    if not d:
        raise ValueError('This sign-in has no device login running')
    path = '/login/oauth/access_token' if s['provider'] == 'github' else '/oauth/token'
    code, body = fetch('POST', p['web'] + path, data={'client_id': client_id(s['provider']), 'device_code': d['code'], 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'}, form=True)
    body = body if isinstance(body, dict) else {}
    if body.get('access_token'):
        s['token'], s['device'] = body['access_token'], None
        s['user'] = _whoami(s)
        return {'status': 'ok', 'user': s['user']}
    err = body.get('error', '')
    if err in ('authorization_pending', 'slow_down', ''):
        return {'status': 'pending', 'slow': err == 'slow_down'}
    forget(sid)
    return {'status': 'expired' if err == 'expired_token' else 'denied'}


def use_token(pid, token):
    _provider(pid)
    token = str(token or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{10,255}', token):
        raise ValueError('That does not look like an access token')
    sid = _new(pid, token=token)
    s = _get(sid)
    try:
        s['user'] = _whoami(s)
    except Exception:
        forget(sid)
        raise
    return {'session': sid, 'user': s['user']}


def _whoami(s):
    code, body = fetch('GET', PROVIDERS[s['provider']]['api'] + '/user', _auth(s))
    if code != 200 or not isinstance(body, dict):
        raise PermissionError('%s did not accept this sign-in (%s)' % (PROVIDERS[s['provider']]['name'], code))
    return str(body.get('login') or body.get('username') or '')


# ------------------------------------------------------------------ repositories
def _repo(s, repo):
    if not REPO_RE.match(str(repo)) or '..' in repo:
        raise ValueError('The repository name looks wrong')
    return repo


def _pid(s, repo):
    return urllib.parse.quote(repo, safe='') if s['provider'] == 'gitlab' else repo


def repos(sid):
    s = _get(sid)
    if s['provider'] == 'github':
        code, body = _api(s, 'GET', '/user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member')
        rows = [(r['full_name'], r['private'], r['default_branch']) for r in body or [] if isinstance(r, dict) and 'full_name' in r]
    else:
        code, body = _api(s, 'GET', '/projects?membership=true&per_page=100&order_by=last_activity_at&simple=true')
        rows = [(r['path_with_namespace'], r.get('visibility') != 'public', r.get('default_branch') or 'main') for r in body or [] if isinstance(r, dict) and 'path_with_namespace' in r]
    return [{'repo': n, 'private': bool(pr), 'branch': b or 'main'} for n, pr, b in rows if REPO_RE.match(n)]


def branches(sid, repo):
    s = _get(sid)
    _repo(s, repo)
    path = '/repos/%s/branches?per_page=100' % repo if s['provider'] == 'github' else '/projects/%s/repository/branches?per_page=100' % _pid(s, repo)
    code, body = _api(s, 'GET', path)
    return [b['name'] for b in body or [] if isinstance(b, dict) and BRANCH_RE.match(str(b.get('name', '')))]


def clone_address(sid, repo):
    """The SSH address the server will pull from (the deploy key makes it work for private repositories)."""
    s = _get(sid)
    return 'git@%s:%s.git' % (urllib.parse.urlsplit(PROVIDERS[s['provider']]['web']).netloc, _repo(s, repo))


def _file(s, repo, branch, name):
    ref = urllib.parse.quote(branch, safe='')
    if s['provider'] == 'github':
        code, body = _api(s, 'GET', '/repos/%s/contents/%s?ref=%s' % (repo, name, ref))
        if code == 200 and isinstance(body, dict) and body.get('encoding') == 'base64':
            return base64.b64decode(body.get('content', '')).decode('utf-8', 'replace')
        return ''
    code, body = _api(s, 'GET', '/projects/%s/repository/files/%s?ref=%s' % (_pid(s, repo), urllib.parse.quote(name, safe=''), ref))
    if code == 200 and isinstance(body, dict):
        return base64.b64decode(body.get('content', '')).decode('utf-8', 'replace')
    return ''


def _top(s, repo, branch):
    ref = urllib.parse.quote(branch, safe='')
    path = '/repos/%s/contents?ref=%s' % (repo, ref) if s['provider'] == 'github' else '/projects/%s/repository/tree?ref=%s&per_page=100' % (_pid(s, repo), ref)
    code, body = _api(s, 'GET', path)
    return {str(x.get('name')) for x in body or [] if isinstance(x, dict)}


def detect(sid, repo, branch):
    """Suggest how to deploy a repository from its top-level files: Docker, Node (static build or server), PHP or plain static."""
    s = _get(sid)
    _repo(s, repo)
    if not BRANCH_RE.match(str(branch)):
        raise ValueError('The branch name looks wrong')
    files = _top(s, repo, branch)
    out = {'type': 'static', 'build': 'none', 'build_cmd': '', 'output': '', 'start': '', 'port': 3000, 'cport': 8080, 'dockerfile': 'Dockerfile', 'why': 'No build files found: serving the files as they are.'}
    if 'Dockerfile' in files:
        m = re.search(r'^\s*EXPOSE\s+(\d{1,5})', _file(s, repo, branch, 'Dockerfile'), re.M | re.I)
        return dict(out, type='docker', cport=int(m.group(1)) if m else 8080, why='A Dockerfile was found: it will be built and run as a container.')
    if 'package.json' in files:
        try:
            pkg = json.loads(_file(s, repo, branch, 'package.json') or '{}')
        except ValueError:
            pkg = {}
        if not isinstance(pkg, dict):
            pkg = {}
        scripts = pkg.get('scripts') or {}
        deps = {**(pkg.get('dependencies') or {}), **(pkg.get('devDependencies') or {})}
        if 'build' in scripts and 'start' not in scripts:
            out_dir = 'dist' if 'vite' in deps else ('build' if 'react-scripts' in deps else 'dist')
            return dict(out, build='npm', output=out_dir, why='A package.json with a build script and no start script: built, then %s/ is served as a static site.' % out_dir)
        if 'start' in scripts:
            return dict(out, type='command', build_cmd='npm ci --omit=dev || npm install --omit=dev', start='npm start',
                        why='A package.json with a start script: it runs as a service (npm start).')
        return dict(out, type='node', start=str(pkg.get('main') or 'index.js'), why='A package.json: it runs with Node.')
    if 'composer.json' in files or 'index.php' in files:
        return dict(out, type='php', why='PHP files were found.')
    if 'requirements.txt' in files:
        return dict(out, type='command', build_cmd='python3 -m venv .venv && .venv/bin/pip install -r requirements.txt', start='.venv/bin/python app.py', why='A Python project: edit the start command if your entry file is not app.py.')
    return out


# ------------------------------------------------------------------ one-click connect
def link(sid, repo, public_key, hook_url, secret, name):
    """Add the read-only deploy key and the push webhook. Returns what was done; the webhook is skipped (with the reason) when the portal is not public HTTPS."""
    s = _get(sid)
    _repo(s, repo)
    done = {'key': False, 'hook': False, 'hook_note': ''}
    title = 'shiplane-%s' % name
    if s['provider'] == 'github':
        code, body = _api(s, 'POST', '/repos/%s/keys' % repo, {'title': title, 'key': public_key, 'read_only': True})
    else:
        code, body = _api(s, 'POST', '/projects/%s/deploy_keys' % _pid(s, repo), {'title': title, 'key': public_key, 'can_push': False})
    if code not in (200, 201) and not (code == 422 and 'already' in json.dumps(body).lower()):
        raise RuntimeError('Could not add the deploy key (%s): %s' % (code, str((body or {}).get('message', ''))[:150] if isinstance(body, dict) else ''))
    done['key'] = True
    if not str(hook_url).startswith('https://'):
        done['hook_note'] = 'The webhook was not added: this portal is not reachable over public HTTPS, so GitHub/GitLab could not call it. Use Deploy now, or publish the portal and add the webhook later.'
    else:
        if s['provider'] == 'github':
            code, body = _api(s, 'POST', '/repos/%s/hooks' % repo, {'name': 'web', 'active': True, 'events': ['push'], 'config': {'url': hook_url, 'content_type': 'json', 'secret': secret}})
        else:
            code, body = _api(s, 'POST', '/projects/%s/hooks' % _pid(s, repo), {'url': hook_url, 'push_events': True, 'token': secret, 'enable_ssl_verification': True})
        done['hook'] = code in (200, 201)
        if not done['hook']:
            done['hook_note'] = 'The webhook could not be added (%s): deploy with the button, or add it by hand.' % code
    forget(sid)                                                   # the token has done its job
    return done
