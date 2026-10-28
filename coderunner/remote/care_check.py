import glob
import json
import os
import re
import subprocess

try:
    import grp
except ImportError:      # not Linux: the checks below are only meaningful on a server
    grp = None


def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, cwd='/tmp').stdout.strip()


def rd(p):
    try:
        return open(p).read()
    except OSError:
        return ''


def sshd_settings(text):
    """`sshd -T` prints one lowercase 'key value' per line; 'port' can appear several times."""
    out = {'port': []}
    for line in text.splitlines():
        key, _, val = line.partition(' ')
        if key == 'port' and val.isdigit():
            out['port'].append(int(val))
        elif key in ('passwordauthentication', 'permitrootlogin', 'pubkeyauthentication', 'kbdinteractiveauthentication'):
            out[key] = val.strip()
    return out


def ufw_state(text):
    out = {'installed': bool(text), 'active': False, 'default_incoming': '', 'allows': []}
    for line in text.splitlines():
        if line.startswith('Status:'):
            out['active'] = 'inactive' not in line
        elif line.startswith('Default:'):
            m = re.match(r'Default:\s*(\w+(?: \(\w+\))?) \(incoming\)', line)
            out['default_incoming'] = m.group(1) if m else ''
        else:
            m = re.match(r'^(\d+)(?:/(tcp|udp))?\s+ALLOW', line)
            if m:
                out['allows'].append(int(m.group(1)))
    return out


def listeners(text):
    """Services listening on a non-loopback address: [{port, addr, proc}]. Input: `ss -H -tlnp`."""
    seen, out = set(), []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        addr, _, port = f[3].rpartition(':')
        addr = addr.strip('[]')
        if addr.startswith('127.') or addr in ('::1', ''):
            continue
        proc = re.search(r'"([^"]+)"', line)
        key = (port, proc.group(1) if proc else '')
        if port.isdigit() and key not in seen:
            seen.add(key)
            out.append({'port': int(port), 'addr': addr, 'proc': key[1]})
    return sorted(out, key=lambda x: x['port'])


def key_users():
    """Local accounts that can use sudo AND have at least one SSH public key: the people who can still get in after password login is turned off."""
    admins = set()
    for g in ('sudo', 'admin', 'wheel') if grp else ():
        try:
            admins.update(grp.getgrnam(g).gr_mem)
        except KeyError:
            pass
    users = []
    for path in glob.glob('/home/*/.ssh/authorized_keys'):
        name = path.split('/')[2]
        if name in admins and any(l.strip() and not l.startswith('#') for l in rd(path).splitlines()):
            users.append(name)
    return sorted(users)


def main():
    mem = {l.split(':')[0]: int(l.split()[1]) for l in rd('/proc/meminfo').splitlines() if ':' in l}
    du = os.statvfs('/')
    release = dict(l.split('=', 1) for l in rd('/etc/os-release').splitlines() if '=' in l)
    auto = rd('/etc/apt/apt.conf.d/20auto-upgrades')
    f2b_active = sh('systemctl is-active fail2ban 2>/dev/null') == 'active'
    return {
        'os_id': release.get('ID', '').strip('"'), 'os': release.get('PRETTY_NAME', 'Linux').strip('"'),
        'ssh': sshd_settings(sh('sshd -T 2>/dev/null')), 'key_users': key_users(),
        'root_keys': any(l.strip() and not l.startswith('#') for l in rd('/root/.ssh/authorized_keys').splitlines()),
        'ufw': ufw_state(sh('ufw status verbose 2>/dev/null')),
        'fail2ban': {'installed': bool(sh('command -v fail2ban-client')), 'active': f2b_active,
                     'banned': int((re.search(r'Currently banned:\s*(\d+)', sh('fail2ban-client status sshd 2>/dev/null')) or [0, 0])[1]) if f2b_active else 0},
        'updates': {'installed': sh('dpkg -s unattended-upgrades 2>/dev/null | grep -c "Status: install ok"') == '1',
                    'enabled': bool(re.search(r'Unattended-Upgrade\s+"1"', auto))},
        'time_synced': sh('timedatectl show -p NTPSynchronized --value 2>/dev/null') == 'yes',
        'swap_mb': sum(int(l.split()[2]) for l in rd('/proc/swaps').splitlines()[1:] if len(l.split()) > 2) // 1024,
        'ram_mb': mem.get('MemTotal', 0) // 1024, 'mem_avail_mb': mem.get('MemAvailable', 0) // 1024,
        'disk_total': du.f_blocks * du.f_frsize, 'disk_free': du.f_bavail * du.f_frsize,
        'reboot_required': os.path.exists('/var/run/reboot-required'),
        'listening': listeners(sh('ss -H -tlnp 2>/dev/null')),
    }


if __name__ == '__main__':
    print(json.dumps(main()))
