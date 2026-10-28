"""A tiny fake of the parts of the Cloudflare API the app uses, so tests never touch the real account."""
import http.server
import json
import re
import threading
import urllib.parse

ACCT, TUN = '0123456789abcdef0123456789abcdef', '00000000-1111-2222-3333-444444444444'
TARGET = TUN + '.cfargotunnel.com'
GOOD = 'testtoken-SECRET-123'
STATE = {}


def reset():
    STATE['zones'] = {'example.com': 'Z1', 'example.org': 'Z2'}
    STATE['config'] = {'ingress': [
        {'hostname': 'example.com', 'service': 'http://127.0.0.1:80'},
        {'hostname': 'www.example.com', 'service': 'http://127.0.0.1:80'},
        {'hostname': '*.example.com', 'service': 'http://127.0.0.1:80'},
        {'service': 'http_status:404'}], 'originRequest': {'noTLSVerify': False}}
    STATE['records'] = [
        {'id': 'r1', 'zone': 'Z1', 'type': 'CNAME', 'name': 'example.com', 'content': TARGET},
        {'id': 'r2', 'zone': 'Z1', 'type': 'CNAME', 'name': 'www.example.com', 'content': TARGET},
        {'id': 'r3', 'zone': 'Z1', 'type': 'CNAME', 'name': '*.example.com', 'content': TARGET},
        {'id': 'r4', 'zone': 'Z1', 'type': 'TXT', 'name': 'example.com', 'content': 'google-site-verification=x'},
        {'id': 'r5', 'zone': 'Z1', 'type': 'A', 'name': 'old.example.com', 'content': '1.2.3.4'}]
    STATE['n'] = 100
    STATE['calls'] = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def handle_any(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        STATE['calls'].append((self.command, u.path))
        if self.headers.get('Authorization') != 'Bearer ' + GOOD:
            return self.send({'success': False, 'errors': [{'message': 'Invalid API Token'}]}, 403)
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)) or 0) or b'{}')
        p = u.path
        if p == '/user/tokens/verify':
            return self.send({'success': True, 'result': {'status': 'active'}})
        if p == '/accounts':
            return self.send({'success': True, 'result': [{'id': ACCT, 'name': 'Test account'}]})
        if p == '/accounts/%s/cfd_tunnel' % ACCT:
            return self.send({'success': True, 'result': [{'id': TUN, 'name': 'coderunner', 'status': 'healthy'}]})
        if p == '/accounts/%s/cfd_tunnel/%s' % (ACCT, TUN):
            return self.send({'success': True, 'result': {'name': 'coderunner', 'status': 'healthy', 'connections': [1, 2, 3, 4]}})
        if p == '/zones':
            n = qs['name'][0]
            return self.send({'success': True, 'result': [{'id': STATE['zones'][n], 'name': n}] if n in STATE['zones'] else []})
        if p == '/accounts/%s/cfd_tunnel/%s/configurations' % (ACCT, TUN):
            if self.command == 'PUT':
                STATE['config'] = body['config']
            return self.send({'success': True, 'result': {'config': STATE['config']}})
        m = re.match(r'^/zones/(Z\d)/dns_records(?:/(\w+))?$', p)
        if m:
            z, rid = m.groups()
            if self.command == 'GET':
                return self.send({'success': True, 'result': [r for r in STATE['records'] if r['zone'] == z and r['name'] == qs['name'][0]]})
            if self.command == 'POST':
                STATE['n'] += 1
                STATE['records'].append({'id': 'r%d' % STATE['n'], 'zone': z, 'type': body['type'], 'name': body['name'], 'content': body['content'], 'proxied': body['proxied']})
                return self.send({'success': True, 'result': {}})
            if self.command == 'DELETE':
                STATE['records'] = [r for r in STATE['records'] if r['id'] != rid]
                return self.send({'success': True, 'result': {'id': rid}})
        self.send({'success': False, 'errors': [{'message': 'unknown route ' + p}]}, 404)

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


def start():
    reset()
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://127.0.0.1:%d' % srv.server_port


def pristine():
    return ({r['id'] for r in STATE['records']} == {'r1', 'r2', 'r3', 'r4', 'r5'} and len(STATE['config']['ingress']) == 4
            and STATE['config']['ingress'][-1] == {'service': 'http_status:404'})
