"""A read-only public status page, protected by an unguessable link token. It shows site names, up/down and uptime only: no addresses, no server details."""
import hmac
import secrets

from . import monitor, settings

DEFAULTS = {'enabled': False, 'title': 'Status', 'token': '', 'sites': []}   # sites: names to show; empty = all


def get():
    c = dict(DEFAULTS)
    c.update(settings.get('statuspage'))
    return c


def public_config():
    c = get()
    return {'enabled': c['enabled'], 'title': c['title'], 'sites': c['sites'], 'path': '/status?t=' + c['token'] if c['enabled'] and c['token'] else ''}


def save(enabled, title, sites, rotate=False):
    title = str(title or 'Status').strip()[:60] or 'Status'
    if not isinstance(sites, list) or len(sites) > 200 or any(not isinstance(s, str) or len(s) > 60 for s in sites):
        raise ValueError('Sites must be a list of site names')
    c = get()
    c.update(enabled=bool(enabled), title=title, sites=sites)
    if (c['enabled'] and not c['token']) or rotate:
        c['token'] = secrets.token_urlsafe(24)
    settings.put('statuspage', c)
    return public_config()


def public_data(token):
    """The page's data for a valid token; LookupError (answered 404, same as an unknown page) otherwise."""
    c = get()
    if not c['enabled'] or not c['token'] or not hmac.compare_digest(str(token or ''), c['token']):
        raise LookupError('not found')
    health = monitor.health()['sites']
    names = [n for n in sorted(health) if not c['sites'] or n in c['sites']]
    sites = [{'name': n, 'up': health[n]['up'], 'uptime': health[n]['uptime']} for n in names]
    overall = 'unknown' if not sites else 'ok' if all(s['up'] for s in sites) else 'down' if not any(s['up'] for s in sites) else 'degraded'
    return {'title': c['title'], 'overall': overall, 'sites': sites}
