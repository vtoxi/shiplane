"""Single sign-on with any OpenID Connect provider (Google, Microsoft Entra, Okta, Keycloak, Authentik ...): authorization-code flow with PKCE.

* Sign-in mode only. Only emails that already have an account can sign in (the users table is the whitelist); SSO never creates accounts.
* The code is exchanged directly with the provider's token endpoint over TLS (plain http only for localhost, for tests). The id_token received that way
  is checked for issuer, audience, nonce and expiry; the email comes from it, or from the userinfo endpoint, and must be verified by the provider.
* The client secret lives in the owner-only settings file and is never sent back.
"""
import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

from . import settings

DEFAULTS = {'enabled': False, 'issuer': '', 'client_id': '', 'client_secret': '', 'label': 'SSO', 'redirect_base': ''}
PENDING, _lock = {}, threading.Lock()      # state -> {nonce, verifier, expires}; in memory: a restart only cancels sign-ins in progress
TTL, MAX_PENDING = 600, 200
LABEL_RE = re.compile(r'^[A-Za-z0-9 ._-]{1,30}$')
_disco = {}


def get():
    c = dict(DEFAULTS)
    c.update(settings.get('sso'))
    return c


def public():
    c = get()
    return {'enabled': c['enabled'], 'label': c['label'], 'issuer': c['issuer'], 'client_id': c['client_id'], 'has_secret': bool(c['client_secret']), 'redirect_base': c['redirect_base']}


def login_button():
    c = get()
    return {'label': c['label']} if c['enabled'] and c['issuer'] and c['client_id'] and c['client_secret'] else None


def _url_ok(url, what):
    p = urllib.parse.urlparse(url)
    if not (p.scheme == 'https' or (p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost'))) or not p.hostname or p.username or p.fragment:
        raise ValueError('%s must be an https address' % what)
    return url.rstrip('/')


def save(v):
    old = get()
    new = dict(old)
    new['enabled'] = bool(v.get('enabled'))
    new['issuer'] = _url_ok(str(v.get('issuer') or '').strip(), 'Issuer') if str(v.get('issuer') or '').strip() else ''
    new['client_id'] = str(v.get('client_id') or '').strip()[:200]
    if str(v.get('client_secret') or '').strip():
        new['client_secret'] = str(v['client_secret']).strip()[:400]
    label = str(v.get('label') or 'SSO').strip()
    if not LABEL_RE.match(label):
        raise ValueError('Button label: letters, numbers, spaces, . _ - (up to 30)')
    new['label'] = label
    base = str(v.get('redirect_base') or '').strip()
    new['redirect_base'] = _url_ok(base, 'Public address') if base else ''
    if new['enabled'] and not (new['issuer'] and new['client_id'] and new['client_secret']):
        raise ValueError('Issuer, client id and client secret are needed to turn SSO on')
    settings.put('sso', new)
    _disco.clear()
    return public()


def _http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data is not None else None, headers=dict({'Accept': 'application/json', 'User-Agent': 'shiplane'}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read(1 << 20).decode('utf-8', 'replace'))
    except OSError as e:
        raise RuntimeError('The sign-in provider did not answer correctly: %s' % e)
    except ValueError:
        raise RuntimeError('The sign-in provider sent something unexpected')


def discover(c, fetch=None):
    key = c['issuer']
    if key not in _disco:
        doc = (fetch or _http)(c['issuer'] + '/.well-known/openid-configuration')
        if str(doc.get('issuer', '')).rstrip('/') != c['issuer']:
            raise RuntimeError('The provider reports a different issuer than the one configured')
        for k in ('authorization_endpoint', 'token_endpoint'):
            _url_ok(str(doc.get(k) or ''), k)
        _disco[key] = doc
    return _disco[key]


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def start(redirect_uri, fetch=None):
    """URL to send the browser to."""
    c = get()
    if not login_button():
        raise ValueError('Single sign-on is not set up')
    doc = discover(c, fetch)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(16), secrets.token_urlsafe(48)
    with _lock:
        now = time.time()
        for k in [k for k, v in PENDING.items() if v['expires'] < now]:
            del PENDING[k]
        if len(PENDING) >= MAX_PENDING:
            raise PermissionError('Too many sign-ins in progress. Try again in a few minutes.')
        PENDING[state] = {'nonce': nonce, 'verifier': verifier, 'expires': now + TTL, 'redirect': redirect_uri}
    q = {'response_type': 'code', 'client_id': c['client_id'], 'redirect_uri': redirect_uri, 'scope': 'openid email', 'state': state, 'nonce': nonce,
         'code_challenge': _b64(hashlib.sha256(verifier.encode()).digest()), 'code_challenge_method': 'S256'}
    return doc['authorization_endpoint'] + ('&' if '?' in doc['authorization_endpoint'] else '?') + urllib.parse.urlencode(q)


def _claims(id_token):
    try:
        return json.loads(base64.urlsafe_b64decode(id_token.split('.')[1] + '=' * (-len(id_token.split('.')[1]) % 4)))
    except (ValueError, IndexError, AttributeError):
        return {}


def finish(code, state, fetch=None):
    """Exchange the code. Returns the provider-verified email, or raises ValueError (shown to the person) / RuntimeError."""
    with _lock:
        pending = PENDING.pop(str(state or ''), None)
    if not pending or pending['expires'] < time.time():
        raise ValueError('This sign-in link expired or was already used. Start again.')
    c = get()
    if not login_button():
        raise ValueError('Single sign-on is not set up')
    fetch = fetch or _http
    doc = discover(c, fetch)
    tok = fetch(doc['token_endpoint'], {'grant_type': 'authorization_code', 'code': str(code or ''), 'redirect_uri': pending['redirect'], 'client_id': c['client_id'],
                                         'client_secret': c['client_secret'], 'code_verifier': pending['verifier']})
    claims = _claims(tok.get('id_token', ''))
    aud = claims.get('aud')
    if claims:
        if str(claims.get('iss', '')).rstrip('/') != c['issuer'] or c['client_id'] not in (aud if isinstance(aud, list) else [aud]) \
                or claims.get('nonce') != pending['nonce'] or float(claims.get('exp', 0)) < time.time() - 60:
            raise ValueError('The sign-in answer did not match this request. Start again.')
    email, verified = claims.get('email'), claims.get('email_verified')
    if not email and doc.get('userinfo_endpoint') and tok.get('access_token'):
        info = fetch(doc['userinfo_endpoint'], None, {'Authorization': 'Bearer ' + str(tok['access_token'])})
        if claims.get('sub') and info.get('sub') != claims['sub']:
            raise ValueError('The sign-in answer did not match this request. Start again.')
        email, verified = info.get('email'), info.get('email_verified')
    if not email:
        raise ValueError('The provider did not share your email address')
    if verified not in (True, 'true'):
        raise ValueError('The provider has not verified that email address')
    return str(email).strip().lower()
