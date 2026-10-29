"""Server-level operations. Only allowlisted services/actions can run; names are validated before use."""
import json
import re

from .runner import run, script

SERVICES = ('cloudflared', 'caddy', 'docker', 'postgresql', 'fail2ban', 'ufw', 'mssql-server', 'mariadb', 'mysql', 'redis-server')
PHP_FPM_RE = re.compile(r'^php\d\.\d-fpm$')
CONTAINER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$')
PORTAL = ('caddy', 'cloudflared')       # when this app runs ON the server, the portal itself is reached through these two
NO_STOP = ('ufw',)                      # stopping ufw would switch the firewall off: use Server care for that
ON_SERVICE_ACTION = None                # set by heal: told when a service is stopped or started on purpose, so auto-heal leaves it alone
# databases need time to shut down cleanly: Docker's default is 10 seconds, and a database killed after that has to recover when it starts again
CONTAINER_ACTIONS = {'start': 'start', 'stop': 'stop -t 60', 'restart': 'restart -t 60'}
POLICY = {'autostart-on': 'unless-stopped', 'autostart-off': 'no'}

UPGRADE_SH = '''set -e
export DEBIAN_FRONTEND=noninteractive
echo "== apt update"; sudo apt-get update -q
echo "== apt upgrade"; sudo apt-get -y -q -o Dpkg::Options::=--force-confold upgrade
echo "== done"; [ -f /var/run/reboot-required ] && echo "A reboot is needed to finish (kernel or core libraries)." || echo "No reboot needed."
'''


def info(cfg):
    rc, out = run(cfg, 'sudo python3 -', inp=script('serverinfo.py').encode(), timeout=150)
    if rc or '{' not in out:
        raise RuntimeError(out.strip()[-400:] or 'could not read server info')
    return json.loads(out[out.index('{'):out.rindex('}') + 1])


def is_service(name):
    return name in SERVICES or bool(PHP_FPM_RE.match(str(name)))


def service(cfg, name, action):
    """start / stop / restart / enable (start at boot) / disable on one allow-listed service, or its logs and status."""
    if not is_service(name):
        raise ValueError('unknown service')
    units = 'docker.socket docker' if name == 'docker' and action in ('stop', 'enable', 'disable') else name     # the socket would start docker again at the next connection
    if action == 'restart':
        rc, out = run(cfg, 'sudo systemctl restart %s && echo "restarted %s ($(systemctl is-active %s))"' % (name, name, name), timeout=90)
        if rc == 0 and ON_SERVICE_ACTION:
            ON_SERVICE_ACTION(name, action)
        return rc, out
    if action == 'logs':
        return run(cfg, 'sudo journalctl -u %s -n 200 --no-pager' % name)
    if action == 'status':
        return run(cfg, 'sudo systemctl status %s --no-pager -n 15' % name)
    if action not in ('start', 'stop', 'enable', 'disable'):
        raise ValueError('unknown action')
    if name in NO_STOP and action in ('stop', 'disable'):
        raise ValueError('Stopping the firewall service would switch the firewall off. Use Server care to manage the firewall.')
    if name in PORTAL and action in ('stop', 'disable') and cfg.get('mode') == 'local':
        raise ValueError('This app is reached through %s: stopping it would cut you off from this page. Stop it from a computer running Shiplane over SSH instead.' % name)
    done = {'start': 'started', 'stop': 'stopped', 'enable': 'will start at boot', 'disable': 'will not start at boot'}[action]
    rc, out = run(cfg, 'sudo systemctl %s %s && echo "%s: %s"' % (action, units, name, done), timeout=90)
    if rc == 0 and ON_SERVICE_ACTION:
        ON_SERVICE_ACTION(name, action)
    return rc, out


def container(cfg, name, action):
    """start / stop / restart a container, set whether it starts by itself (autostart-on / autostart-off), or read its logs."""
    if not CONTAINER_RE.match(name):
        raise ValueError('bad container name')
    if action in CONTAINER_ACTIONS:
        return run(cfg, 'sudo docker %s %s' % (CONTAINER_ACTIONS[action], name), timeout=90)
    if action in POLICY:
        return run(cfg, 'sudo docker update --restart=%s %s' % (POLICY[action], name), timeout=60)
    if action == 'logs':
        return run(cfg, 'sudo docker logs --tail 200 %s 2>&1' % name)
    raise ValueError('unknown action')


def reboot(cfg):
    # delayed so the command returns cleanly before the connection drops
    return run(cfg, 'sudo systemd-run --on-active=3 systemctl reboot && echo "rebooting in 3 seconds"')
