"""Changing a site's address: request checks, the DNS move order, and the Caddy rewrite on a real-looking site file.

    python tests/test_rename.py
"""
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import cloudflare, config, deploy, dnsproviders  # noqa: E402
from coderunner.runner import script  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
SENT, MOVES = [], []


def before():
    SENT.clear()
    MOVES.clear()
    deploy.status = lambda cfg: {'sites': [{'name': 'shop', 'hostname': 'store.example.com', 'redirects': ['www.store.example.com']}]}
    deploy.run = lambda cfg, cmd, inp=None, timeout=60: (SENT.append(inp.decode()) or (0, 'Caddy now serves shop at shop.example.com\n'))
    config.cloudflare_ready = lambda: True
    cloudflare.map_hosts = lambda hosts: MOVES.append(('map', hosts)) or ['added ' + hosts[0]]
    cloudflare.unmap_hosts = lambda hosts: MOVES.append(('unmap', hosts)) or ['removed ' + hosts[0]]
    dnsproviders.connected = lambda: []


def raises(fn, a, text):
    try:
        fn(CFG, a)
    except ValueError as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('did not raise')


def test_requests_are_checked():
    raises(deploy.rename_host, {'name': 'coderunner', 'hostname': 'x.example.com'}, 'this app itself')
    raises(deploy.rename_host, {'name': 'shop', 'hostname': 'bad name'}, 'looks wrong')
    raises(deploy.rename_host, {'name': 'shop', 'hostname': 'store.example.com'}, 'already its address')
    raises(deploy.rename_host, {'name': 'shop', 'hostname': 'www.store.example.com'}, 'redirects')
    raises(deploy.rename_host, {'name': 'ghost', 'hostname': 'x.example.com'}, 'No site')
    assert not SENT


def test_dns_moves_new_first_and_only_when_asked():
    lines = deploy.rename_host(CFG, {'name': 'shop', 'hostname': 'Shop.Example.com', 'mapCf': True, 'env': True})
    assert "NEW=shop.example.com" in SENT[-1] and "OLD=store.example.com" in SENT[-1] and 'ENVTOO=true' in SENT[-1]
    assert MOVES == [('map', ['shop.example.com']), ('unmap', ['store.example.com'])] and lines[-1] == 'removed store.example.com'
    MOVES.clear()
    deploy.rename_host(CFG, {'name': 'shop', 'hostname': 'shop.example.com'})
    assert not MOVES and 'ENVTOO=false' in SENT[-1]


def test_caddy_rewrite_touches_only_the_whole_old_name():
    body = re.search(r"sudo python3 - \"\$F\" \"\$OLD\" \"\$NEW\" <<'PY'\n(.*?)\nPY\n", script('rename_host.sh'), re.S).group(1)
    d = tempfile.mkdtemp()
    f = os.path.join(d, 'shop.caddy')
    open(f, 'w', newline='\n').write('http://store.example.com, https://store.example.com {\n\treverse_proxy 127.0.0.1:5000\n}\n\n'
                                    'http://www.store.example.com {\n\tredir https://store.example.com{uri} permanent\n}\n\n'
                                    '# not mine: xstore.example.com store.example.com.au\n')
    r = subprocess.run([sys.executable, '-c', body, f, 'store.example.com', 'shop.example.com'], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    t = open(f).read()
    assert t.startswith('http://shop.example.com, https://shop.example.com {')
    assert 'redir https://shop.example.com{uri}' in t and 'http://www.store.example.com {' in t      # the redirect source keeps its own name
    assert 'xstore.example.com' in t and 'store.example.com.au' in t
    r = subprocess.run([sys.executable, '-c', body, f, 'nothere.example.com', 'a.example.com'], capture_output=True, text=True)
    assert r.returncode != 0 and 'not found' in r.stderr
    assert subprocess.run(['bash', '-n'], input=script('rename_host.sh').encode(), capture_output=True).returncode == 0


if __name__ == '__main__':
    run_tests(globals(), before=before)
