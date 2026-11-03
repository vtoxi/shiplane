"""Email deliverability check for a domain: MX, SPF, DKIM and DMARC, read over DNS-over-HTTPS (no packages, nothing is changed).

`check()` returns a list of findings {id, status ok|warn|bad, title, detail, fix}. The DNS lookups are done through `lookup`, so tests can replace it.
"""
import json
import re
import urllib.parse
import urllib.request

from . import config

DOH = 'https://cloudflare-dns.com/dns-query?name=%s&type=%s'
SELECTORS = ('default', 'google', 'selector1', 'selector2', 'k1', 's1', 'mail', 'dkim', 'smtp')
SELECTOR_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$')
LOOKUP_TERMS = re.compile(r'^[+\-~?]?(include:|a\b|a:|mx\b|mx:|ptr|exists:|redirect=)', re.I)


def lookup(name, rtype):
    """List of record values for `rtype` ('TXT' strings joined and unquoted, 'MX' as 'priority host'). [] when none or on a network error."""
    req = urllib.request.Request(DOH % (urllib.parse.quote(name), rtype), headers={'Accept': 'application/dns-json', 'User-Agent': 'shiplane'})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except (OSError, ValueError):
        return None
    out = []
    for a in data.get('Answer') or []:
        v = str(a.get('data', ''))
        out.append(re.sub(r'"\s*"', '', v).strip('"') if rtype == 'TXT' else v)
    return out


def _f(id_, status, title, detail, fix=''):
    return {'id': id_, 'status': status, 'title': title, 'detail': detail, 'fix': fix}


def spf(records):
    found = [r for r in records if r.lower().startswith('v=spf1')]
    if not found:
        return _f('spf', 'bad', 'No SPF record', 'Receivers cannot tell which servers may send mail for this domain, so mail is more likely to land in spam.',
                  'Add a TXT record on the domain, for example:  v=spf1 mx ~all  (add include: entries for any service that sends mail for you)')
    if len(found) > 1:
        return _f('spf', 'bad', 'More than one SPF record', 'Receivers ignore SPF when a domain has several records.', 'Merge them into a single TXT record that starts with v=spf1.')
    rec = found[0]
    terms = rec.split()[1:]
    lookups = sum(1 for t in terms if LOOKUP_TERMS.match(t))
    if lookups > 10:
        return _f('spf', 'bad', 'SPF needs more than 10 DNS lookups', '%d include/a/mx/ptr terms: receivers stop at 10 and treat SPF as failed.' % lookups,
                  'Remove unused includes or replace them with ip4:/ip6: entries.')
    end = terms[-1].lower() if terms else ''
    if end in ('-all', '~all'):
        return _f('spf', 'ok', 'SPF is set', rec + ('' if end == '-all' else '  (~all is a soft fail; -all is stricter once you are sure every sender is listed)'))
    if end.endswith('all'):
        return _f('spf', 'warn', 'SPF allows anyone to send' if end in ('+all', 'all') else 'SPF is neutral', rec, 'End the record with ~all (or -all).')
    return _f('spf', 'warn', 'SPF has no "all" at the end', rec, 'End the record with ~all (or -all).')


def dmarc(records):
    found = [r for r in records if r.lower().startswith('v=dmarc1')]
    if not found:
        return _f('dmarc', 'bad', 'No DMARC record', 'DMARC tells receivers what to do with mail that fails SPF/DKIM and sends you reports. Gmail and Yahoo expect it from bulk senders.',
                  'Add a TXT record on _dmarc.<domain>, for example:  v=DMARC1; p=none; rua=mailto:dmarc@<domain>  and tighten p= to quarantine, then reject, once reports look clean.')
    tags = dict((k.strip().lower(), v.strip()) for k, _, v in (p.partition('=') for p in found[0].split(';')) if k.strip())
    pol = tags.get('p', '').lower()
    if pol not in ('none', 'quarantine', 'reject'):
        return _f('dmarc', 'bad', 'DMARC policy is invalid', found[0], 'Set p=none, p=quarantine or p=reject.')
    if pol == 'none':
        return _f('dmarc', 'warn', 'DMARC only monitors (p=none)', found[0] + ('' if 'rua' in tags else '  (no rua= address, so you get no reports)'),
                  'Once reports show only legitimate mail, move to p=quarantine and then p=reject.')
    return _f('dmarc', 'ok', 'DMARC is enforced (p=%s)' % pol, found[0] + ('' if 'rua' in tags else '  (add rua=mailto:... to receive reports)'))


def dkim(by_selector):
    """by_selector: {selector: [txt records]}"""
    good = sorted(s for s, recs in by_selector.items() if any('v=dkim1' in r.lower() and re.search(r'p=\s*[A-Za-z0-9+/=]{20,}', r) for r in recs))
    revoked = sorted(s for s, recs in by_selector.items() if any('v=dkim1' in r.lower() and re.search(r'p=\s*(;|$)', r) for r in recs))
    if good:
        return _f('dkim', 'ok', 'DKIM key found (%s)' % ', '.join(good), 'Selectors checked: ' + ', '.join(by_selector))
    if revoked:
        return _f('dkim', 'warn', 'DKIM key is revoked (%s)' % ', '.join(revoked), 'The record exists but its public key is empty.', 'Publish a new key from your mail provider.')
    return _f('dkim', 'warn', 'No DKIM key found', 'Checked the common selectors (%s). Your provider may use another one.' % ', '.join(by_selector),
              'Turn on DKIM signing at your mail provider and publish the TXT record it gives you, or re-run this check with that selector name.')


def mx(records):
    if not records:
        return _f('mx', 'warn', 'No MX record', 'This domain cannot receive mail (fine if it only sends, but replies and bounces will not arrive).', 'Add MX records for your mail provider.')
    return _f('mx', 'ok', 'MX records found', ', '.join(sorted(records)))


def check(domain, selectors=(), fetch=None):
    domain = str(domain or '').strip().lower().rstrip('.')
    if not config.HOST_RE.match(domain) or '.' not in domain:
        raise ValueError('Type a domain like example.com')
    sels = list(dict.fromkeys(list(SELECTORS) + [s.lower() for s in selectors]))
    for s in sels:
        if not SELECTOR_RE.match(s):
            raise ValueError('Bad DKIM selector: ' + s)
    fetch = fetch or lookup
    got = {k: fetch(*k) for k in [(domain, 'MX'), (domain, 'TXT'), ('_dmarc.' + domain, 'TXT')] + [('%s._domainkey.%s' % (s, domain), 'TXT') for s in sels]}
    if all(v is None for v in got.values()):
        raise RuntimeError('Could not reach the DNS-over-HTTPS service. Check this computer\'s internet connection.')
    g = lambda *k: got.get(k) or []
    return {'domain': domain, 'findings': [mx(g(domain, 'MX')), spf(g(domain, 'TXT')), dkim({s: g('%s._domainkey.%s' % (s, domain), 'TXT') for s in sels}), dmarc(g('_dmarc.' + domain, 'TXT'))]}
