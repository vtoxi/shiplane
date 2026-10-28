"""Wildcard certificates through a connected DNS provider (remote/wildcard.sh does the work on the server).

Let's Encrypt only gives *.zone through a DNS check, so Certbot uses the provider's API with the credentials already connected on
Integrations. They go to the server inside the script (stdin), are stored root-only for renewals, and never appear in a log or reply.
Only sites served directly (Direct HTTPS) use the certificate; behind the Cloudflare tunnel, Cloudflare serves HTTPS itself.
"""
import re

from . import cloudflare, config, deploy, dnsproviders
from .runner import q, run, script

DOMAIN_RE = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')


def zones():
    """[{zone, provider}] for every zone a connected provider holds (Cloudflare first)."""
    out, seen = [], set()
    if config.cloudflare_ready():
        for z in cloudflare.zone_names():
            out.append({'zone': z, 'provider': 'cloudflare'})
            seen.add(z)
    d = dnsproviders._load()
    for p in dnsproviders.connected():
        for z in sorted(dnsproviders._zones(p, d[p])):
            if z not in seen:
                out.append({'zone': z, 'provider': p})
                seen.add(z)
    return out


def _credentials(provider):
    """The text Certbot's plugin reads, built from the credentials already connected (never sent back to the browser)."""
    if provider == 'cloudflare':
        return 'dns_cloudflare_api_token = %s' % config.cloudflare()['api_token']
    c = dnsproviders._load()[provider]
    if provider == 'digitalocean':
        return 'dns_digitalocean_token = %s' % c['token']
    return '[default]\naws_access_key_id = %s\naws_secret_access_key = %s' % (c['key_id'], c['secret'])


def _script(mode, zone='', provider='', email='', cred=''):
    return (script('wildcard.sh').replace('__MODE__', mode).replace('__ZONE__', q(zone)).replace('__PROVIDER__', q(provider))
            .replace('__EMAIL__', q(email)).replace('__CRED__', q(cred)))


def state(cfg):
    """[{zone, expires}] of the wildcard certificates on the server."""
    rc, out = run(cfg, 'bash -s', inp=_script('status').encode(), timeout=60)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'could not read the certificates')
    return [{'zone': p[1], 'expires': p[2]} for p in (line.split('|') for line in out.splitlines()) if len(p) == 3 and p[0] == 'CERT']


def start_issue(cfg, zone, email):
    """Check the request, then issue in a background job. Returns the job id."""
    zone = str(zone or '').strip().lower()
    email = str(email or '').strip()
    owner = next((z['provider'] for z in zones() if z['zone'] == zone), None)
    if not DOMAIN_RE.match(zone) or not owner:
        raise ValueError('Pick a domain held by a connected DNS provider')
    if not re.match(r'^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$', email):
        raise ValueError("Let's Encrypt needs an e-mail address for expiry notices")
    text = _script('issue', zone, owner, email, _credentials(owner))

    def work(job):
        job.say('Wildcard certificate for *.%s through %s' % (zone, dnsproviders.NAMES.get(owner, 'Cloudflare')))
        rc = deploy.stream_script(job, cfg, text)
        job.ok = rc == 0 and any('WILDCARD_OK' in line for line in job.lines)
    return deploy.start_job(work)


def remove(cfg, zone, confirm):
    zone = str(zone or '').strip().lower()
    if not DOMAIN_RE.match(zone):
        raise ValueError('That domain looks wrong')
    if confirm != zone:
        raise ValueError('Type the domain to confirm')
    rc, out = run(cfg, 'bash -s', inp=_script('remove', zone).encode(), timeout=120)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'could not remove the certificate')
    deploy.perf.invalidate('status')
    return out.strip().splitlines()
