"""Direct-HTTPS sites (a domain whose DNS points straight at this server): the Caddy site written for them, www redirects that also answer https://, and switching a site
between tunnel and direct (the real site_direct.sh run on a temp file). Needs no server.

    python tests/test_direct_https.py
"""
import os
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import config, deploy  # noqa: E402

SRC = tempfile.mkdtemp()
config.CF_FILE = os.path.join(SRC, 'no-cloudflare.json')
BASE = {'name': 'shop', 'hostname': 'example.pk', 'type': 'static', 'source': SRC, 'redirectFrom': ['www.example.pk', 'old.example.pk']}


def site_text(**over):
    app = deploy.validate_app(dict(BASE, **over))
    text = deploy.deploy_script(app, 'me')
    return text.split("<<'SITE'\n")[1].split("\nSITE")[0]


def test_a_direct_site_answers_https_on_its_redirected_names_too():
    t = site_text(direct=True)
    blocks = t.split('\n\n')
    assert blocks[0].startswith('http://example.pk, https://example.pk {')
    assert blocks[1].startswith('http://www.example.pk, https://www.example.pk {') and 'redir https://example.pk{uri} permanent' in blocks[1]        # https://www works: it has a certificate
    assert blocks[2].startswith('http://old.example.pk, https://old.example.pk {')


def test_a_tunnel_site_is_unchanged():
    t = site_text(direct=False)
    assert t.split('\n\n')[0].startswith('http://example.pk {') and 'https://example.pk,' not in t and 'http://www.example.pk {' in t


class Env:
    """site_direct.sh with sudo/caddy/systemctl stand-ins and the paths moved into a temp folder."""

    def __init__(self, text):
        self.root = tempfile.mkdtemp()
        self.bin = os.path.join(self.root, 'bin')
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.root, 'sites'))
        os.makedirs(os.path.join(self.root, 'meta'))
        self.file = os.path.join(self.root, 'sites', 'shop.caddy')
        open(self.file, 'w').write(text)
        open(os.path.join(self.root, 'meta', 'shop.json'), 'w').write('{"name": "shop", "hostname": "example.pk"}')
        for name, body in (('sudo', 'exec env "$@"'), ('systemctl', 'exit 0'), ('caddy', 'exit 0')):
            p = os.path.join(self.bin, name)
            open(p, 'w').write('#!/bin/bash\n%s\n' % body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def run(self, mode):
        text = open(os.path.join(config.REMOTE, 'site_direct.sh')).read().replace('__NAME__', 'shop').replace('__MODE__', mode)
        text = text.replace('/etc/caddy/sites/', self.root + '/sites/').replace('/etc/deploy-meta/', self.root + '/meta/')
        r = subprocess.run(['bash', '-c', text], capture_output=True, text=True, env=dict(os.environ, PATH=self.bin + ':' + os.environ['PATH']))
        return r, open(self.file).read()


def test_switching_a_site_to_direct_and_back_covers_its_redirects():
    tunnel = site_text(direct=False)
    env = Env(tunnel)
    r, on = env.run('on')
    assert r.returncode == 0, r.stdout + r.stderr
    assert on == site_text(direct=True)                                                                                    # the same file a direct deploy would have written
    assert '"direct": true' in open(os.path.join(env.root, 'meta', 'shop.json')).read()
    r, off = env.run('off')
    assert r.returncode == 0 and off == tunnel and '"direct": false' in open(os.path.join(env.root, 'meta', 'shop.json')).read()   # and back, exactly
    r, again = env.run('on')
    r, twice = env.run('on')
    assert twice == again                                                                                                  # turning it on twice changes nothing


def test_the_api_passes_the_direct_flag_through_and_everything_else_is_unchanged():
    assert deploy.validate_app(dict(BASE, direct=True))['direct'] is True and deploy.validate_app(dict(BASE))['direct'] is False


if __name__ == '__main__':
    run_tests(dict(globals()))
