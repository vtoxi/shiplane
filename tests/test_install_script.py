"""scripts/install_on_server.py: the rendered install script (hostname handling, Caddy site, valid bash) and the allowed-hostname sync, run for real on a temp file.

    python tests/test_install_script.py
"""
import os
import re
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
import install_on_server as inst  # noqa: E402


def test_rendered_script_is_valid_bash_and_has_no_placeholders_left():
    for direct in (False, True):
        text = inst.render('Shiplane.UK', 'Me@Example.com', direct)
        assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0
        assert not re.search(r'__[A-Z]+__', text) and 'DOMAIN=shiplane.uk;' in text


def test_caddy_site_is_http_behind_a_proxy_or_https_when_direct():
    assert 'http://shiplane.uk {' in inst.render('shiplane.uk', 'a@b.co') and 'https://shiplane.uk' not in inst.render('shiplane.uk', 'a@b.co').split('coderunner.caddy')[1][:60]
    assert 'http://shiplane.uk, https://shiplane.uk {' in inst.render('shiplane.uk', 'a@b.co', True)


def test_bad_domains_are_refused():
    for bad in ('', 'nodot', 'bad host.com', 'a.com/../x', 'x.com;rm -rf /', '-a.com', 'a..com', "a.com'; id; '"):
        try:
            inst.render(bad, 'a@b.co')
        except ValueError:
            continue
        raise AssertionError(bad)


def test_the_allowed_hostname_follows_the_new_domain_and_drops_the_old_one():
    text = inst.render('shiplane.uk', 'a@b.co')
    snippet = text.split('# >>> sync-hosts')[1].split('# <<< sync-hosts')[0].split('\n', 1)[1]            # without the rest of the marker line
    tmp = tempfile.mkdtemp()
    shim = os.path.join(tmp, 'sudo')
    open(shim, 'w').write('#!/bin/bash\nexec "$@"\n')
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR)
    env = os.path.join(tmp, 'coderunner.env')
    run = lambda: subprocess.run(['bash', '-c', 'DOMAIN=shiplane.uk; ENVF=%s\n%s' % (env, snippet)], env=dict(os.environ, PATH=tmp + ':' + os.environ['PATH']), capture_output=True, text=True)
    open(env, 'w').write('CODERUNNER_AUTH=login\nCODERUNNER_ALLOW_HOSTS=server.rankin.work\nCODERUNNER_DATA=/var/lib/coderunner\n')
    r = run()
    assert r.returncode == 0, r.stderr
    assert open(env).read() == 'CODERUNNER_AUTH=login\nCODERUNNER_ALLOW_HOSTS=shiplane.uk\nCODERUNNER_DATA=/var/lib/coderunner\n'
    open(env, 'w').write('CODERUNNER_AUTH=login\n')                                                    # an older install without the line gets it
    assert run().returncode == 0 and 'CODERUNNER_ALLOW_HOSTS=shiplane.uk' in open(env).read()


if __name__ == '__main__':
    run_tests(dict(globals()))
