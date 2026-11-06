"""The Mail page's backend: a free mail server (Postfix + Dovecot + Rspamd) with mailboxes, aliases, forwarders, catch-all, autoresponders, spam filtering,
DKIM signing, an optional outgoing relay, a queue view and DNS record help. Every operation is the fixed script remote/mail/mailctl.py with a validated request."""
import json
import re

from . import config, deploy, emailcheck, firewall, perf
from .runner import run, script

PORTS = (25, 465, 587, 993, 995)               # the mail ports to open; 80 and 443 stay with Caddy (certificates, webmail)
DOMAIN_RE = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')
EMAIL_RE = re.compile(r'^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9])?@([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')


def _clean_domain(d):
    d = str(d or '').strip().lower().rstrip('.')
    if not DOMAIN_RE.fullmatch(d):
        raise ValueError('That is not a valid domain name')
    return d


def _clean_email(e):
    e = str(e or '').strip().lower()
    if not EMAIL_RE.fullmatch(e) or '..' in e.split('@')[0]:
        raise ValueError('That is not a valid email address')
    return e


def _script(req):
    return script('mail/mailctl.py').replace('__REQ__', repr(json.dumps(req)))


def call(cfg, action, timeout=120, **fields):
    rc, out = run(cfg, 'sudo python3 -', inp=_script(dict(fields, action=action)).encode(), timeout=timeout)
    if '{' not in out:
        raise RuntimeError(out.strip()[-300:] or 'could not reach the server')
    data = json.loads(out[out.index('{'):out.rindex('}') + 1])
    if rc or data.get('error'):
        raise ValueError(data.get('error') or 'The mail operation failed')
    perf.invalidate('status')
    return data


def status(cfg):
    return call(cfg, 'status', timeout=40)


def view(cfg):
    return call(cfg, 'view', timeout=60)


def install(cfg, hostname, options=None):
    """Install and configure the mail server as a background job (several minutes). Returns the job id."""
    host = _clean_domain(hostname)
    opts = {k: v for k, v in (options or {}).items() if k in ('spam', 'max_message_mb')}
    text = script('mail/install.sh').replace('__HOST__', "'%s'" % host)
    init = _script({'action': 'init', 'hostname': host, 'options': opts})
    full = text + '\necho "== settings"\nsudo python3 - <<\'MAILCTL_INIT\'\n' + init + '\nMAILCTL_INIT\n/usr/local/sbin/shiplane-mail-cert-sync || true\necho "DONE: the mail server is installed. Next: add a domain, then the DNS records."\n'
    return deploy.start_script_job(cfg, full)


def add_domain(cfg, domain, selector='mail'):
    return call(cfg, 'domain_add', domain=_clean_domain(domain), selector=str(selector or 'mail').lower())


def remove_domain(cfg, domain, confirm):
    domain = _clean_domain(domain)
    if confirm != domain:
        raise ValueError('Type the domain name to confirm')
    return call(cfg, 'domain_remove', domain=domain)


def add_account(cfg, email, password, quota_mb=1024, name=''):
    return call(cfg, 'account_add', email=_clean_email(email), password=password, quota_mb=quota_mb, name=str(name or '')[:80])


def account_op(cfg, email, op, **kw):
    if op not in ('password', 'quota', 'disable', 'enable', 'autoreply', 'delete'):
        raise ValueError('Unknown mailbox action')
    return call(cfg, 'account_' + op, email=_clean_email(email), **kw)


def add_alias(cfg, source, dests):
    source = str(source or '').strip().lower()
    if source.startswith('@'):
        source = '@' + _clean_domain(source[1:])
    else:
        source = _clean_email(source)
    dests = dests if isinstance(dests, list) else [x.strip() for x in re.split(r'[,\s;]+', str(dests or '')) if x.strip()]
    return call(cfg, 'alias_add', source=source, dests=[_clean_email(d) for d in dests])


def remove_alias(cfg, source):
    source = str(source or '').strip().lower()
    return call(cfg, 'alias_remove', source=source)


def set_relay(cfg, relay):
    return call(cfg, 'relay', relay=relay or {})


def set_options(cfg, options):
    return call(cfg, 'options', options=options or {})


def queue(cfg, op='list', qid=None):
    return call(cfg, 'queue', op=op, id=qid)


def logs(cfg, lines=200):
    return call(cfg, 'logs', lines=int(lines or 200))


def port25(cfg):
    return call(cfg, 'port25', timeout=40)


def dns_records(domain, hostname, ip, selector, dkim_public):
    """What to add at the domain's DNS: MX, SPF, DKIM, DMARC, and the mail host's own address. Pure: nothing is changed."""
    domain, hostname = _clean_domain(domain), _clean_domain(hostname)
    recs = []
    if domain == hostname or hostname.endswith('.' + domain):
        recs.append({'type': 'A', 'name': hostname, 'value': ip or '(this server\'s IP)', 'why': 'Where the mail server lives.'})
    recs += [
        {'type': 'MX', 'name': domain, 'value': hostname, 'priority': 10, 'why': 'Tells the world where to deliver mail for %s.' % domain},
        {'type': 'TXT', 'name': domain, 'value': 'v=spf1 mx ~all', 'why': 'SPF: only this server may send mail for the domain (add include: entries for other services that send for you).'},
        {'type': 'TXT', 'name': '%s._domainkey.%s' % (selector, domain), 'value': 'v=DKIM1; k=rsa; p=%s' % dkim_public, 'why': 'DKIM: the public key that proves outgoing mail is really from you.'},
        {'type': 'TXT', 'name': '_dmarc.%s' % domain, 'value': 'v=DMARC1; p=none; rua=mailto:postmaster@%s' % domain, 'why': 'DMARC: what receivers do with mail that fails SPF/DKIM. Start with p=none, move to quarantine once reports look clean.'},
        {'type': 'CNAME', 'name': 'autoconfig.%s' % domain, 'value': hostname, 'why': 'Optional: lets Thunderbird find the settings by itself.'},
    ]
    return recs


def check_dns(domain, selector):
    return emailcheck.check(_clean_domain(domain), [selector] if selector else [])


def open_ports(cfg, preview=False, confirm=None):
    """Allow the mail ports (plus nothing else) through the firewall. Never touches SSH; does nothing when the firewall is off."""
    if not preview and confirm != 'mail':
        raise ValueError('Type "mail" to confirm')
    st = firewall.status(cfg)
    if not st.get('installed') or not st.get('active'):
        return {'firewall': 'off', 'commands': [], 'note': 'The firewall is off, so the mail ports are already reachable. Turn the firewall on in Firewall & email, then open them here.'}
    cmds = []
    for p in PORTS:
        cmds += firewall.add(cfg, 'allow', p, 'tcp', 'any', preview=preview)['commands']
    return {'firewall': 'on', 'commands': cmds}
