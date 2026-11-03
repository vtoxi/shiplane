"""Staging to production: validation, and the real promote.sh run against a throwaway folder tree with shimmed sudo/systemctl/curl.

    python tests/test_promote.py
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import config, deploy  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}


def raises(fn, text):
    try:
        fn()
    except (ValueError, RuntimeError) as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected ' + text)


def with_status(sites):
    deploy.status = lambda cfg: {'sites': sites}


def test_validation():
    with_status([{'name': 'shop-staging', 'type': 'node'}, {'name': 'shop', 'type': 'node'}, {'name': 'blog', 'type': 'static'}, {'name': 'dock', 'type': 'docker'}, {'name': 'dock2', 'type': 'docker'}])
    raises(lambda: deploy.promote(CFG, {'from': 'shop', 'to': 'shop', 'confirm': 'shop'}), 'two different')
    raises(lambda: deploy.promote(CFG, {'from': 'shop-staging', 'to': 'coderunner', 'confirm': 'coderunner'}), 'cannot be part')
    raises(lambda: deploy.promote(CFG, {'from': 'shop-staging', 'to': 'shop', 'confirm': 'yes'}), 'Type the production site name')
    raises(lambda: deploy.promote(CFG, {'from': 'ghost', 'to': 'shop', 'confirm': 'shop'}), 'No site called ghost')
    raises(lambda: deploy.promote(CFG, {'from': 'blog', 'to': 'shop', 'confirm': 'shop'}), 'same type')
    raises(lambda: deploy.promote(CFG, {'from': 'dock', 'to': 'dock2', 'confirm': 'dock2'}), 'Docker')
    raises(lambda: deploy.promote(CFG, {'from': 'Bad Name', 'to': 'shop', 'confirm': 'shop'}), 'bad site name')
    sent = {}
    deploy.start_script_job = lambda cfg, text: sent.setdefault('t', text) and 'job'
    seen = []
    deploy.ON_SITE_ACTION = lambda n, a: seen.append((n, a))
    assert deploy.promote(CFG, {'from': 'shop-staging', 'to': 'shop', 'confirm': 'shop'}) == 'job' and "FROM='shop-staging'" in sent['t'] or "FROM=shop-staging" in sent['t']
    assert seen == [('shop', 'deploy')]


class Tree:
    """A fake server root: scripts see /srv and /etc/deploy-meta inside it; sudo, systemctl and curl are shims."""

    def __init__(self, curl_code='200', active=True):
        self.root = tempfile.mkdtemp()
        self.bin = os.path.join(self.root, 'bin')
        os.makedirs(self.bin)
        self.log = os.path.join(self.root, 'calls.log')
        shims = {'sudo': 'exec "$@"', 'sleep': 'exit 0', 'curl': 'echo %s' % curl_code,
                 'systemctl': 'echo "systemctl $*" >> %s; case "$1" in is-active) %s;; esac; exit 0' % (self.log, 'exit 0' if active else 'echo inactive; exit 3')}
        for name, body in shims.items():
            p = os.path.join(self.bin, name)
            open(p, 'w').write('#!/bin/bash\n%s\n' % body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def site(self, name, typ, files, releases=('20260101-000000',), health='/', hostname=None):
        base = os.path.join(self.root, 'srv', name)
        for r in releases:
            os.makedirs(os.path.join(base, 'releases', r))
            for f, c in files.items():
                open(os.path.join(base, 'releases', r, f), 'w').write(c + r)
        if releases:
            os.symlink(os.path.join(base, 'releases', releases[-1]), os.path.join(base, 'current'))
        os.makedirs(os.path.join(self.root, 'meta'), exist_ok=True)
        json.dump({'name': name, 'type': typ, 'hostname': hostname or name + '.example.com', 'healthPath': health}, open(os.path.join(self.root, 'meta', name + '.json'), 'w'))

    def run(self, src, dst):
        text = config_script().replace('__FROM__', src).replace('__TO__', dst).replace('/srv/', self.root + '/srv/').replace('/etc/deploy-meta/', self.root + '/meta/')
        return subprocess.run(['bash', '-c', text], capture_output=True, text=True, env=dict(os.environ, PATH=self.bin + ':' + os.environ['PATH']))

    def current(self, name):
        return os.path.realpath(os.path.join(self.root, 'srv', name, 'current'))


def config_script():
    return open(os.path.join(config.REMOTE, 'promote.sh')).read()


def test_static_site_is_promoted_and_old_releases_are_pruned():
    t = Tree()
    t.site('shop-staging', 'static', {'index.html': 'NEW-'}, ('20260301-000000',))
    t.site('shop', 'static', {'index.html': 'OLD-'}, ('20260101-000000', '20260102-000000', '20260103-000000', '20260104-000000'))
    r = t.run('shop-staging', 'shop')
    assert r.returncode == 0, r.stdout + r.stderr
    cur = t.current('shop')
    assert open(os.path.join(cur, 'index.html')).read() == 'NEW-20260301-000000' and 'promoted: shop' in r.stdout
    assert len(os.listdir(os.path.join(t.root, 'srv', 'shop', 'releases'))) == 3                         # newest three kept
    assert open(os.path.join(t.root, 'srv', 'shop-staging', 'releases', '20260301-000000', 'index.html')).read() == 'NEW-20260301-000000'   # staging untouched
    assert not os.path.exists(t.log)                                                                       # static sites need no restart
    assert time.time() - os.path.getmtime(cur) < 60                                                        # counts as the newest release for deploy.sh's pruning


def test_service_site_restarts_and_passes_the_health_gate():
    t = Tree('200')
    t.site('app-staging', 'node', {'server.js': 'NEW'}, ('20260301-000000',))
    t.site('app', 'node', {'server.js': 'OLD'})
    r = t.run('app-staging', 'app')
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'systemctl restart app' in open(t.log).read() and open(os.path.join(t.current('app'), 'server.js')).read() == 'NEW20260301-000000'


def test_failed_health_check_rolls_back_to_the_previous_release():
    t = Tree('503')
    t.site('app-staging', 'node', {'server.js': 'NEW'}, ('20260301-000000',))
    t.site('app', 'node', {'server.js': 'OLD'})
    before = t.current('app')
    r = t.run('app-staging', 'app')
    assert r.returncode == 3 and 'HEALTH CHECK FAILED' in r.stdout and 'ROLLED BACK' in r.stdout
    assert t.current('app') == before                                                                      # production is serving the old release again


def test_refusals():
    t = Tree()
    t.site('a-staging', 'node', {'x': '1'}, ('20260301-000000',))
    t.site('a', 'static', {'x': '1'})
    assert 'same type' in t.run('a-staging', 'a').stdout
    t.site('empty-staging', 'node', {}, ())
    t.site('e', 'node', {'x': '1'})
    r = t.run('empty-staging', 'e')
    assert r.returncode == 1 and 'no live release' in r.stdout
    t.site('d1', 'docker', {'x': '1'})
    t.site('d2', 'docker', {'x': '1'})
    assert 'Docker' in t.run('d1', 'd2').stdout
    assert t.run('missing', 'a').returncode == 1


if __name__ == '__main__':
    run_tests(dict(globals()))
