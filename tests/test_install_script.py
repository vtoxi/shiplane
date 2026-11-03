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


def _run_caddy_section(domain_in_other_file, validate_ok, previous, oldhosts='CODERUNNER_ALLOW_HOSTS=server.rankin.work'):
    text = inst.render('shiplane.uk', 'a@b.co', True)
    section = text.split('# >>> caddy-site')[1].split('# <<< caddy-site')[0].split('\n', 1)[1]
    tmp = tempfile.mkdtemp()
    caddy_dir = os.path.join(tmp, 'caddy')
    os.makedirs(os.path.join(caddy_dir, 'sites'))
    open(os.path.join(caddy_dir, 'Caddyfile'), 'w').write('import sites/*.caddy\n')
    if domain_in_other_file:
        open(os.path.join(caddy_dir, 'sites', 'old.caddy'), 'w').write('http://shiplane.uk {\n}\n')
    if previous:
        open(os.path.join(caddy_dir, 'sites', 'coderunner.caddy'), 'w').write('PREVIOUS SITE\n')
    bin_dir = os.path.join(tmp, 'bin')
    os.makedirs(bin_dir)
    for name, body in (('sudo', 'exec "$@"'), ('systemctl', 'echo "systemctl $*" >> %s' % os.path.join(tmp, 'systemctl.log')), ('caddy', 'echo "Error: ambiguous site definition: http://shiplane.uk" >&2; exit 1' if not validate_ok else 'exit 0')):
        path = os.path.join(bin_dir, name)
        open(path, 'w').write('#!/bin/bash\n%s\n' % body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    envf = os.path.join(tmp, 'coderunner.env')
    open(envf, 'w').write('CODERUNNER_AUTH=login\nCODERUNNER_ALLOW_HOSTS=shiplane.uk\nCODERUNNER_DATA=/d\n')            # what the hostname sync already wrote
    script = 'DOMAIN=shiplane.uk\nENVF=%s\nOLDHOSTS=%s\nSITEF=%s/sites/coderunner.caddy\n' % (envf, oldhosts, caddy_dir) + section.replace('/etc/caddy', caddy_dir)
    r = subprocess.run(['bash', '-c', script], env=dict(os.environ, PATH=bin_dir + ':' + os.environ['PATH']), capture_output=True, text=True)
    _run_caddy_section.env = open(envf).read()
    _run_caddy_section.log = open(os.path.join(tmp, 'systemctl.log')).read() if os.path.exists(os.path.join(tmp, 'systemctl.log')) else ''
    return r, os.path.join(caddy_dir, 'sites', 'coderunner.caddy')


def test_a_failed_caddy_check_explains_itself_and_keeps_the_old_site():
    r, site = _run_caddy_section(domain_in_other_file=True, validate_ok=False, previous=True)
    assert r.returncode == 1 and 'Caddy config invalid' in r.stdout and 'ambiguous site definition' in r.stdout          # Caddy's own reason is shown
    assert 'old.caddy' in r.stdout and 'defined twice' in r.stdout                                                       # and the other file that defines the address
    assert open(site).read() == 'PREVIOUS SITE\n' and 'put back' in r.stdout                                              # the working site is not left deleted
    assert 'CODERUNNER_ALLOW_HOSTS=server.rankin.work\n' in _run_caddy_section.env and 'shiplane.uk' not in _run_caddy_section.env   # the old allowed hostname is back
    assert 'systemctl restart coderunner' in _run_caddy_section.log and 'back to: CODERUNNER_ALLOW_HOSTS=server.rankin.work' in r.stdout
    r, site = _run_caddy_section(domain_in_other_file=True, validate_ok=False, previous=False, oldhosts='')                 # a first install had no such line
    assert 'ALLOW_HOSTS' not in _run_caddy_section.env and '(not set)' in r.stdout
    r, site = _run_caddy_section(domain_in_other_file=False, validate_ok=False, previous=False)
    assert r.returncode == 1 and not os.path.exists(site) and 'defined twice' not in r.stdout


def test_a_good_caddy_check_writes_the_site():
    r, site = _run_caddy_section(domain_in_other_file=False, validate_ok=True, previous=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'ALLOW_HOSTS=shiplane.uk' in _run_caddy_section.env and not _run_caddy_section.log                            # success keeps the new hostname and restarts nothing here
    assert open(site).read().startswith('http://shiplane.uk, https://shiplane.uk {') and 'reverse_proxy 127.0.0.1:8765' in open(site).read()


if __name__ == '__main__':
    run_tests(dict(globals()))
