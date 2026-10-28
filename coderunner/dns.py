"""Your own nameservers: zones and records are kept here, rendered to zone files and served by NSD on the server (a small authoritative DNS server).

Nothing happens on the server until you press Apply, and the firewall is only changed by a separate, confirmed action. A zone file is checked with
nsd-checkzone before anything is replaced, so a typo cannot take DNS down. Direct HTTPS (a site answering on 80/443 itself instead of through the
Cloudflare tunnel) is a per-site switch, also confirmed.
"""
import datetime
import ipaddress
import re
import secrets
import threading

from . import deploy, settings
from .runner import q, run, script

TYPES = ('A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS')
DOMAIN_RE = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
LABEL = r'[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?'
NAME_RE = re.compile(r'^(@|(\*\.)?%s(\.%s)*)$' % (LABEL, LABEL))
TXT_RE = re.compile(r'^[\x20-\x21\x23-\x5b\x5d-\x7e]{1,1000}$')           # printable ASCII without " and \
EMAIL_RE = re.compile(r'^[A-Za-z0-9._+-]{1,64}@([a-z0-9-]+\.)+[a-z]{2,63}$')
MAX_ZONES, MAX_RECORDS = 20, 200
PORT_SETS = {'dns': ('53/tcp', '53/udp'), 'web': ('80/tcp', '443/tcp')}
_lock = threading.RLock()


# ------------------------------------------------------------------- the store
def _store():
    s = settings.get('dns')
    return {'ns': list(s.get('ns') or []), 'admin': s.get('admin') or '', 'zones': {k: v for k, v in (s.get('zones') or {}).items() if isinstance(v, dict)}}


def _save(s):
    settings.put('dns', s)


def _zone(s, domain):
    domain = str(domain or '').strip().lower().rstrip('.')
    if domain not in s['zones']:
        raise LookupError('There is no zone called %s' % domain)
    return domain, s['zones'][domain]


def host(value, label='Hostname'):
    v = str(value or '').strip().lower().rstrip('.')
    if not DOMAIN_RE.match(v):
        raise ValueError('%s looks wrong (example: ns1.example.com)' % label)
    return v


def view():
    s = _store()
    return {'ns': s['ns'], 'admin': s['admin'], 'zones': [{'domain': d, 'records': len(z.get('records', [])), 'serial': z.get('serial')} for d, z in sorted(s['zones'].items())]}


def records(domain):
    d, z = _zone(_store(), domain)
    return {'domain': d, 'records': z.get('records', []), 'serial': z.get('serial')}


def set_nameservers(body):
    """One or two nameserver names (two are strongly advised) and the contact address that goes into the zone's SOA record."""
    ns = []
    for n in (body.get('ns') or [])[:2]:
        if str(n or '').strip():
            ns.append(host(n, 'Nameserver'))
    if not ns or len(set(ns)) != len(ns):
        raise ValueError('Give one or two different nameserver names (example: ns1.example.com and ns2.example.com)')
    admin = str(body.get('admin') or '').strip()
    if admin and not EMAIL_RE.match(admin):
        raise ValueError('The contact address looks wrong')
    with _lock:
        s = _store()
        s['ns'], s['admin'] = ns, admin
        _save(s)
    return view()


# ----------------------------------------------------------------- the records
def _bump(z):
    today = int(datetime.date.today().strftime('%Y%m%d00'))
    z['serial'] = max(int(z.get('serial') or 0) + 1, today)


def add_zone(domain, ip):
    """A zone with the usual starting records: the nameservers, the bare name and www at the server's address."""
    domain = host(domain, 'Domain')
    try:
        ip = str(ipaddress.IPv4Address(str(ip).strip()))
    except ValueError:
        raise ValueError('The server address must be an IPv4 address')
    with _lock:
        s = _store()
        if not s['ns']:
            raise ValueError('Set the nameserver names first')
        if domain in s['zones']:
            raise ValueError('%s already has a zone' % domain)
        if len(s['zones']) >= MAX_ZONES:
            raise ValueError('At most %d zones' % MAX_ZONES)
        recs = [{'name': '@', 'type': 'NS', 'value': n, 'ttl': 3600} for n in s['ns']]
        recs += [{'name': '@', 'type': 'A', 'value': ip, 'ttl': 300}, {'name': 'www', 'type': 'A', 'value': ip, 'ttl': 300}]
        for n in s['ns']:                                                      # glue: a nameserver inside the zone needs its own address
            if n.endswith('.' + domain):
                recs.append({'name': n[:-len(domain) - 1], 'type': 'A', 'value': ip, 'ttl': 3600})
        s['zones'][domain] = {'serial': 0, 'records': [dict(r, id=secrets.token_hex(4)) for r in recs]}
        _bump(s['zones'][domain])
        _save(s)
    return records(domain)


def delete_zone(domain, confirm):
    with _lock:
        s = _store()
        d, _ = _zone(s, domain)
        if confirm != d:
            raise ValueError('Type the domain to confirm')
        del s['zones'][d]
        _save(s)


def _value(typ, value, domain):
    v = str(value or '').strip()
    if typ == 'A':
        try:
            return str(ipaddress.IPv4Address(v))
        except ValueError:
            raise ValueError('An A record needs an IPv4 address')
    if typ == 'AAAA':
        try:
            return str(ipaddress.IPv6Address(v))
        except ValueError:
            raise ValueError('An AAAA record needs an IPv6 address')
    if typ == 'TXT':
        if not TXT_RE.match(v):
            raise ValueError('Text records may use printable characters except " and \\ (at most 1000)')
        return v
    return host(v, 'The target')                                               # CNAME, MX, NS point to a hostname


def put_record(domain, body):
    """Add a record (no id) or change one (with its id). Returns the zone."""
    typ = str(body.get('type') or '').upper()
    if typ not in TYPES:
        raise ValueError('Record type must be one of ' + ', '.join(TYPES))
    with _lock:
        s = _store()
        d, z = _zone(s, domain)
        name = str(body.get('name') or '@').strip().lower().rstrip('.')
        if name.endswith('.' + d):
            name = name[:-len(d) - 1]
        if name == d:
            name = '@'
        if not NAME_RE.match(name):
            raise ValueError('The name looks wrong (use @ for the bare domain, or www, mail, *.apps ...)')
        try:
            ttl = int(body.get('ttl') or 300)
        except (TypeError, ValueError):
            raise ValueError('TTL must be a number of seconds')
        if not 60 <= ttl <= 86400:
            raise ValueError('TTL must be between 60 and 86400 seconds')
        rec = {'name': name, 'type': typ, 'value': _value(typ, body.get('value'), d), 'ttl': ttl}
        if typ == 'MX':
            try:
                rec['prio'] = int(body.get('prio') if body.get('prio') not in (None, '') else 10)
            except (TypeError, ValueError):
                raise ValueError('Priority must be a number')
            if not 0 <= rec['prio'] <= 65535:
                raise ValueError('Priority must be between 0 and 65535')
        rid = str(body.get('id') or '')
        others = [r for r in z['records'] if r['id'] != rid]
        if rid and len(others) == len(z['records']):
            raise LookupError('That record does not exist any more')
        same = [r for r in others if r['name'] == name]
        if typ == 'CNAME' and (name == '@' or same):
            raise ValueError('A CNAME cannot share its name with another record, and cannot be the bare domain')
        if any(r['type'] == 'CNAME' for r in same):
            raise ValueError('%s is a CNAME: it cannot have other records' % name)
        if any(r['type'] == typ and r['value'] == rec['value'] and r.get('prio') == rec.get('prio') for r in same):
            raise ValueError('That record already exists')
        if len(others) >= MAX_RECORDS:
            raise ValueError('At most %d records per zone' % MAX_RECORDS)
        z['records'] = others + [dict(rec, id=rid or secrets.token_hex(4))]
        _bump(z)
        _save(s)
    return records(d)


def delete_record(domain, rid):
    with _lock:
        s = _store()
        d, z = _zone(s, domain)
        keep = [r for r in z['records'] if r['id'] != rid]
        if len(keep) == len(z['records']):
            raise LookupError('That record does not exist any more')
        z['records'] = keep
        _bump(z)
        _save(s)
    return records(d)


# ------------------------------------------------------------------- zone files
def _fqdn(v):
    return v + '.'


def render(domain):
    """The zone file text. Plain, validated values only, so it can be written to the server inside a quoted heredoc."""
    s = _store()
    d, z = _zone(s, domain)
    if not s['ns']:
        raise ValueError('Set the nameserver names first')
    rname = ('hostmaster.%s' % d) if not s['admin'] else '%s.%s' % (s['admin'].split('@')[0].replace('.', '\\.'), s['admin'].split('@')[1])
    lines = ['$ORIGIN %s.' % d, '$TTL 300', '@ IN SOA %s. %s. %d 7200 3600 1209600 300' % (s['ns'][0], rname, z['serial'])]
    for r in sorted(z['records'], key=lambda r: (r['name'] != '@', r['name'], r['type'], r['value'])):
        if r['type'] == 'TXT':
            chunks = ' '.join('"%s"' % r['value'][i:i + 255] for i in range(0, len(r['value']), 255))
            data = chunks
        elif r['type'] == 'MX':
            data = '%d %s' % (r.get('prio', 10), _fqdn(r['value']))
        elif r['type'] in ('CNAME', 'NS'):
            data = _fqdn(r['value'])
        else:
            data = r['value']
        lines.append('%s %d IN %s %s' % (r['name'], r['ttl'], r['type'], data))
    return '\n'.join(lines) + '\n'


def apply_script():
    s = _store()
    if not s['ns']:
        raise ValueError('Set the nameserver names first')
    if not s['zones']:
        raise ValueError('Add a zone first')
    blocks = []
    for d in sorted(s['zones']):
        mark = 'ZONE_' + secrets.token_hex(6).upper()
        blocks.append('cat > "$STAGE/%s.zone" <<\'%s\'\n%s%s' % (d, mark, render(d), mark))
    return script('dns_apply.sh').replace('__DOMAINS__', ' '.join(sorted(s['zones']))).replace('__ZONES__', '\n'.join(blocks))


def apply(cfg, user):
    """Write the zones to the server and (re)start NSD. Progress is a normal job. Returns {'job': id}."""
    text = apply_script()

    def work(job):
        rc = deploy.stream_script(job, cfg, text)
        job.ok = rc == 0
        job.say('DONE: DNS is being served' if job.ok else 'FAILED (exit code %s): nothing was changed on the server' % rc)
    return {'job': deploy.start_job(work)}


# ----------------------------------------------------------- firewall and status
def ports(cfg, which, confirm):
    """Open the DNS (53) or web (80, 443) ports in the firewall. Only after the person typed 'open'."""
    if which not in PORT_SETS:
        raise ValueError('Unknown port set')
    if confirm != 'open':
        raise ValueError('Type "open" to confirm')
    rc, out = run(cfg, 'bash -s', inp=script('dns_ports.sh').replace('__MODE__', which).encode(), timeout=60)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'could not change the firewall')
    return out.strip().splitlines()


def status(cfg):
    """What the server says now: NSD, the firewall and this server's address."""
    rc, out = run(cfg, 'bash -s', inp=script('dns_status.sh').encode(), timeout=30)
    facts = dict(line.split(' ', 1) for line in out.splitlines() if ' ' in line and line.split(' ', 1)[0].isupper())
    return {'ip': facts.get('IP', '').strip(), 'nsd': facts.get('NSD', 'missing').strip(), 'listening': facts.get('LISTEN53', 'no').strip() == 'yes',
            'firewall': facts.get('UFW', 'unknown').strip(), 'open': {p: facts.get('FW' + p, 'unknown').strip() for p in ('53', '80', '443')}}


def site_direct(cfg, name, on, confirm):
    """Let a site answer on ports 80/443 itself (Caddy gets its own certificate) instead of only through the tunnel. Needs the name to point at this server."""
    deploy._name(name)
    if name in deploy.PROTECTED:
        raise ValueError('The site that runs this app stays behind the tunnel')
    if confirm != name:
        raise ValueError('Type the site name to confirm')
    deploy.perf.invalidate('status')
    rc, out = run(cfg, 'bash -s', inp=script('site_direct.sh').replace('__NAME__', q(name)).replace('__MODE__', 'on' if on else 'off').encode(), timeout=60)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'could not change the site')
    return out.strip().splitlines()

