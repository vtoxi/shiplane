"""Is a domain ready to be served from this server? Looks the real DNS up (over DNS-over-HTTPS) and says what is wrong in plain words: the A record, www, an IPv6 record
pointing elsewhere (Let's Encrypt tries it first), a CAA record that forbids Let's Encrypt, and who hosts the domain's DNS. Changes nothing.

`evaluate()` is pure (it takes the answers), so it is tested without a network."""
import ipaddress
import json
import re
import urllib.parse
import urllib.request

from . import config

DOH = 'https://cloudflare-dns.com/dns-query?name=%s&type=%s'
RTYPES = {1: 'A', 28: 'AAAA', 5: 'CNAME', 2: 'NS', 257: 'CAA'}


def query(name, rtype):
    """[(TYPE, data)] for the name (a CNAME chain is included), [] when there is none, None when the lookup itself failed."""
    req = urllib.request.Request(DOH % (urllib.parse.quote(name), rtype), headers={'Accept': 'application/dns-json', 'User-Agent': 'shiplane'})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except (OSError, ValueError):
        return None
    return [(RTYPES.get(a.get('type'), str(a.get('type'))), str(a.get('data', '')).strip().strip('"').rstrip('.') if a.get('type') in (2, 5) else str(a.get('data', '')).strip())
            for a in data.get('Answer') or []]


def _f(id_, status, title, detail, fix=''):
    return {'id': id_, 'status': status, 'title': title, 'detail': detail, 'fix': fix}


def _ips(rows, kind):
    return sorted({d for t, d in rows if t == kind})


def evaluate(domain, ip, ans):
    """Findings from the DNS answers. ans: {'apex': [(type, data)], 'www': [...], 'apex6': [...], 'www6': [...], 'ns': [...], 'caa': [...]}."""
    out = []
    apex_a, www_a = _ips(ans['apex'], 'A'), _ips(ans['www'], 'A')
    www_cname = [d for t, d in ans['www'] if t == 'CNAME']
    ns = sorted({d.lower() for t, d in ans['ns'] if t == 'NS'})
    mine = ' (%s)' % ip if ip else ''
    # who hosts the DNS
    if not ns:
        out.append(_f('ns', 'bad', 'The domain has no nameservers', 'The registrar has not delegated it yet, or it is misspelled.', 'Check the domain name and that it is registered and active.'))
    elif any(n.endswith('.cloudflare.com') for n in ns):
        out.append(_f('ns', 'warn', 'The DNS is hosted by Cloudflare', ', '.join(ns), 'Add the records in Cloudflare (not at your registrar), or use the Cloudflare tab on this page to serve it through the tunnel. For direct HTTPS set the records to "DNS only" (grey cloud).'))
    elif any(n.endswith('.' + domain) for n in ns):
        out.append(_f('ns', 'ok', 'The domain uses its own nameservers', ', '.join(ns), 'If they are this server\'s nameservers (DNS hosting page), edit the records there.'))
    else:
        out.append(_f('ns', 'ok', 'The DNS is hosted by %s' % ns[0].split('.', 1)[-1] if '.' in ns[0] else 'The DNS is hosted elsewhere', ', '.join(ns), 'Add the records below in your registrar or DNS host\'s DNS manager (the place that lists your nameservers).'))
    # the apex
    if not apex_a:
        out.append(_f('apex', 'bad', 'No A record for %s' % domain, 'Nothing tells browsers where the site is.', 'Add an A record: name @ (or %s), value %s.' % (domain, ip or 'this server\'s IP address')))
    elif not ip:
        out.append(_f('apex', 'warn', '%s points at %s' % (domain, ', '.join(apex_a)), 'This server\'s public address could not be read to compare with.', 'Make sure it is this server\'s IP.'))
    elif apex_a == [ip]:
        out.append(_f('apex', 'ok', '%s points at this server%s' % (domain, mine), ''))
    elif ip in apex_a:
        out.append(_f('apex', 'bad', '%s also points elsewhere' % domain, 'Addresses: %s. Visitors, and the certificate check, are sent to a random one of them.' % ', '.join(apex_a), 'Delete the A records that are not %s.' % ip))
    else:
        out.append(_f('apex', 'bad', '%s points at %s, not at this server%s' % (domain, ', '.join(apex_a), mine),
                      'That is often a parking page your registrar or host added when you bought the domain.', 'Edit that A record: set its value to %s and delete any other A record for @.' % ip))
    # www (an alias of the main name resolves to the main name's addresses, so the addresses are what matter)
    if www_a and (not ip or www_a == [ip]):
        out.append(_f('www', 'ok' if ip else 'warn', 'www.%s %s' % (domain, ('works' + (' (an alias of %s)' % domain if www_cname else '')) if ip else 'points at %s' % ', '.join(www_a)), '' if ip else 'This server\'s public address could not be read to compare with.'))
    elif www_a:
        out.append(_f('www', 'bad', 'www.%s points at %s' % (domain, ', '.join(www_a)), 'It does not lead only to this server.', 'Set it to a CNAME of %s, or an A record with %s, and delete the others.' % (domain, ip)))
    elif www_cname:
        out.append(_f('www', 'bad', 'www.%s is an alias of %s, which has no address' % (domain, ', '.join(www_cname)), '', 'Point the CNAME at %s, or use an A record.' % domain))
    else:
        out.append(_f('www', 'warn', 'www.%s has no record' % domain, 'People who type www in front will not find the site.', 'Add a CNAME record: name www, value %s (or an A record with %s).' % (domain, ip or 'this server\'s IP')))
    # IPv6: Let's Encrypt and browsers try it first
    v6 = _ips(ans['apex6'], 'AAAA') + _ips(ans['www6'], 'AAAA')
    if v6:
        out.append(_f('ipv6', 'warn', 'An IPv6 (AAAA) record exists', ', '.join(sorted(set(v6))), 'Browsers and Let\'s Encrypt try IPv6 first. If that address is not this server (a parking page, for example), HTTPS fails. Delete the AAAA records unless this server really answers on them.'))
    # CAA: only the "issue" lines decide who may issue a certificate for the name itself
    issue = []
    for t, d in ans['caa']:
        if t == 'CAA' and not d.startswith('\\#'):
            parts = d.split(None, 2)
            if len(parts) >= 2 and parts[1].lower() == 'issue':
                issue.append((parts[2] if len(parts) > 2 else '').lower())
    if issue and not any('letsencrypt.org' in v for v in issue):
        out.append(_f('caa', 'bad', 'A CAA record blocks Let\'s Encrypt', '; '.join(d for t, d in ans['caa'] if t == 'CAA'), 'Add a CAA record: name @, flags 0, tag issue, value "letsencrypt.org" (or delete the CAA records).'))
    elif issue:
        out.append(_f('caa', 'ok', 'CAA allows Let\'s Encrypt', ''))
    return out


def check(domain, ip='', fetch=None):
    domain = str(domain or '').strip().lower().rstrip('.')
    if not config.HOST_RE.match(domain) or '.' not in domain or len(domain) > 253 or domain.startswith('www.'):
        raise ValueError('Type the domain without www, for example example.pk')
    if ip:
        try:
            ip = str(ipaddress.IPv4Address(ip))
        except ValueError:
            raise ValueError('The server address must be an IPv4 address')
    fetch = fetch or query
    answers = {'apex': fetch(domain, 'A'), 'www': fetch('www.' + domain, 'A'), 'apex6': fetch(domain, 'AAAA'), 'www6': fetch('www.' + domain, 'AAAA'), 'ns': fetch(domain, 'NS'), 'caa': fetch(domain, 'CAA')}
    if all(v is None for v in answers.values()):
        raise RuntimeError('Could not reach the DNS-over-HTTPS service. Check this computer\'s internet connection.')
    answers = {k: v or [] for k, v in answers.items()}
    return {'domain': domain, 'ip': ip, 'findings': evaluate(domain, ip, answers), 'records': {k: [{'type': t, 'data': d} for t, d in v] for k, v in answers.items()}}
