"""Server care: a security and health checklist for the server, with safe one-click fixes.

One read-only script (remote/care_check.py) collects the facts. `evaluate` turns them into findings and a score; `fix_script` decides, from fresh
facts and never from the browser, whether a fix is safe and builds it from a fixed script in remote/care/. Fixes refuse anything that could lock
you out (SSH password login, the firewall) and need an explicit confirmation.
"""
import json
import re

from . import deploy, perf
from .runner import explain_ssh, q, run, script

SUPPORTED = ('ubuntu', 'debian')
RISKY_PORTS = {3306: 'MySQL/MariaDB', 5432: 'PostgreSQL', 6379: 'Redis', 27017: 'MongoDB', 11211: 'Memcached', 9200: 'Elasticsearch', 5984: 'CouchDB', 2375: 'Docker (no login)', 8765: 'Shiplane itself'}
ROOT_OK = ('no', 'prohibit-password', 'without-password', 'forced-commands-only')
CONFIRM = 'apply'
FIXES = {   # id -> (script file, button label)
    'ssh_keys_only': ('ssh_keys_only.sh', 'Turn off password login'),
    'ssh_no_root': ('ssh_no_root.sh', 'Block root passwords'),
    'firewall': ('firewall.sh', 'Turn on the firewall'),
    'fail2ban': ('fail2ban.sh', 'Install fail2ban'),
    'auto_updates': ('auto_updates.sh', 'Turn on automatic updates'),
    'time_sync': ('time_sync.sh', 'Synchronise the clock'),
    'swap': ('swap.sh', 'Add a swap file'),
}
BAD, WARN, OK = 'bad', 'warn', 'ok'
WEIGHT = {'ssh_password': 20, 'firewall': 20, 'auto_updates': 15, 'ssh_root': 10, 'fail2ban': 10, 'swap': 10, 'disk': 10, 'exposed': 10, 'time_sync': 5, 'reboot': 5, 'memory': 5}


def collect(cfg):
    """Fresh facts from the server (one script, read-only)."""
    rc, out = run(cfg, 'sudo python3 -', inp=script('care_check.py').encode(), timeout=60)
    if rc or '{' not in out:
        raise RuntimeError(explain_ssh(out, cfg)[-400:] or 'could not read the server')
    return json.loads(out[out.index('{'):out.rindex('}') + 1])


def swap_size(raw):
    """MB of swap worth adding: 1 GB on a 1 GB server, 2 GB up to 4 GB of memory, none above that."""
    ram = raw.get('ram_mb', 0)
    return 0 if ram <= 0 or ram > 4096 else 1024 if ram <= 1024 else 2048


def _exposed(raw):
    """Risky services listening on every address that the firewall does not block."""
    ufw = raw['ufw']
    shielded = ufw['active'] and ufw['default_incoming'].startswith('deny')
    return [p for p in raw['listening'] if p['port'] in RISKY_PORTS and not (shielded and p['port'] not in ufw['allows'])]


def evaluate(raw):
    """Findings (in the order shown) and the score. Pure: no server calls."""
    ssh, ufw, f2b, upd = raw['ssh'], raw['ufw'], raw['fail2ban'], raw['updates']
    items = []

    def add(id_, title, status, detail, why, fix=None):
        items.append({'id': id_, 'title': title, 'status': status, 'detail': detail, 'why': why, 'fix': fix, 'weight': WEIGHT.get(id_, 0)})
    pw = ssh.get('passwordauthentication', 'yes')
    add('ssh_password', 'SSH password login', OK if pw == 'no' else BAD, 'Off: only keys can log in' if pw == 'no' else 'On: anyone on the internet can try passwords',
        'Bots try thousands of passwords a day. Keys cannot be guessed.', None if pw == 'no' else 'ssh_keys_only')
    root = ssh.get('permitrootlogin', 'yes')
    add('ssh_root', 'Root login', OK if root in ROOT_OK else BAD, 'Root cannot log in with a password' if root in ROOT_OK else 'Root can log in with a password',
        'Root is the first account every attacker tries.', None if root in ROOT_OK else 'ssh_no_root')
    if not ufw['installed']:
        fw = (BAD, 'No firewall installed')
    elif not ufw['active']:
        fw = (BAD, 'Installed but switched off')
    elif not ufw['default_incoming'].startswith('deny'):
        fw = (WARN, 'On, but incoming traffic is allowed by default')
    else:
        ports = sorted(set(ufw['allows']))
        fw = (OK, 'On: only port%s %s open to the outside' % ('' if len(ports) == 1 else 's', ', '.join(str(p) for p in ports)) if ports else 'On: nothing is open to the outside')
    add('firewall', 'Firewall', fw[0], fw[1], 'A firewall closes every port you did not choose to open.', None if fw[0] == OK else 'firewall')
    add('fail2ban', 'Blocks repeated failed logins', OK if f2b['active'] else WARN, 'fail2ban is on (%d address%s banned now)' % (f2b['banned'], '' if f2b['banned'] == 1 else 'es') if f2b['active'] else 'Not running',
        'Slows down password guessing and keeps your logs quiet.', None if f2b['active'] else 'fail2ban')
    updating = upd['installed'] and upd['enabled']
    add('auto_updates', 'Automatic security updates', OK if updating else BAD, 'On: security fixes install by themselves' if updating else 'Off: known holes stay open until you update by hand',
        'Most break-ins use holes that were fixed months ago.', None if updating else 'auto_updates')
    add('time_sync', 'Clock', OK if raw['time_synced'] else WARN, 'Synchronised' if raw['time_synced'] else 'Not synchronised',
        'A wrong clock breaks certificates, logins and logs.', None if raw['time_synced'] else 'time_sync')
    size = swap_size(raw)
    if raw['swap_mb'] > 0:
        sw = (OK, '%d MB of swap' % raw['swap_mb'])
    elif size:
        sw = (WARN, 'No swap on a server with %d MB of memory' % raw['ram_mb'])
    else:
        sw = (OK, 'Not needed (%d MB of memory)' % raw['ram_mb'])
    add('swap', 'Swap (memory safety net)', sw[0], sw[1], 'Without swap, a memory spike makes Linux kill a program (often your database).', 'swap' if sw[0] == WARN else None)
    if raw.get('ram_mb') and 'mem_avail_mb' in raw:
        mem = 100.0 * (raw['ram_mb'] - raw['mem_avail_mb']) / raw['ram_mb']
        add('memory', 'Memory in use', BAD if mem >= 95 else WARN if mem >= 85 else OK, '%d MB of %d MB in use (%d%%)' % (raw['ram_mb'] - raw['mem_avail_mb'], raw['ram_mb'], mem),
            'When memory runs out, Linux stops programs, often the database. See what uses it, restart a hungry service, or add swap.')
    used = 100.0 * (raw['disk_total'] - raw['disk_free']) / max(1, raw['disk_total'])
    add('disk', 'Disk space', BAD if used >= 90 else WARN if used >= 80 else OK, '%d%% used, %.1f GB free' % (used, raw['disk_free'] / 1e9),
        'A full disk stops databases and deploys. Clean up in Maintenance, Disk & cleanup.')
    add('reboot', 'Restart needed', WARN if raw['reboot_required'] else OK, 'Updates are waiting for a restart' if raw['reboot_required'] else 'No restart needed',
        'Some security updates only take effect after a restart (Server page).')
    open_ports = _exposed(raw)
    add('exposed', 'Exposed services', BAD if open_ports else OK,
        'Reachable from the internet: ' + ', '.join('%s (%d)' % (RISKY_PORTS[p['port']], p['port']) for p in open_ports) if open_ports else 'No database or admin service is open to the internet',
        'Databases and caches should only listen on this server itself.', 'firewall' if open_ports and not (ufw['active'] and ufw['default_incoming'].startswith('deny')) else None)
    penalty = sum(WEIGHT[i['id']] * (1 if i['status'] == BAD else 0.5 if i['status'] == WARN else 0) for i in items)
    score = max(0, min(100, int(round(100 - penalty))))
    label = 'Well protected' if score >= 90 else 'Good, with some gaps' if score >= 70 else 'Needs attention' if score >= 50 else 'At risk'
    return {'score': score, 'label': label, 'os': raw['os'], 'findings': items, 'supported': raw['os_id'] in SUPPORTED}


def fix_script(fix_id, raw, cfg):
    """The script for one fix, or ValueError saying why it must not run. `raw` must be fresh facts from the server."""
    if fix_id not in FIXES:
        raise ValueError('Unknown fix')
    if raw['os_id'] not in SUPPORTED:
        raise ValueError('Automatic fixes support Ubuntu and Debian servers only (this one is %s)' % raw['os'])
    by_password = cfg.get('mode') == 'ssh' and cfg.get('auth') == 'password'
    ports = sorted(set(raw['ssh']['port']) | ({int(cfg['port'])} if cfg.get('mode') == 'ssh' and cfg.get('port') else set()))
    text = script('care/' + FIXES[fix_id][0])
    values = {}
    if fix_id == 'ssh_keys_only':
        if by_password:
            raise ValueError('Shiplane itself logs in to this server with a password. Switch it to an SSH key in Settings first, or it would lock itself out.')
        if not raw['key_users'] and not raw.get('root_keys'):
            raise ValueError('No account on this server has an SSH key yet, so turning passwords off would lock everyone out. Add your public key to ~/.ssh/authorized_keys first.')
    elif fix_id == 'ssh_no_root':
        if by_password and cfg.get('user') == 'root':
            raise ValueError('Shiplane logs in as root with a password. Use an SSH key (or another user) in Settings first.')
    elif fix_id in ('firewall', 'fail2ban'):
        if not ports or any(not 1 <= p <= 65535 for p in ports):
            raise ValueError('Could not work out the SSH port, so the firewall could lock you out. Nothing was changed.')
        values['ssh_ports'] = ' '.join(str(p) for p in ports)
        values['ssh_ports_comma'] = ','.join(str(p) for p in ports)
        values['web_ports'] = ' '.join(str(w) for w in (80, 443) if any(l['port'] == w for l in raw['listening']))
    elif fix_id == 'swap':
        size = swap_size(raw)
        if raw['swap_mb'] > 0 or not size:
            raise ValueError('This server does not need a swap file')
        if raw['disk_free'] < (size + 5120) * 1048576:
            raise ValueError('Not enough free disk: the swap file would leave less than 5 GB free')
        values['size_mb'] = str(size)
    for key, val in values.items():
        text = text.replace('__%s__' % key.upper(), val if re.fullmatch(r'[0-9 ,]*', val) else q(val))
    if re.search(r'__[A-Z_]+__', text):
        raise ValueError('Fix script has unfilled placeholders')
    return 'set -euo pipefail\n' + text


def start_fix(cfg, fix_id, confirm):
    """Re-read the server, check the fix is safe right now, then run it as a background job. Returns the job id."""
    if confirm != CONFIRM:
        raise ValueError('Confirm the change first')
    if any(not j.done and getattr(j, 'care', False) for j in deploy.JOBS.values()):
        raise ValueError('Another fix is still running')
    text = fix_script(fix_id, collect(cfg), cfg)
    job = deploy.start_script_job(cfg, text)
    deploy.JOBS[job].care = True
    perf.invalidate()
    return job


def report(cfg):
    """Everything the page needs: the findings, the score and, for each fixable finding, whether the fix is allowed right now and why not."""
    raw = collect(cfg)
    out = evaluate(raw)
    for f in out['findings']:
        if f['fix']:
            try:
                fix_script(f['fix'], raw, cfg)
                f['fix'] = {'id': f['fix'], 'label': FIXES[f['fix']][1], 'blocked': ''}
            except ValueError as e:
                f['fix'] = {'id': f['fix'], 'label': FIXES[f['fix']][1], 'blocked': str(e)}
    return out
