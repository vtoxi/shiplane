"""The real HTTP server under the performance profile: request limit, cache revalidation, body size cap and log-stream cap.

    python tests/test_server_perf.py
"""
import http.client
import os
import socket
import sys
import tempfile
import threading
import time

os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
os.environ.pop('CODERUNNER_AUTH', None)
os.environ.pop('CODERUNNER_PROFILE', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import perf, server  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


SRV = server.make_server(free_port())
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()
COOKIE = 'cr=%s' % server.STATE['token']


def get(path, headers=None, method='GET'):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request(method, path, headers=dict({'Host': '127.0.0.1:%d' % PORT, 'Cookie': COOKIE}, **(headers or {})))
    r = c.getresponse()
    body = r.read()
    out = (r.status, dict((k.lower(), v) for k, v in r.getheaders()), body)
    c.close()
    return out


def restore():
    perf.slots.resize(perf.PROFILES['light']['threads'])
    perf.streams.resize(perf.PROFILES['light']['streams'])
    while perf.slots.busy > 0:
        perf.slots.release()
    while perf.streams.busy > 0:
        perf.streams.release()
    perf.STATS['refused'] = 0


def test_server_is_the_bounded_one():
    assert isinstance(SRV, server.BoundedServer) and SRV.daemon_threads is True


def test_static_files_are_revalidated_not_downloaded_again():
    status, headers, body = get('/static/js/api.js')
    assert status == 200 and len(body) > 100 and headers['cache-control'] == 'no-cache' and headers['etag'].startswith('"')
    tag = headers['etag']
    status, headers2, body2 = get('/static/js/api.js', {'If-None-Match': tag})
    assert status == 304 and body2 == b'' and headers2['etag'] == tag                                  # nothing sent, nothing read from disk
    status, _, body3 = get('/static/js/api.js', {'If-None-Match': '"stale"'})
    assert status == 200 and body3 == body
    assert get('/static/js/nope.js')[0] == 404


def test_api_answers_are_never_cached():
    status, headers, _ = get('/api/session')
    assert status == 200 and headers['cache-control'] == 'no-store'
    status, headers, _ = get('/static/app.css')
    assert headers['cache-control'] == 'no-cache'


def test_oversized_api_bodies_are_refused_before_they_are_read():
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.putrequest('POST', '/api/session')
    for k, v in (('Host', '127.0.0.1:%d' % PORT), ('Cookie', COOKIE), ('X-Requested-With', 'coderunner'), ('Content-Length', str(server.MAX_JSON + 1))):
        c.putheader(k, v)
    c.endheaders()                                                                                    # the body is never sent: the server must answer without waiting for it
    r = c.getresponse()
    assert r.status == 413 and b'too large' in r.read()
    c.close()


def test_extra_requests_get_a_clear_503_instead_of_piling_up_threads():
    perf.slots.resize(1)
    assert perf.slots.acquire(0)                                                                      # the only slot is taken
    t0 = time.time()
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('GET', '/api/session', headers={'Host': '127.0.0.1:%d' % PORT, 'Cookie': COOKIE})
    r = c.getresponse()
    assert r.status == 503 and r.getheader('Retry-After') == '2' and 1.5 < time.time() - t0 < 5 and perf.STATS['refused'] >= 1
    c.close()
    perf.slots.release()
    assert get('/api/session')[0] == 200                                                              # and service resumes at once when a slot frees


def test_slots_are_released_after_every_request():
    for _ in range(20):
        assert get('/api/session')[0] == 200
    for _ in range(100):                      # the reply reaches the client a moment before the server thread gives its slot back
        if perf.slots.busy == 0:
            break
        time.sleep(0.02)
    assert perf.slots.busy == 0


def test_live_log_streams_are_capped():
    perf.streams.resize(1)
    assert perf.streams.acquire(0)
    status, _, body = get('/api/logs/stream?kind=site&name=demo')
    assert status == 429 and b'Too many live logs' in body
    perf.streams.release()


def test_a_browser_without_the_launch_link_gets_instructions_not_a_dead_end():
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('GET', '/', headers={'Host': '127.0.0.1:%d' % PORT})                                              # no cookie, no token
    r = c.getresponse()
    body = r.read().decode()
    assert r.status == 401 and r.getheader('Content-Type').startswith('text/html')
    assert 'start.bat' in body and '?t=' in body and 'CODERUNNER_TOKEN' in body and 'does not have access yet' in body
    c.close()
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('GET', '/api/session', headers={'Host': '127.0.0.1:%d' % PORT})
    assert c.getresponse().status == 401                                                                       # and the API is still closed to it
    c.close()


def test_performance_endpoints():
    status, _, body = get('/api/perf')
    assert status == 200 and b'"profile": "light"' in body and b'rss_mb' in body
    status, _, body = get('/api/session')
    assert b'"ui_poll": 30' in body


if __name__ == '__main__':
    try:
        run_tests(globals(), before=restore)
    finally:
        SRV.shutdown()
