"""The address this app itself is served on (Settings, Server connection): read it, move it to a new hostname, and optionally move the
Cloudflare tunnel route with it. The work is one fixed script, remote/app_address.sh; the hostname is validated here first."""
from . import cloudflare, config, perf
from .runner import q, run, script


def _run(cfg, mode, host='', direct=False):
    rc, out = run(cfg, 'bash -s', inp=script('app_address.sh').replace('__MODE__', q(mode)).replace('__NEW__', q(host))
                  .replace('__DIRECT__', 'true' if direct else 'false').encode(), timeout=60)
    return rc, out.strip()


def show(cfg):
    """{'installed', 'hosts', 'direct'}: what this server's copy of the app currently answers on."""
    rc, out = _run(cfg, 'show')
    if rc:
        raise RuntimeError(out[-300:] or 'could not read the app address')
    kv = dict(l.split('=', 1) for l in out.splitlines() if '=' in l)
    return {'installed': kv.get('INSTALLED') == 'yes', 'hosts': [h for h in kv.get('HOSTS', '').split(',') if h], 'direct': kv.get('DIRECT') == 'yes'}


def change(cfg, a):
    """Move the app to a.hostname. a.confirm must repeat it (a wrong address locks everyone out until it is fixed from the server).
    a.direct: Caddy gets the certificate itself; a.cloudflare: add the new tunnel route and remove the old hostname's own route. Returns the lines to show."""
    new = str(a.get('hostname') or '').strip().lower()
    if not config.HOST_RE.match(new) or '.' not in new:
        raise ValueError('The new address looks wrong (example: shiplane.example.com)')
    if a.get('confirm') != new:
        raise ValueError('Type the new address to confirm')
    cur = show(cfg)
    if not cur['installed']:
        raise ValueError('This app was not installed on the server with install_on_server.py, so its address cannot be changed from here')
    if cur['hosts'] == [new]:
        raise ValueError('The app already answers on %s' % new)
    old = [h for h in cur['hosts'] if h != new]
    rc, out = _run(cfg, 'apply', new, bool(a.get('direct')))
    perf.invalidate('status')
    if rc:
        raise RuntimeError(out[-400:] or 'could not change the address')
    lines = out.splitlines()
    if a.get('cloudflare') and config.cloudflare_ready():
        try:
            lines += cloudflare.map_hosts([new]) if not a.get('direct') else []
            lines += cloudflare.unmap_hosts(old) if old else []
        except Exception as e:  # the app already answers on the new name; say what is left to do
            lines.append('Cloudflare step FAILED: %s. Fix it on the Domains page.' % e)
    return lines
