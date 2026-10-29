"""Server care details: what is behind a card on the Care page (banned addresses, biggest folders, memory hogs ...) and the few actions that go with them.

Every command is fixed text; the only inputs are a folder (validated) and an IP address (parsed, not pasted). Disk scans run at the lowest CPU and
disk priority with a time limit, so looking never slows your sites down.
"""
import ipaddress
import re

from . import care
from .runner import q, run

DETAIL_OF = {'ssh_password': 'ssh', 'ssh_root': 'ssh', 'fail2ban': 'fail2ban', 'firewall': 'firewall', 'auto_updates': 'updates', 'reboot': 'updates',
             'time_sync': 'time', 'swap': 'memory', 'memory': 'memory', 'disk': 'disk', 'exposed': 'exposed'}
SCAN_SECONDS = 30
PATH_RE = re.compile(r'^/[A-Za-z0-9._/-]*$')


# ------------------------------------------------------------------ parsers
def parse_fail2ban(text):
    def num(label):
        m = re.search(r'%s:\s*(\d+)' % label, text)
        return int(m.group(1)) if m else 0
    m = re.search(r'Banned IP list:\s*(.*)', text)
    return {'kind': 'fail2ban', 'installed': 'Status for the jail' in text, 'banned': m.group(1).split() if m else [],
            'currently_failed': num('Currently failed'), 'total_failed': num('Total failed'), 'total_banned': num('Total banned')}


def parse_disk(du, df, path):
    """`du -xk --max-depth=1` lines ("KB<TAB>path", biggest first) and `df` rows. The folder itself (the total) is not in the list."""
    items = []
    for line in du.splitlines():
        m = re.match(r'^(\d+)\s+(/.*)$', line.strip())
        if m and m.group(2).rstrip('/') != path.rstrip('/'):
            items.append({'path': m.group(2), 'kb': int(m.group(1))})
    total = next((int(m.group(1)) for m in (re.match(r'^(\d+)\s+(/.*)$', l.strip()) for l in du.splitlines()) if m and m.group(2).rstrip('/') == path.rstrip('/')), None)
    mounts = []
    for line in df.splitlines()[1:]:
        f = line.split()
        if len(f) >= 5:
            mounts.append({'mount': f[0], 'size': f[1], 'used': f[2], 'avail': f[3], 'pct': int(f[4].rstrip('%') or 0)})
    return {'kind': 'disk', 'path': path, 'total_kb': total, 'items': sorted(items, key=lambda i: -i['kb'])[:10], 'mounts': mounts}


def parse_memory(free, ps):
    nums = {}
    for line in free.splitlines():
        f = line.split()
        if f and f[0] in ('Mem:', 'Swap:') and len(f) >= 3:
            nums[f[0]] = [int(x) for x in f[1:] if x.isdigit()]
    mem, swap = nums.get('Mem:', [0, 0, 0, 0, 0, 0]), nums.get('Swap:', [0, 0, 0])
    procs = []
    for line in ps.splitlines()[1:]:
        f = line.split(None, 4)
        if len(f) == 5 and f[0].isdigit() and f[2].isdigit():
            procs.append({'pid': int(f[0]), 'user': f[1], 'rss_mb': round(int(f[2]) / 1024.0, 1), 'cpu': float(f[3]) if re.match(r'^[\d.]+$', f[3]) else 0.0, 'name': f[4].strip()})
    return {'kind': 'memory', 'total_mb': mem[0], 'used_mb': mem[1] if len(mem) > 1 else 0, 'available_mb': mem[5] if len(mem) > 5 else 0,
            'swap_total_mb': swap[0], 'swap_used_mb': swap[1] if len(swap) > 1 else 0, 'processes': procs[:10]}


def parse_ssh(text):
    total = re.search(r'TOTAL=(\d+)', text)
    top = [{'ip': m.group(2), 'count': int(m.group(1))} for m in re.finditer(r'^\s*(\d+)\s+([0-9a-fA-F:.]+)\s*$', text, re.M)]
    return {'kind': 'ssh', 'failed_24h': int(total.group(1)) if total else 0, 'top': top[:8]}


def parse_updates(text):
    head, _, rest = text.partition('---')
    pkgs = [l.split('/')[0] for l in head.splitlines() if '/' in l]
    count = re.search(r'COUNT=(\d+)', rest)
    reboot = [l.strip() for l in rest.splitlines() if l.strip() and not l.startswith('COUNT=')]
    return {'kind': 'updates', 'count': int(count.group(1)) if count else len(pkgs), 'packages': pkgs[:20], 'reboot_packages': reboot[:20]}


def parse_firewall(text):
    return {'kind': 'firewall', 'active': 'Status: active' in text, 'rules': [l.strip() for l in text.splitlines() if re.match(r'^\[\s*\d+\]', l.strip())][:40]}


# ------------------------------------------------------------------ reading
def check_folder(path):
    path = str(path or '/').strip() or '/'
    if not PATH_RE.match(path) or '..' in path.split('/') or '//' in path or len(path) > 200:
        raise ValueError('That folder path looks wrong')
    return path if path == '/' else path.rstrip('/')


def detail(cfg, finding, path=None, raw=None):
    """The details behind one finding, as a dict with a 'kind' the page knows how to draw."""
    kind = DETAIL_OF.get(finding)
    if not kind:
        raise ValueError('No details for that item')
    if kind == 'fail2ban':
        rc, out = run(cfg, 'sudo fail2ban-client status sshd 2>&1', timeout=30)
        return parse_fail2ban(out)
    if kind == 'disk':
        p = check_folder(path)
        rc, du = run(cfg, 'nice -n 19 ionice -c 3 timeout %d du -xk --max-depth=1 %s 2>/dev/null | sort -rn | head -14' % (SCAN_SECONDS, q(p)), timeout=SCAN_SECONDS + 15)
        rc2, df = run(cfg, 'df -h --output=target,size,used,avail,pcent -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | head -8', timeout=20)
        out = parse_disk(du, df, p)
        out['partial'] = not du.strip() or out['total_kb'] is None
        return out
    if kind == 'memory':
        rc, free = run(cfg, 'free -m', timeout=20)
        rc2, ps = run(cfg, 'ps -eo pid,user:14,rss,pcpu,comm --sort=-rss | head -11', timeout=20)
        return parse_memory(free, ps)
    if kind == 'ssh':
        cmd = ('L=$(sudo journalctl -u ssh -u sshd --since "24 hours ago" --no-pager -q 2>/dev/null | grep -E "Failed password|Invalid user|authentication failure"); '
               'echo "TOTAL=$(printf "%s" "$L" | grep -c .)"; printf "%s" "$L" | grep -oE "from [0-9a-fA-F:.]+" | awk \'{print $2}\' | sort | uniq -c | sort -rn | head -8')
        rc, out = run(cfg, cmd, timeout=40)
        return parse_ssh(out)
    if kind == 'firewall':
        rc, out = run(cfg, 'sudo ufw status numbered 2>&1 | head -60', timeout=20)
        return parse_firewall(out)
    if kind == 'updates':
        rc, out = run(cfg, 'apt list --upgradable 2>/dev/null | tail -n +2 | head -20; echo ---; echo COUNT=$(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l); cat /var/run/reboot-required.pkgs 2>/dev/null', timeout=40)
        return parse_updates(out)
    if kind == 'exposed':
        facts = raw or care.collect(cfg)
        return {'kind': 'exposed', 'listening': facts['listening'], 'risky': {str(k): v for k, v in care.RISKY_PORTS.items()}}
    rc, out = run(cfg, 'timedatectl 2>&1 | head -12', timeout=20)
    return {'kind': 'time', 'text': out.strip()}


# ------------------------------------------------------------------ actions
def check_ip(value):
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError:
        raise ValueError('That is not an IP address')
    return ip


def action(cfg, name, ip, confirm, caller_ip=''):
    """Ban or unban an address in fail2ban's SSH jail. Banning is refused for loopback, this server's own address and the address you are using now."""
    if confirm != care.CONFIRM:
        raise ValueError('Confirm the change first')
    if name not in ('ban', 'unban'):
        raise ValueError('Unknown action')
    addr = check_ip(ip)
    if name == 'ban':
        mine = {a for a in (str(caller_ip).strip(), str(cfg.get('host', '')).strip()) if a}
        if addr.is_loopback or addr.is_unspecified or addr.is_multicast or str(addr) in mine:
            raise ValueError('That address is yours (or this server): banning it would lock you out')
    rc, out = run(cfg, 'sudo fail2ban-client set sshd %s %s 2>&1' % ('banip' if name == 'ban' else 'unbanip', q(str(addr))), timeout=30)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'fail2ban did not accept that (is it installed and running?)')
    return '%s %s' % ('Banned' if name == 'ban' else 'Unbanned', addr)
