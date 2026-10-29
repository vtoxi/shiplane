"""Unit checks for monitor.py (log commands, uptime logic) and billing.py (against a fake Hetzner API). No network, no SSH."""
import http.server
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
from coderunner import billing, monitor  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(('ok   ' if ok else 'FAIL ') + name)
    fails += 0 if ok else 1


def raises(fn, *a):
    try:
        fn(*a)
    except ValueError:
        return True
    return False


# ---- log commands only accept validated names
check('site log command', 'journalctl -u shop -n 100 -f' in monitor.log_command('site', 'shop'))
check('site name with shell chars refused', raises(monitor.log_command, 'site', 'a;rm -rf /'))
check('service must be allowlisted', raises(monitor.log_command, 'service', 'ssh'))
check('container name refused', raises(monitor.log_command, 'container', 'x y;z'))
check('unknown kind refused', raises(monitor.log_command, 'file', '/etc/passwd'))
check('stream is time-bounded', 'timeout 1800' in monitor.log_command('service', 'caddy'))

# ---- uptime: down only after two misses in a row, back up on the first success
monitor.STATE.clear()
answers = iter([120, None, None, 90, 80])
for _ in range(5):
    monitor.check_once({'shop': 'shop.example.com'}, probe=lambda h: next(answers))
    if _ == 1:
        check('one miss is not down', monitor.health()['sites']['shop']['up'])
    if _ == 2:
        check('two misses is down', not monitor.health()['sites']['shop']['up'])
h = monitor.health()['sites']['shop']
check('recovered', h['up'] and h['checks'] == 5 and h['uptime'] == 60.0)
kinds = [json.loads(l)['kind'] for l in open(os.path.join(os.environ['CODERUNNER_DATA'], 'activity.jsonl'))]
check('down and up are logged', kinds == ['site-down', 'site-up'])
monitor.check_once({}, probe=lambda h: 1)
check('deleted sites drop out', monitor.health()['sites'] == {})
check('history saved to disk', os.path.isfile(monitor.HEALTH_FILE))

# ---- billing against a fake Hetzner API
now = time.time()
created = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(now - 40 * 86400))   # older than a month: the full monthly price


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        ok = self.headers.get('Authorization') in ('Bearer tok', 'Bearer tok' + 'a' * 61)
        body = json.dumps({'servers': [{'name': 'vtoxi', 'created': created, 'datacenter': {'location': {'name': 'hel1'}},
                                        'server_type': {'name': 'cx23', 'prices': [{'location': 'hel1', 'price_monthly': {'gross': '4.9900'}, 'price_hourly': {'gross': '0.0080'}}]},
                                        'included_traffic': 20e12, 'outgoing_traffic': 5e9}]} if ok else {}).encode()
        self.send_response(200 if ok else 401)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.HTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
billing.HZ_API = 'http://127.0.0.1:%d' % srv.server_port
r = billing._hetzner('tok')
check('hetzner monthly price', r['month_total'] == 4.99 and r['servers'][0]['type'] == 'cx23')
check('month-to-date never exceeds the monthly price', 0 < r['month_so_far'] <= 4.99)
try:
    billing._hetzner('bad')
    check('bad token is an error', False)
except RuntimeError as e:
    check('bad token is an error', '401' in str(e))
os.environ.pop('HETZNER_API_TOKEN', None)
check('no token means not ready', billing.overview()['hetzner'] == {'ready': False})
from coderunner import settings  # noqa: E402
check('a token that is not one is refused', raises(billing.hetzner_connect, 'short'))
check('a token Hetzner refuses is not saved', raises(billing.hetzner_connect, 'b' * 64) and not billing.hetzner_connection()['connected'])
out = billing.hetzner_connect('tok' + 'a' * 61)
check('connect checks the token, saves it and never returns it', out['connected'] and out['source'] == 'screen' and 'tok' + 'a' * 61 not in str(out) and billing.hetzner_token() == 'tok' + 'a' * 61)
check('disconnect forgets it', not billing.hetzner_disconnect()['connected'] and billing.hetzner_token() == '')
# the newer API shape: "location" instead of "datacenter"
Fake.shape = 'new'
_old = Fake.do_GET


def _new(self):
    body = json.dumps({'servers': [{'name': 'vtoxi', 'created': created, 'location': {'name': 'hel1'},
                                    'server_type': {'name': 'cx23', 'prices': [{'location': 'hel1', 'price_monthly': {'gross': '4.9900'}, 'price_hourly': {'gross': '0.0080'}}]}}]}).encode()
    self.send_response(200)
    self.send_header('Content-Length', str(len(body)))
    self.end_headers()
    self.wfile.write(body)


Fake.do_GET = _new
r = billing._hetzner('tok')
check('newer API shape (location, no traffic fields)', r['month_total'] == 4.99 and r['servers'][0]['location'] == 'hel1' and r['servers'][0]['traffic_used'] == 0)
srv.shutdown()

print('\nALL PASSED' if not fails else '\n%d FAILED' % fails)
sys.exit(1 if fails else 0)
