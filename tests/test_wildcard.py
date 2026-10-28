"""Wildcard certificates: request checks, credentials only inside the script (stdin), and the attach step on real Caddy site files.

    python tests/test_wildcard.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import config, deploy, dnsproviders, wildcard as w  # noqa: E402
from coderunner.runner import script  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
TOKEN = 'dop_v1_' + 'b' * 64
SENT = []


def before():
    SENT.clear()
    dnsproviders.FILE = os.path.join(tempfile.mkdtemp(), 'dnsproviders.json')
    dnsproviders._save({'digitalocean': {'token': TOKEN}, 'ip': '93.184.216.34'})
    dnsproviders._zones = lambda p, c: {'shop.example': 'shop.example'}
    config.cloudflare_ready = lambda: False
    w.run = lambda cfg, cmd, inp=None, timeout=60: (SENT.append((cmd, inp.decode())) or (0, 'CERT|shop.example|Jan 27 10:00:00 2027 GMT\n'))
    deploy.stream_script = lambda job, cfg, text: (SENT.append(('bash -s', text)), job.say('WILDCARD_OK'), 0)[2]


def raises(fn, *a, text=''):
    try:
        fn(*a)
    except ValueError as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('did not raise')


def test_issue_checks_the_request_and_sends_credentials_only_in_the_script():
    assert w.zones() == [{'zone': 'shop.example', 'provider': 'digitalocean'}]
    raises(w.start_issue, CFG, 'other.example', 'me@example.com', text='connected DNS provider')
    raises(w.start_issue, CFG, 'shop.example', 'nope', text='e-mail')
    job = w.start_issue(CFG, 'shop.example', 'me@example.com')
    for _ in range(100):
        if deploy.JOBS[job].done:
            break
        __import__('time').sleep(0.02)
    assert deploy.JOBS[job].ok
    cmd, text = SENT[-1]
    assert cmd == 'bash -s' and TOKEN not in cmd and 'dns_digitalocean_token = ' + TOKEN in text
    assert not any(TOKEN in line for line in deploy.JOBS[job].lines)            # never in the log the browser reads
    assert "PROVIDER=digitalocean" in text and "ZONE=shop.example" in text
    assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0


def test_status_and_remove():
    assert w.state(CFG) == [{'zone': 'shop.example', 'expires': 'Jan 27 10:00:00 2027 GMT'}]
    raises(w.remove, CFG, 'shop.example', 'wrong', text='confirm')
    w.remove(CFG, 'shop.example', 'shop.example')
    assert 'MODE=remove' in SENT[-1][1]


def test_attach_uses_the_wildcard_only_for_direct_sites_one_level_under_the_zone():
    body = re.search(r"sudo tee \$ATTACH >/dev/null <<'SH'\n(.*?)\nSH\n", script('wildcard.sh'), re.S).group(1)
    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, 'sites'))
        os.makedirs(os.path.join(root, 'wildcard'))
        open(os.path.join(root, 'wildcard', 'shop.example.caddy'), 'w').write('(wild_shop_example) {\n}\n')
        files = {'a': 'http://a.shop.example, https://a.shop.example {\n\treverse_proxy 127.0.0.1:5000\n}\n',
                 'apex': 'http://shop.example, https://shop.example {\n\tfile_server\n}\n',
                 'tunnel': 'http://b.shop.example {\n\tfile_server\n}\n',
                 'deep': 'http://x.y.shop.example, https://x.y.shop.example {\n\tfile_server\n}\n',
                 'other': 'http://c.other.org, https://c.other.org {\n\tfile_server\n}\n',
                 'stale': 'http://d.shop.example {\n\timport wild_shop_example\n\tfile_server\n}\n'}
        for n, t in files.items():
            open(os.path.join(root, 'sites', n + '.caddy'), 'w', newline='\n').write(t)
        sh = body.replace('/etc/caddy', root.replace('\\', '/'))
        for _ in range(2):                                    # running it twice changes nothing more
            r = subprocess.run(['bash', '-c', sh], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
        got = {n: open(os.path.join(root, 'sites', n + '.caddy')).read() for n in files}
        assert got['a'].splitlines()[1] == '\timport wild_shop_example' and got['a'].count('import wild_') == 1
        assert got['apex'].splitlines()[1] == '\timport wild_shop_example'
        assert all('import wild_' not in got[n] for n in ('tunnel', 'deep', 'other', 'stale'))
    finally:
        shutil.rmtree(root)


if __name__ == '__main__':
    run_tests(globals(), before=before)
