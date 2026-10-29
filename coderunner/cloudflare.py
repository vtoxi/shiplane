"""Cloudflare: map hostnames to the tunnel (a tunnel route -> Caddy on 127.0.0.1:80, plus a proxied CNAME).

Token needs: Account > Cloudflare Tunnel: Edit, Zone > Zone: Read, Zone > DNS: Edit.
It never overwrites an existing DNS record.
"""
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import quote

from . import config


def call(method, path, body=None, token=None):
    token = token or config.cloudflare()['api_token']
    if not token:
        raise RuntimeError('Cloudflare is not connected: connect it on Integrations')
    req = urllib.request.Request(config.CF_API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b'{}')
        except ValueError:
            data = {}
        if not data.get('errors'):
            data = {'success': False, 'errors': [{'message': 'HTTP %d' % e.code}]}
    if not data.get('success'):
        raise RuntimeError('Cloudflare: %s (%s %s)' % ('; '.join(x.get('message', '?') for x in data.get('errors', [])), method, path.split('?')[0]))
    return data.get('result')


# ------------------------------------------------------- connect / disconnect from the screen
ACCOUNT_RE = re.compile(r'^[0-9a-f]{32}$')
TUNNEL_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{20,100}$')


def connection():
    """What the screen may know: whether Cloudflare is connected, where the token comes from, which account and tunnel. Never the token."""
    c = config.cloudflare()
    return {'connected': config.cloudflare_ready(), 'source': 'environment' if os.environ.get('CLOUDFLARE_API_TOKEN') else 'file' if c['api_token'] else '',
            'account_id': c['account_id'], 'tunnel_id': c['tunnel_id']}


def discover(token, account_id=''):
    """Check a pasted token and list the accounts and tunnels it can see. Nothing is saved."""
    token = str(token or '').strip()
    if not TOKEN_RE.match(token):
        raise ValueError('That does not look like a Cloudflare API token')
    call('GET', '/user/tokens/verify', token=token)                 # raises with Cloudflare's own message when the token is wrong
    try:
        accounts = [{'id': a['id'], 'name': a.get('name', a['id'])} for a in call('GET', '/accounts?per_page=50', token=token) or []]
    except RuntimeError:
        accounts = []                                                # a token without "Account Settings: Read" cannot list accounts: the id can be typed
    account_id = str(account_id or '').strip() or (accounts[0]['id'] if len(accounts) == 1 else '')
    tunnels = []
    if account_id:
        if not ACCOUNT_RE.match(account_id):
            raise ValueError('The account id looks wrong (32 letters and digits)')
        tunnels = [{'id': t['id'], 'name': t.get('name', ''), 'status': t.get('status', '')}
                   for t in call('GET', '/accounts/%s/cfd_tunnel?is_deleted=false&per_page=50' % account_id, token=token) or []]
    return {'accounts': accounts, 'account_id': account_id, 'tunnels': tunnels}


def connect(token, account_id, tunnel_id):
    """Save the connection after proving the token can see that tunnel."""
    if os.environ.get('CLOUDFLARE_API_TOKEN'):
        raise ValueError('The token comes from the CLOUDFLARE_API_TOKEN environment variable of this app: change or remove it there')
    token, account_id, tunnel_id = str(token or '').strip(), str(account_id or '').strip(), str(tunnel_id or '').strip()
    if not TOKEN_RE.match(token) or not ACCOUNT_RE.match(account_id) or not TUNNEL_RE.match(tunnel_id):
        raise ValueError('The token, account or tunnel looks wrong')
    call('GET', '/accounts/%s/cfd_tunnel/%s' % (account_id, tunnel_id), token=token)
    config.save_cloudflare({'api_token': token, 'account_id': account_id, 'tunnel_id': tunnel_id})
    return connection()


def disconnect():
    """Forget the saved token. Mappings already made in Cloudflare stay as they are."""
    if os.environ.get('CLOUDFLARE_API_TOKEN'):
        raise ValueError('The token comes from the CLOUDFLARE_API_TOKEN environment variable of this app: remove it there')
    config.clear_cloudflare()
    return connection()


def covers(pattern, host):
    if pattern == host:
        return True
    if pattern.startswith('*.') and host.endswith(pattern[1:]):
        head = host[:-len(pattern) + 1]
        return bool(head) and '.' not in head
    return False


def zone_of(host, cache):
    parts = host.split('.')
    for i in range(len(parts) - 1):
        cand = '.'.join(parts[i:])
        if cand not in cache:
            z = call('GET', '/zones?name=' + quote(cand, safe=''))
            cache[cand] = (z[0]['id'], z[0]['name']) if z else None
        if cache[cand]:
            return cache[cand]
    raise RuntimeError('No Cloudflare zone for %s in this account (add the domain to Cloudflare first)' % host)


def zone_names():
    """The domains (zones) of this Cloudflare account, for the Deploy page's domain list."""
    return sorted(z['name'] for z in (call('GET', '/zones?per_page=50&status=active') or []))


def verify():
    """Is the token valid, and is the tunnel healthy? (read-only)"""
    c = config.cloudflare()
    tok = call('GET', '/user/tokens/verify') or {}
    info = {'token': tok.get('status', 'unknown')}
    try:
        t = call('GET', '/accounts/%s/cfd_tunnel/%s' % (c['account_id'], c['tunnel_id'])) or {}
        info['tunnel'] = {'name': t.get('name'), 'status': t.get('status'), 'connections': len(t.get('connections') or [])}
    except Exception as e:
        info['tunnel_error'] = str(e)
    return info


def tunnel_path():
    c = config.cloudflare()
    if not (c['account_id'] and c['tunnel_id']):
        raise RuntimeError('account_id and tunnel_id are missing in cloudflare.json')
    return c, '/accounts/%s/cfd_tunnel/%s/configurations' % (c['account_id'], c['tunnel_id'])


def state(hosts=None):
    """Route + DNS state per hostname (default: every hostname in the tunnel config)."""
    c, tpath = tunnel_path()
    target = c['tunnel_id'] + '.cfargotunnel.com'
    cfg = (call('GET', tpath) or {}).get('config') or {}
    ingress = cfg.get('ingress') or []
    rules = [r for r in ingress if r.get('hostname')]
    if hosts is None:
        hosts = [r['hostname'] for r in rules]
    zones, rows = {}, []
    for h in hosts:
        zid, zname = zone_of(h, zones)
        route = 'explicit' if any(r['hostname'] == h for r in rules) else 'wildcard' if any(covers(r['hostname'], h) for r in rules) else 'missing'
        recs = [r for r in (call('GET', '/zones/%s/dns_records?name=%s' % (zid, quote(h, safe=''))) or []) if r['type'] in ('A', 'AAAA', 'CNAME')]
        detail = ''
        if any(r['type'] == 'CNAME' and r['content'] == target for r in recs):
            dns = 'ok'
        elif recs:
            dns, detail = 'conflict', '%s %s' % (recs[0]['type'], recs[0]['content'])
        else:
            one = h != zname and '.' not in h[:-len(zname) - 1]
            wild = call('GET', '/zones/%s/dns_records?name=%s' % (zid, quote('*.' + zname, safe=''))) or []
            dns = 'wildcard' if one and any(r['type'] == 'CNAME' and r['content'] == target for r in wild) else 'missing'
        rows.append({'host': h, 'zone': zname, 'zid': zid, 'route': route, 'dns': dns, 'detail': detail,
                     'multilevel': h != zname and '.' in h[:-len(zname) - 1]})
    return c, cfg, ingress, rows


def describe(rows):
    out = []
    for r in rows:
        dns = {'ok': 'CNAME to the tunnel (ok)', 'wildcard': 'covered by the wildcard CNAME (ok)', 'missing': 'MISSING',
               'conflict': 'CONFLICT: already has %s' % r['detail']}[r['dns']]
        out.append('%s: tunnel route %s, DNS %s' % (r['host'], 'MISSING' if r['route'] == 'missing' else r['route'] + ' (ok)', dns))
        if r['multilevel']:
            out.append('  note: %s is more than one level under %s; HTTPS there needs Cloudflare Advanced Certificate Manager' % (r['host'], r['zone']))
    return out


def map_hosts(hosts):
    c, cfg, ingress, rows = state(hosts)
    tpath = tunnel_path()[1]
    out = []
    rules = [r for r in ingress if r.get('hostname')]
    catch = [r for r in ingress if not r.get('hostname')] or [{'service': 'http_status:404'}]
    add = [r['host'] for r in rows if r['route'] == 'missing']
    if add:
        cfg['ingress'] = rules + [{'hostname': h, 'service': 'http://127.0.0.1:80'} for h in add] + catch
        call('PUT', tpath, {'config': cfg})
        out += ['tunnel route added: ' + h for h in add]
    for r in rows:
        if r['dns'] == 'missing':
            call('POST', '/zones/%s/dns_records' % r['zid'], {'type': 'CNAME', 'name': r['host'], 'content': c['tunnel_id'] + '.cfargotunnel.com',
                                                              'proxied': True, 'ttl': 1, 'comment': 'created by Coderunner'})
            out.append('DNS CNAME created: ' + r['host'])
        elif r['dns'] == 'conflict':
            out.append('NOT changed: %s already has a DNS record (%s). Use Replace on Domains, Cloudflare tab (it asks first), or remove it in Cloudflare.' % (r['host'], r['detail']))
    return out or ['Nothing to change: already mapped']


def replace_dns(host, confirm):
    """Replace a conflicting A/AAAA/CNAME record of exactly this name by the tunnel's CNAME. Only called after the user typed the hostname to confirm.
    Other record types (MX, TXT, ...) and other names are never touched; the removed records are listed so they can be recreated by hand."""
    if confirm != host:
        raise ValueError('Type the hostname to confirm')
    if '*' in host:
        raise ValueError('Wildcard names cannot be replaced from here')
    row = state([host])[3][0]
    if row['dns'] != 'conflict':
        raise ValueError('%s has no conflicting DNS record any more: nothing was replaced' % host)
    out = []
    for r in call('GET', '/zones/%s/dns_records?name=%s' % (row['zid'], quote(host, safe=''))) or []:
        if r['type'] in ('A', 'AAAA', 'CNAME'):
            call('DELETE', '/zones/%s/dns_records/%s' % (row['zid'], r['id']))
            out.append('DNS record removed: %s %s %s (was %s)' % (host, r['type'], r['content'], 'proxied' if r.get('proxied') else 'DNS only'))
    return out + map_hosts([host])


def unmap_hosts(hosts):
    c, tpath = tunnel_path()
    target = c['tunnel_id'] + '.cfargotunnel.com'
    cfg = (call('GET', tpath) or {}).get('config') or {}
    ingress = cfg.get('ingress') or []
    keep = [r for r in ingress if r.get('hostname') not in hosts]
    out = ['tunnel route removed: ' + r['hostname'] for r in ingress if r.get('hostname') in hosts]
    if out:
        if not any(not r.get('hostname') for r in keep):
            keep.append({'service': 'http_status:404'})
        cfg['ingress'] = keep
        call('PUT', tpath, {'config': cfg})
    zones = {}
    for h in hosts:
        zid, _ = zone_of(h, zones)
        for r in call('GET', '/zones/%s/dns_records?name=%s' % (zid, quote(h, safe=''))) or []:
            if r['type'] == 'CNAME' and r['content'] == target:
                call('DELETE', '/zones/%s/dns_records/%s' % (zid, r['id']))
                out.append('DNS CNAME removed: ' + h)
    return out or ['Nothing to remove in Cloudflare']
