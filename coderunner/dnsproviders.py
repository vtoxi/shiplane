"""Other DNS providers next to Cloudflare: Amazon Route 53 and DigitalOcean.

They have no tunnel, so mapping a hostname means an A record pointing at this server's public IP, and the site is served directly
(Caddy's own HTTPS on 80/443, see dns.py Direct HTTPS). Like cloudflare.py it never overwrites a record that points somewhere else.
Credentials are pasted in Settings, checked against the provider, and kept only in dnsproviders.json (owner-only), never shown again.
Route 53 requests are signed with AWS Signature V4 using the standard library (no SDK).
"""
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode

from . import config

FILE = os.path.join(config.CONF, 'dnsproviders.json')
NAMES = {'route53': 'Amazon Route 53', 'digitalocean': 'DigitalOcean'}
R53_API = os.environ.get('R53_API_BASE', 'https://route53.amazonaws.com')
DO_API = os.environ.get('DO_API_BASE', 'https://api.digitalocean.com')
AWS_KEY_RE = re.compile(r'^[A-Z0-9]{16,128}$')
AWS_SECRET_RE = re.compile(r'^[A-Za-z0-9/+=]{30,128}$')
DO_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{20,100}$')
R53_NS = '{https://route53.amazonaws.com/doc/2013-04-01/}'
TTL = 300


# ------------------------------------------------------------------ storage
def _load():
    try:
        with open(FILE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    os.makedirs(config.CONF, exist_ok=True)
    fd = os.open(FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        config._owner_only(fd)
        json.dump(d, f, indent=2)


def connected():
    """The connected providers, in a fixed order: ['route53', 'digitalocean']."""
    d = _load()
    return [p for p in NAMES if d.get(p)]


def connection():
    """What the screen may know: which providers are connected and the IP records point at. Never a secret."""
    d = _load()
    return {'ip': d.get('ip', ''), 'providers': [{'id': p, 'name': NAMES[p], 'connected': bool(d.get(p)),
                                                  'key_id': (d.get(p) or {}).get('key_id', '')} for p in NAMES]}


def _ip(v):
    try:
        ip = ipaddress.ip_address(str(v or '').strip())
    except ValueError:
        raise ValueError('Type the public IPv4 address of this server (the address its A records point at)')
    if ip.version != 4 or ip.is_private or ip.is_loopback:
        raise ValueError('That is not a public IPv4 address')
    return str(ip)


def detect_ip(cfg):
    """The server's own public IPv4 (from its network interfaces), or '' when it has none (behind NAT: the person types it)."""
    from .runner import run
    try:
        rc, out = run(cfg, 'hostname -I', timeout=15)
    except Exception:
        return ''
    for word in out.split():
        try:
            if ipaddress.ip_address(word).version == 4 and _ip(word):
                return word
        except ValueError:
            pass
    return ''


def connect(provider, creds, ip):
    """Check the credentials by listing the zones, then save them with the server IP."""
    if provider not in NAMES:
        raise ValueError('Pick Route 53 or DigitalOcean')
    creds = creds or {}
    if provider == 'route53':
        c = {'key_id': str(creds.get('key_id') or '').strip(), 'secret': str(creds.get('secret') or '').strip()}
        if not AWS_KEY_RE.match(c['key_id']) or not AWS_SECRET_RE.match(c['secret']):
            raise ValueError('The access key id or secret looks wrong')
    else:
        c = {'token': str(creds.get('token') or '').strip()}
        if not DO_TOKEN_RE.match(c['token']):
            raise ValueError('That does not look like a DigitalOcean API token')
    ip = _ip(ip)
    zones = _zones(provider, c)                     # raises with the provider's own message when the credentials are wrong
    d = _load()
    d[provider] = c
    d['ip'] = ip
    _save(d)
    return dict(connection(), zones=sorted(zones))


def disconnect(provider):
    """Forget a provider's credentials. Records already made stay as they are."""
    d = _load()
    d.pop(provider, None)
    if not any(d.get(p) for p in NAMES):
        d.pop('ip', None)
    _save(d)
    return connection()


# ------------------------------------------------------------------ http
def _http(method, url, headers, body=None):
    """(status, bytes). Tests replace this function."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _sign(c, method, path, query, body, now=None, service='route53'):
    """AWS Signature V4 headers for a Route 53 request (global service, region us-east-1)."""
    now = now or datetime.datetime.utcnow()
    amz, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
    host = R53_API.split('://', 1)[1]
    payload = hashlib.sha256(body or b'').hexdigest()
    canon = '\n'.join([method, quote(path), query, 'host:%s\nx-amz-date:%s\n' % (host, amz), 'host;x-amz-date', payload])
    scope = '%s/us-east-1/%s/aws4_request' % (day, service)
    to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    k = ('AWS4' + c['secret']).encode()
    for part in (day, 'us-east-1', service, 'aws4_request'):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return {'X-Amz-Date': amz, 'Authorization': 'AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=host;x-amz-date, Signature=%s' % (c['key_id'], scope, sig)}


def _r53(c, method, path, params=None, body=None):
    query = urlencode(sorted((params or {}).items()), quote_via=quote)
    headers = _sign(c, method, path, query, body)
    if body:
        headers['Content-Type'] = 'text/xml'
    status, raw = _http(method, R53_API + path + ('?' + query if query else ''), headers, body)
    if status >= 300:
        m = re.search(rb'<Message>(.*?)</Message>', raw or b'')
        raise RuntimeError('Route 53: %s (%s %s)' % (m.group(1).decode() if m else 'HTTP %d' % status, method, path))
    return ET.fromstring(raw) if raw else None


def _do(c, method, path, data=None):
    status, raw = _http(method, DO_API + path, {'Authorization': 'Bearer ' + c['token'], 'Content-Type': 'application/json'},
                        None if data is None else json.dumps(data).encode())
    if status >= 300:
        try:
            msg = json.loads(raw).get('message')
        except ValueError:
            msg = ''
        raise RuntimeError('DigitalOcean: %s (%s %s)' % (msg or 'HTTP %d' % status, method, path.split('?')[0]))
    return json.loads(raw) if raw else {}


# ------------------------------------------------------------------ zones and records
def _zones(provider, c):
    """{domain: zone id} the credentials can see."""
    if provider == 'route53':
        root = _r53(c, 'GET', '/2013-04-01/hostedzone', {'maxitems': '100'})
        return {z.findtext(R53_NS + 'Name').rstrip('.'): z.findtext(R53_NS + 'Id').split('/')[-1]
                for z in root.iter(R53_NS + 'HostedZone') if z.findtext(R53_NS + 'Config/' + R53_NS + 'PrivateZone') != 'true'}
    return {d['name']: d['name'] for d in _do(c, 'GET', '/v2/domains?per_page=200').get('domains', [])}


def zone_names():
    """The domains of every connected provider, for the Deploy page's domain list."""
    d = _load()
    names = set()
    for p in connected():
        try:
            names.update(_zones(p, d[p]))
        except Exception:
            pass                                    # one broken provider must not hide the others' domains
    return sorted(names)


def _owner(host, d):
    """(provider, creds, zone name, zone id) of the most specific zone that holds host."""
    best = None
    for p in connected():
        for name, zid in _zones(p, d[p]).items():
            if (host == name or host.endswith('.' + name)) and (not best or len(name) > len(best[2])):
                best = (p, d[p], name, zid)
    if not best:
        raise RuntimeError('No connected DNS provider holds a zone for %s' % host)
    return best


def _get_a(p, c, zone, zid, host):
    """The current A record values for host."""
    if p == 'route53':
        root = _r53(c, 'GET', '/2013-04-01/hostedzone/%s/rrset' % zid, {'name': host + '.', 'type': 'A', 'maxitems': '1'})
        for rs in root.iter(R53_NS + 'ResourceRecordSet'):
            if rs.findtext(R53_NS + 'Name').rstrip('.') == host and rs.findtext(R53_NS + 'Type') == 'A':
                return [v.text for v in rs.iter(R53_NS + 'Value')]
        return []
    recs = _do(c, 'GET', '/v2/domains/%s/records?type=A&name=%s' % (zone, quote(host))).get('domain_records', [])
    return [r['data'] for r in recs]


def _r53_change(c, zid, action, host, ip):
    body = ('<?xml version="1.0" encoding="UTF-8"?><ChangeResourceRecordSetsRequest xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><ChangeBatch>'
            '<Changes><Change><Action>%s</Action><ResourceRecordSet><Name>%s.</Name><Type>A</Type><TTL>%d</TTL><ResourceRecords>'
            '<ResourceRecord><Value>%s</Value></ResourceRecord></ResourceRecords></ResourceRecordSet></Change></Changes></ChangeBatch>'
            '</ChangeResourceRecordSetsRequest>') % (action, host, TTL, ip)
    _r53(c, 'POST', '/2013-04-01/hostedzone/%s/rrset' % zid, body=body.encode())


def map_hosts(hosts):
    """Add an A record to this server for every host that has none. Lines to show the person."""
    d = _load()
    ip = d.get('ip')
    if not ip:
        raise RuntimeError('No DNS provider is connected')
    lines = []
    for host in hosts:
        p, c, zone, zid = _owner(host, d)
        have = _get_a(p, c, zone, zid, host)
        if have == [ip]:
            lines.append('%s: A %s already in %s' % (host, ip, NAMES[p]))
        elif have:
            lines.append('%s: SKIPPED, it already points at %s in %s (not overwritten)' % (host, ', '.join(have), NAMES[p]))
        else:
            if p == 'route53':
                _r53_change(c, zid, 'CREATE', host, ip)
            else:
                _do(c, 'POST', '/v2/domains/%s/records' % zone, {'type': 'A', 'name': '@' if host == zone else host[:-len(zone) - 1], 'data': ip, 'ttl': TTL})
            lines.append('%s: A %s added in %s' % (host, ip, NAMES[p]))
    return lines


def unmap_hosts(hosts):
    """Remove the A records that point at this server (only those)."""
    d = _load()
    ip = d.get('ip')
    lines = []
    for host in hosts:
        p, c, zone, zid = _owner(host, d)
        if _get_a(p, c, zone, zid, host) != [ip]:
            lines.append('%s: left alone (no A record to this server in %s)' % (host, NAMES[p]))
            continue
        if p == 'route53':
            _r53_change(c, zid, 'DELETE', host, ip)
        else:
            for r in _do(c, 'GET', '/v2/domains/%s/records?type=A&name=%s' % (zone, quote(host))).get('domain_records', []):
                _do(c, 'DELETE', '/v2/domains/%s/records/%s' % (zone, r['id']))
        lines.append('%s: A record removed from %s' % (host, NAMES[p]))
    return lines


def records(provider):
    """Every A record in a connected provider's zones: [{host, zone, values, here}], here = it points at this server."""
    d = _load()
    if provider not in connected():
        raise ValueError('%s is not connected' % NAMES.get(provider, provider))
    c, ip, out = d[provider], d.get('ip'), []
    for zone, zid in sorted(_zones(provider, c).items()):
        if provider == 'route53':
            root = _r53(c, 'GET', '/2013-04-01/hostedzone/%s/rrset' % zid, {'maxitems': '300'})
            found = [(rs.findtext(R53_NS + 'Name').rstrip('.'), [v.text for v in rs.iter(R53_NS + 'Value')])
                     for rs in root.iter(R53_NS + 'ResourceRecordSet') if rs.findtext(R53_NS + 'Type') == 'A']
        else:
            recs = _do(c, 'GET', '/v2/domains/%s/records?type=A&per_page=200' % zone).get('domain_records', [])
            by = {}
            for r in recs:
                by.setdefault(zone if r['name'] == '@' else '%s.%s' % (r['name'], zone), []).append(r['data'])
            found = sorted(by.items())
        out += [{'host': hst, 'zone': zone, 'values': vals, 'here': vals == [ip]} for hst, vals in found]
    return out
