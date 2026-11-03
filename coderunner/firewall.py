"""Firewall (ufw) rule manager with a lock-out guard. Every change is built from validated parts; nothing is run from free text.

* The SSH port(s) this app uses (the saved port plus what sshd listens on) always keep an allow rule: removing, denying, or enabling the firewall without one is refused.
* `preview=True` returns the exact commands without running them.
* Enabling, disabling and resetting need the typed word `firewall`.
"""
import ipaddress
import re

from .runner import is_local, q, run

PORT_RE = re.compile(r'^(\d{1,5})(:\d{1,5})?$')
ACTIONS = ('allow', 'deny', 'limit')
PROTOS = ('tcp', 'udp', 'any')
ROW_RE = re.compile(r'^(\S+(?: \(v6\))?)\s+(ALLOW|DENY|LIMIT|REJECT)(?: IN| OUT| FWD)?\s+(.+?)\s*$')


def _port(port):
    port = str(port).strip()
    m = PORT_RE.match(port)
    if not m:
        raise ValueError('Port must be a number (1-65535) or a range like 6000:6100')
    lo, hi = int(m.group(1)), int(m.group(2)[1:]) if m.group(2) else int(m.group(1))
    if not (1 <= lo <= 65535 and 1 <= hi <= 65535 and lo <= hi):
        raise ValueError('Port must be between 1 and 65535')
    return port


def _source(src):
    src = str(src or 'any').strip().lower()
    if src in ('', 'any', 'anywhere'):
        return 'any'
    try:
        return str(ipaddress.ip_network(src, strict=False))
    except ValueError:
        raise ValueError('From must be "any", an IP address or a network like 10.0.0.0/8')


def _rule(action, port, proto, source):
    if action not in ACTIONS:
        raise ValueError('Action must be allow, deny or limit')
    if proto not in PROTOS:
        raise ValueError('Protocol must be tcp, udp or any')
    port, source = _port(port), _source(source)
    if source == 'any':
        spec = '%s%s' % (port, '' if proto == 'any' else '/' + proto)
        return port, source, spec
    return port, source, 'from %s to any port %s%s' % (source, port, '' if proto == 'any' else ' proto ' + proto)


def parse(text):
    """`ufw status verbose` -> {active, default_in, rules:[{to, action, from, v6}]}."""
    out = {'active': False, 'default_in': '', 'rules': []}
    for line in text.splitlines():
        if line.startswith('Status:'):
            out['active'] = 'inactive' not in line
        elif line.startswith('Default:'):
            m = re.match(r'Default:\s*(\w+) \(incoming\)', line)
            out['default_in'] = m.group(1) if m else ''
        else:
            m = ROW_RE.match(line)
            if m:
                to, action, frm = m.groups()
                out['rules'].append({'to': to.replace(' (v6)', ''), 'action': action.lower(), 'from': frm.replace(' (v6)', '').strip(), 'v6': '(v6)' in to})
    out['rules'] = [r for r in out['rules'] if not r['v6']] or out['rules']
    return out


def ssh_ports(cfg):
    ports = {int(cfg.get('port') or 22)} if not is_local(cfg) else set()
    rc, out = run(cfg, "sudo sshd -T 2>/dev/null | awk '$1==\"port\"{print $2}'", timeout=20)
    ports |= {int(x) for x in out.split() if x.isdigit()}
    return sorted(ports or {22})


def status(cfg):
    rc, out = run(cfg, 'sudo ufw status verbose 2>&1', timeout=30)
    if 'command not found' in out or 'not found' in out and rc:
        return {'installed': False, 'active': False, 'default_in': '', 'rules': [], 'ssh_ports': ssh_ports(cfg)}
    info = parse(out)
    info['installed'] = True
    info['ssh_ports'] = ssh_ports(cfg)
    return info


def _covers_ssh(rule, ports):
    """Does this allow-rule open one of the SSH ports to everyone (or at least not nobody)?"""
    if rule['action'] not in ('allow', 'limit'):
        return False
    m = PORT_RE.match(rule['to'].split('/')[0])
    if not m:
        return False
    lo, hi = int(m.group(1)), int(m.group(2)[1:]) if m.group(2) else int(m.group(1))
    return any(lo <= p <= hi for p in ports)


def _guard(cfg, info, adding=None, deleting=None):
    """Refuse a change that would leave the SSH ports without an allow rule while the firewall is on."""
    ports = info['ssh_ports']
    rules = [r for r in info['rules']]
    if adding and adding['action'] == 'deny' and adding['source'] == 'any':
        lo, hi = adding['range']
        if any(lo <= p <= hi for p in ports):
            raise ValueError('That would block SSH (port %s), which this app uses to reach the server' % ', '.join(map(str, ports)))
    if deleting:
        left = [r for r in rules if not (r['to'] == deleting['to'] and r['action'] == deleting['action'] and r['from'] == deleting['from'])]
        if info['active'] and info['default_in'] == 'deny' and not any(_covers_ssh(r, ports) for r in left):
            raise ValueError('That is the last rule that lets SSH in (port %s). Removing it would lock you out' % ', '.join(map(str, ports)))


def _range(port):
    m = PORT_RE.match(port)
    return int(m.group(1)), int(m.group(2)[1:]) if m.group(2) else int(m.group(1))


def add(cfg, action, port, proto='tcp', source='any', preview=False):
    port, source, spec = _rule(action, port, proto, source)
    cmd = 'sudo ufw %s %s' % (action, spec)
    if preview:
        return {'commands': [cmd]}
    info = status(cfg)
    if not info['installed']:
        raise RuntimeError('ufw is not installed on the server (Server care can install it)')
    _guard(cfg, info, adding={'action': action, 'source': source, 'range': _range(port)})
    rc, out = run(cfg, cmd + ' 2>&1', timeout=30)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'ufw refused the rule')
    return {'commands': [cmd], 'output': out.strip()}


def delete(cfg, action, port, proto='tcp', source='any', preview=False):
    port, source, spec = _rule(action, port, proto, source)
    cmd = 'sudo ufw --force delete %s %s' % (action, spec)
    if preview:
        return {'commands': [cmd]}
    info = status(cfg)
    to = port + ('' if proto == 'any' else '/' + proto)
    _guard(cfg, info, deleting={'to': to, 'action': action, 'from': 'Anywhere' if source == 'any' else source})
    rc, out = run(cfg, cmd + ' 2>&1', timeout=30)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'ufw refused')
    return {'commands': [cmd], 'output': out.strip()}


def power(cfg, on, confirm, preview=False):
    """Turn the firewall on (SSH is allowed first, so enabling can never lock you out) or off."""
    if confirm != 'firewall' and not preview:
        raise ValueError('Type "firewall" to confirm')
    if not on:
        cmds = ['sudo ufw disable']
    else:
        ports = ssh_ports(cfg)
        cmds = ['sudo ufw allow %d/tcp' % p for p in ports] + ['sudo ufw --force enable']
    if preview:
        return {'commands': cmds}
    rc, out = run(cfg, ' && '.join(cmds) + ' 2>&1', timeout=60)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'ufw failed')
    return {'commands': cmds, 'output': out.strip()}
