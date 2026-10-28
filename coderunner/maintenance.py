"""Server upkeep: domain/SSL health, scheduled tasks (cron) per site, and disk usage with safe cleanup.

Same rules as the rest of the app: names and numbers are validated here, and only fixed commands reach the shell.
"""
import concurrent.futures
import hashlib
import json
import re
import socket
import ssl
import time

from . import config, deploy
from .runner import q, run, script

CRON_MAX = 20                                   # scheduled tasks per site
_ITEM = r'(\*|\d{1,2}(-\d{1,2})?)(/\d{1,2})?'
CRON_FIELD_RE = re.compile(r'^%s(,%s)*$' % (_ITEM, _ITEM))
CRON_CMD_RE = re.compile(r'^[A-Za-z0-9 _./:=@+,-]{1,200}$')   # no ; | & $ ` > < quotes, newlines or % (cron treats % specially)
CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


# ------------------------------------------------------------- domain / SSL health
def _check_host(host):
    r = {'host': host, 'dns': None, 'cert_days': None, 'issuer': None, 'tls_error': None}
    try:
        r['dns'] = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)[0][4][0]
    except OSError:
        return r
    try:
        with socket.create_connection((host, 443), 6) as raw, ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
        r['cert_days'] = int((ssl.cert_time_to_seconds(cert['notAfter']) - time.time()) // 86400)
        r['issuer'] = dict(x[0] for x in cert.get('issuer', ())).get('organizationName')
    except (OSError, ssl.SSLError, KeyError) as e:
        r['tls_error'] = str(e)[:120]
    return r


def domain_health(cfg):
    """DNS, certificate expiry and HTTP status for the public address of every site.

    Hostnames come from the server's own site list (never from the caller), so this cannot be pointed at arbitrary hosts.
    """
    sites = [s for s in deploy.status(cfg)['sites'] if s['hostname'] and '*' not in s['hostname'] and config.HOST_RE.match(s['hostname'])]
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        checks = list(ex.map(lambda s: _check_host(s['hostname']), sites))
    for s, c in zip(sites, checks):
        c.update(site=s['name'], public=s.get('public'), public_ms=s.get('public_ms'))
    return checks


# ------------------------------------------------------------ scheduled tasks (cron)
def _cron_file(name):
    deploy._name(name)
    return '/etc/cron.d/coderunner-' + name


def _cron_id(line):
    return hashlib.sha1(line.encode()).hexdigest()[:10]


def _read_cron(cfg, name):
    rc, out = run(cfg, 'sudo cat %s 2>/dev/null; true' % q(_cron_file(name)))
    return [l for l in out.splitlines() if re.match(r'^[\d*]', l)]


def _write_cron(cfg, name, lines):
    path = q(_cron_file(name))
    if not lines:
        return run(cfg, 'sudo rm -f %s' % path)
    text = 'PATH=/usr/local/bin:/usr/bin:/bin\n' + '\n'.join(lines) + '\n'
    return run(cfg, 'sudo tee %s >/dev/null && sudo chmod 644 %s && sudo chown root:root %s' % (path, path, path), inp=text.encode())


def _parse_cron(line):
    parts = line.split(None, 7)
    return {'id': _cron_id(line), 'schedule': ' '.join(parts[:5]), 'command': parts[7].split(' && ', 1)[-1] if len(parts) > 7 else ''}


def cron_list(cfg, name):
    return [_parse_cron(l) for l in _read_cron(cfg, name)]


def _check_schedule(schedule):
    fields = str(schedule).split()
    if len(fields) != 5:
        raise ValueError('Schedule needs 5 parts: minute hour day month weekday (example: 0 3 * * *)')
    for f, (lo, hi) in zip(fields, CRON_RANGES):
        if not CRON_FIELD_RE.match(f):
            raise ValueError('Schedule part "%s" looks wrong' % f)
        if any(int(n) < lo or int(n) > hi for n in re.findall(r'(?<![/\d])\d+', f)):
            raise ValueError('Schedule part "%s" is out of range (%d-%d)' % (f, lo, hi))
    return ' '.join(fields)


def cron_add(cfg, a):
    name = a.get('name', '')
    schedule = _check_schedule(a.get('schedule', ''))
    command = str(a.get('command', '')).strip()
    if not CRON_CMD_RE.match(command):
        raise ValueError('Command: letters, digits, spaces and . / : = @ + , - _ only (no pipes, quotes or redirects). Put anything complex in a script in your site.')
    _cron_file(name)   # validates the site name before it reaches a shell
    rc, out = run(cfg, 'id app_%s >/dev/null 2>&1 && echo yes || echo no' % name)
    if 'yes' not in out:
        raise ValueError('Only sites that run as a service (Node or .NET) can have scheduled tasks')
    lines = _read_cron(cfg, name)
    if len(lines) >= CRON_MAX:
        raise ValueError('At most %d scheduled tasks per site' % CRON_MAX)
    lines.append('%s app_%s cd /srv/%s/current && %s' % (schedule, name, name, command))
    rc, out = _write_cron(cfg, name, lines)
    return rc, out.strip() or 'added'


def cron_delete(cfg, a):
    name, cid = a.get('name', ''), str(a.get('id', ''))
    lines = _read_cron(cfg, name)
    keep = [l for l in lines if _cron_id(l) != cid]
    if len(keep) == len(lines):
        raise LookupError('That task no longer exists')
    return _write_cron(cfg, name, keep)


# ---------------------------------------------------------------- disk and cleanup
def disk_report(cfg):
    rc, out = run(cfg, 'sudo python3 -', inp=script('disk.py').encode(), timeout=150)
    if rc or '{' not in out:
        raise RuntimeError(out.strip()[-400:] or 'could not read disk usage')
    return json.loads(out[out.index('{'):out.rindex('}') + 1])


def _free(cfg):
    rc, out = run(cfg, "df -B1 --output=avail / | tail -1")
    return int(out.strip() or 0)


PRUNE_RELEASES = '''KEEP=%d
for d in %s; do
  [ -d "$d/releases" ] || continue
  cur=$(readlink -f "$d/current" 2>/dev/null || true)
  ls -1d "$d"/releases/* 2>/dev/null | sort -r | tail -n +$((KEEP+1)) | while read -r r; do
    [ "$(readlink -f "$r")" = "$cur" ] && continue
    echo "removing $r"; sudo rm -rf -- "$r"
  done
done'''

CLEANERS = {
    'journal': 'sudo journalctl --vacuum-size=200M 2>&1 | tail -3',
    'apt': 'sudo apt-get clean && echo "apt cache cleared"',
    'docker': 'command -v docker >/dev/null && sudo docker image prune -f 2>&1 | tail -3 || echo "docker is not installed"',
}


def disk_clean(cfg, a):
    what = a.get('what')
    if what == 'releases':
        keep = max(1, min(10, int(a.get('keep', 3))))
        name = str(a.get('name') or '')
        target = '/srv/%s' % deploy._name(name) if name else '/srv/*'
        cmd = PRUNE_RELEASES % (keep, target)
        label = 'old releases of %s' % (name or 'all sites')
    elif what in CLEANERS:
        cmd, label = CLEANERS[what], what
    else:
        raise ValueError('unknown cleanup')
    before = _free(cfg)
    rc, out = run(cfg, 'bash -s', inp=cmd.encode(), timeout=180)
    freed = max(0, _free(cfg) - before)
    return rc, label, freed, out.strip()
