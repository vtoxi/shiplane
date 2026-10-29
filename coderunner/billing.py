"""Billing overview. Hetzner: the server's price and an estimate for this month (needs a READ-ONLY Hetzner Cloud API token).
Cloudflare: the account's paid subscriptions (the token needs "Billing: Read"); no subscriptions means the free plan.
Neither provider exposes invoices through its API, so the numbers are estimates and the card says so."""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config, settings

HZ_API = os.environ.get('HETZNER_API_BASE', 'https://api.hetzner.cloud/v1')
HZ_FILE = os.path.join(config.ROOT, 'hetzner.json')


def hetzner_token():
    """The token saved on the Integrations page; HETZNER_API_TOKEN or an old hetzner.json still work as overrides."""
    t = os.environ.get('HETZNER_API_TOKEN', '') or settings.get('hetzner').get('api_token', '')
    if not t:
        try:
            with open(HZ_FILE, encoding='utf-8') as f:
                t = json.load(f).get('api_token', '')
        except (OSError, ValueError):
            pass
    return str(t).strip()


def hetzner_connection():
    """What the screen may know: connected or not, and from where. Never the token."""
    src = 'environment' if os.environ.get('HETZNER_API_TOKEN') else 'screen' if settings.get('hetzner').get('api_token') else 'file' if hetzner_token() else ''
    return {'connected': bool(src), 'source': src}


def hetzner_connect(token):
    """Check a pasted token by listing the project's servers, then save it (write-only)."""
    token = str(token or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9]{32,128}', token):
        raise ValueError('That does not look like a Hetzner Cloud API token (64 letters and digits)')
    try:
        n = len(_get(HZ_API + '/servers', token).get('servers', []))
    except RuntimeError as e:
        raise ValueError('Hetzner refused the token (%s)' % e)
    settings.put('hetzner', {'api_token': token})
    return dict(hetzner_connection(), servers=n)


def hetzner_disconnect():
    settings.put('hetzner', {})
    return hetzner_connection()


def _get(url, token):
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'coderunner'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError('HTTP %s' % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(str(getattr(e, 'reason', e)))


def _hetzner(token, now=None):
    now = now or time.time()
    month = time.gmtime(now)
    start = time.mktime((month.tm_year, month.tm_mon, 1, 0, 0, 0, 0, 0, 0)) - time.timezone
    servers, total = [], 0.0
    for s in _get(HZ_API + '/servers', token).get('servers', []):
        # newer API versions drop "datacenter" in favour of "location"
        loc = (s.get('location') or s['datacenter']['location'])['name']
        prices = s['server_type'].get('prices') or []
        if not prices:
            raise RuntimeError('Hetzner returned no price for %s' % s['server_type'].get('name'))
        price = next((p for p in prices if p['location'] == loc), prices[0])
        monthly, hourly = float(price['price_monthly']['gross']), float(price['price_hourly']['gross'])
        created = time.mktime(time.strptime(s['created'][:19], '%Y-%m-%dT%H:%M:%S')) - time.timezone
        hours = max(0.0, now - max(created, start)) / 3600
        so_far = min(monthly, hours * hourly)   # Hetzner bills hourly and caps at the monthly price
        total += so_far
        servers.append({'name': s['name'], 'type': s['server_type']['name'], 'location': loc, 'monthly': round(monthly, 2),
                        'so_far': round(so_far, 2), 'traffic_used': s.get('outgoing_traffic') or 0, 'traffic_included': s.get('included_traffic') or 0})
    return {'servers': servers, 'month_so_far': round(total, 2), 'month_total': round(sum(x['monthly'] for x in servers), 2), 'currency': 'EUR'}


def _cloudflare():
    c = config.cloudflare()
    if not (c['api_token'] and c['account_id']):
        raise RuntimeError('not configured')
    data = _get('%s/accounts/%s/subscriptions' % (config.CF_API, c['account_id']), c['api_token'])
    items = [{'name': (s.get('rate_plan') or {}).get('public_name') or s.get('product', {}).get('name', 'Subscription'),
              'price': s.get('price', 0), 'currency': s.get('currency', 'USD'), 'frequency': s.get('frequency', ''),
              'renews': (s.get('current_period_end') or '')[:10]} for s in data.get('result', [])]
    return {'items': items, 'paid': round(sum(i['price'] for i in items), 2)}


def overview():
    out = {'hetzner': {'ready': False}, 'cloudflare': {'ready': False}}
    tok = hetzner_token()
    if tok:
        try:
            out['hetzner'] = dict(_hetzner(tok), ready=True)
        except Exception as e:
            out['hetzner'] = {'ready': False, 'error': str(e)}
    try:
        out['cloudflare'] = dict(_cloudflare(), ready=True)
    except Exception as e:
        out['cloudflare'] = {'ready': False, 'error': str(e)}
    return out
