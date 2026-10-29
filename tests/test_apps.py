"""Apps: catalog, folder rules, server status, install planning and the generated scripts (no server needed).

    python tests/test_apps.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import apps, config, deploy  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com'}
ORIGINAL = {(apps, 'run'): apps.run, (apps.deploy, 'start_job'): deploy.start_job, (apps.deploy, 'stream_script'): deploy.stream_script,
            (apps.cloudflare, 'map_hosts'): apps.cloudflare.map_hosts, (apps.config, 'cloudflare_ready'): config.cloudflare_ready, (apps, 'status'): apps.status}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def state(**installed):
    """A server state: state(php=['8.3'], mariadb=['default']) and wordpress instances via the instances key."""
    out = {'os': 'Ubuntu 24.04', 'apps': {a['id']: {'installed': {}, 'system': ''} for a in apps.CATALOG}}
    for app_id, versions in installed.items():
        if app_id == 'instances':
            for i in versions:
                out['apps'].setdefault(i['app'], {'installed': {}, 'system': ''}).setdefault('instances', []).append(i)
        else:
            out['apps'][app_id]['installed'] = {v: 'detail' for v in versions}
    return out


WP = {'id': 'wordpress', 'version': 'latest', 'hostname': 'Blog.Example.com', 'path': '/var/www/blog.example.com', 'php': '8.3'}


# ------------------------------------------------------------------ catalog
def test_catalog_is_consistent():
    ids = [a['id'] for a in apps.CATALOG]
    assert len(ids) == len(set(ids)) and set(ids) == set(apps.BY_ID)
    for a in apps.CATALOG:
        assert a['default'] in a['versions'] and a['kind'] in ('runtime', 'service', 'tool', 'webapp') and len(a['tile']) <= 2
        assert all(r in apps.BY_ID for r in a['requires']) and os.path.isfile(os.path.join(config.REMOTE, 'apps', a['file']))
        assert a['needs_path'] == (a['kind'] == 'webapp')
    assert all('file' not in a for a in apps.catalog()) and len(apps.catalog()) == len(apps.CATALOG)
    assert apps.BY_ID['phpmyadmin']['protect'] and apps.BY_ID['adminer']['protect'] and not apps.BY_ID['wordpress']['protect']


def test_every_install_script_is_valid_bash_with_no_loose_ends():
    params = {'site': 'blog', 'host': 'blog.example.com', 'root': '/var/www/blog', 'php': '8.3', 'ba_user': 'admin', 'ba_pw': 'Secret-Pass-123'}
    for a in apps.CATALOG:
        for version in a['versions']:
            text = apps.render(a['id'], version, params if a['needs_path'] else {})
            assert '__' not in text.replace('__', '', 0) or not apps.re.findall(r'__[A-Z_]+__', text), (a['id'], version)
            p = subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True)
            assert p.returncode == 0, (a['id'], version, p.stderr.decode())


def test_render_quotes_values_and_refuses_unfilled_placeholders():
    text = apps.render('wordpress', 'latest', {'site': 'b', 'host': 'b.example.com', 'root': '/var/www/b', 'php': '8.3', 'ba_user': '', 'ba_pw': ''})
    assert "SITE=b;" in text and "BA_USER=''" in text and 'WPV=latest' in text
    text = apps.render('wordpress', 'latest', {'site': "x'; rm -rf /; '", 'host': 'h.example.com', 'root': '/var/www/b', 'php': '8.3', 'ba_user': '', 'ba_pw': ''})
    assert "SITE='x'\"'\"'; rm -rf /; '\"'\"''" in text                                                 # a hostile value stays one quoted word
    raises(apps.render, 'wordpress', 'latest', {'site': 'b'}, text='Unfilled placeholders')


# ------------------------------------------------------------------ folders
def test_check_path_accepts_only_safe_folders():
    assert apps.check_path('/var/www/blog.example.com') == '/var/www/blog.example.com'
    assert apps.check_path('/home/bob/sites/blog') == '/home/bob/sites/blog' and apps.check_path('/srv/x/y') == '/srv/x/y'
    assert apps.check_path('/var/www', allow_root=True) == '/var/www' and apps.check_path('/var/www/blog/') == '/var/www/blog'
    for bad, text in (('', 'looks wrong'), ('var/www/x', 'looks wrong'), ('/var/www/../etc', 'looks wrong'), ('/var/www//x', 'looks wrong'), ('/var/www/a b', 'looks wrong'),
                      ('/var/www/x;rm', 'looks wrong'), ('/var/www/$HOME', 'looks wrong'), ('/etc/passwd', 'Choose a folder under'), ('/', 'Choose a folder under'),
                      ('/varwww/x', 'Choose a folder under'), ('/var/www', 'not /var/www itself'), ('/srv', 'not /srv itself'), ('/var/www/./x', 'looks wrong'),
                      ('/var/www/' + '/'.join('d%d' % i for i in range(9)), 'nested too deeply'), ('/var/www/-x', 'looks wrong'), (None, 'looks wrong')):
        raises(apps.check_path, bad, text=text)


def test_list_dir_top_level_and_remote_folder():
    top = apps.list_dir(CFG, '/')
    assert top['is_top'] and top['dirs'] == ['var/www', 'srv', 'home'] and top['parent'] is None and apps.list_dir(CFG, '')['is_top']
    listing = {'path': '/var/www', 'exists': True, 'dirs': ['a', 'b'], 'file_count': 1, 'writable': True}
    srv = FakeServer().when('python3', 0, 'noise\n' + json.dumps(listing))
    apps.run = srv.run
    out = apps.list_dir(CFG, '/var/www/')
    assert out['dirs'] == ['a', 'b'] and out['parent'] == '/' and not out['is_top'] and srv.calls[0]['cmd'] == 'sudo python3 - /var/www' and b'scandir' in srv.calls[0]['inp']
    apps.run = FakeServer().when('python3', 0, json.dumps(dict(listing, path='/var/www/blog'))).run
    assert apps.list_dir(CFG, '/var/www/blog')['parent'] == '/var/www'
    raises(apps.list_dir, CFG, '/etc', text='Choose a folder under')
    raises(apps.list_dir, CFG, '/var/www/../x', text='looks wrong')
    apps.run = FakeServer().when('python3', 1, 'sudo: no tty').run
    raises(apps.list_dir, CFG, '/var/www/x', text='no tty', kind=RuntimeError)
    apps.run = FakeServer().when('python3', 0, 'garbage').run
    raises(apps.list_dir, CFG, '/var/www/x', text='garbage', kind=RuntimeError)
    apps.run = FakeServer().when('python3', 1, '').run
    raises(apps.list_dir, CFG, '/var/www/x', text='could not read the folder', kind=RuntimeError)


def test_make_dir():
    srv = FakeServer().when('mkdir', 0, 'created')
    apps.run = srv.run
    assert apps.make_dir(CFG, '/var/www/new.example.com') == '/var/www/new.example.com' and srv.calls[0]['cmd'] == 'sudo mkdir -p /var/www/new.example.com && echo created'
    raises(apps.make_dir, CFG, '/etc/evil', text='Choose a folder under')
    raises(apps.make_dir, CFG, '/var/www', text='not /var/www itself')
    raises(apps.make_dir, CFG, '/var/www/a;b', text='looks wrong')
    assert len(srv.calls) == 1                                                                           # refused paths never reach the server
    apps.run = FakeServer().when('mkdir', 1, 'Permission denied').run
    raises(apps.make_dir, CFG, '/srv/x', text='Permission denied', kind=RuntimeError)
    apps.run = FakeServer().when('mkdir', 1, '').run
    raises(apps.make_dir, CFG, '/srv/x', text='could not create the folder', kind=RuntimeError)


# ------------------------------------------------------------------ status
PROBE = {'os': 'Ubuntu 24.04', 'apps': {'node': {'installed': {'22': 'v22.1.0'}, 'system': 'v18.0.0'}, 'php': {'installed': {'8.3': 'PHP 8.3.1'}, 'system': ''}},
         'instances': [{'app': 'wordpress', 'name': 'blog', 'hostname': 'blog.example.com', 'path': '/var/www/blog', 'version': '6.6.2', 'php': '8.3'}]}


def test_status_merges_probe_and_instances():
    srv = FakeServer().when('python3', 0, json.dumps(PROBE))
    apps.run = srv.run
    out = apps.status(CFG)
    assert out['os'] == 'Ubuntu 24.04' and out['apps']['node']['installed'] == {'22': 'v22.1.0'} and out['apps']['node']['system'] == 'v18.0.0'
    assert out['apps']['wordpress']['instances'][0]['hostname'] == 'blog.example.com' and out['apps']['laravel']['instances'] == []
    assert out['apps']['redis'] == {'installed': {}, 'system': ''} and 'instances' not in out['apps']['node']          # every catalog app has an entry
    assert b'/opt/runtimes' in srv.calls[0]['inp']


def test_status_errors_are_explained():
    apps.run = FakeServer().when('python3', 255, 'ssh: connect to host srv.example.com port 22: Connection timed out').run
    raises(apps.status, CFG, text='Could not reach', kind=RuntimeError)
    apps.run = FakeServer().when('python3', 0, 'no json').run
    raises(apps.status, CFG, text='no json', kind=RuntimeError)
    apps.run = FakeServer().when('python3', 1, '').run
    raises(apps.status, CFG, text='could not read the server', kind=RuntimeError)
    apps.run = FakeServer().when('python3', 0, json.dumps({'apps': {}})).run
    assert apps.status(CFG)['os'] == ''


# ------------------------------------------------------------------ planning
def test_runtime_install_plan_and_its_rules():
    assert apps.plan({'id': 'node', 'version': '22'}, state()) == [('node', '22', {})]
    assert apps.plan({'id': 'node'}, state()) == [('node', '22', {})]                                     # default version
    assert apps.plan({'id': 'node', 'version': '20'}, state(node=['22'])) == [('node', '20', {})]        # another version side by side
    raises(apps.plan, {'id': 'node', 'version': '22'}, state(node=['22']), text='already installed')
    raises(apps.plan, {'id': 'node', 'version': '14'}, state(), text='not available')
    raises(apps.plan, {'id': 'nope'}, state(), text='Unknown app')
    raises(apps.plan, {}, state(), text='Unknown app')
    raises(apps.plan, {'id': 'mariadb'}, state(mariadb=['default']), text='already installed')


def test_requirements_are_reported_or_installed_first():
    raises(apps.plan, WP, state(php=['8.3']), text='Needs MariaDB first')
    raises(apps.plan, WP, state(), text='Needs PHP 8.3, MariaDB first')
    steps = apps.plan(dict(WP, install_requirements=True), state())
    assert [(a, v) for a, v, _ in steps] == [('php', '8.3'), ('mariadb', 'default'), ('wordpress', 'latest')]
    assert [(a, v) for a, v, _ in apps.plan(dict(WP, install_requirements=True), state(php=['8.3'], mariadb=['default']))] == [('wordpress', 'latest')]
    assert [a for a, _, _ in apps.plan(dict(WP, php='8.2', install_requirements=True), state(php=['8.3'], mariadb=['default']))] == ['php', 'wordpress']   # the chosen PHP version is what counts
    raises(apps.plan, {'id': 'composer'}, state(), text='Needs PHP')
    assert apps.plan({'id': 'composer'}, state(php=['8.1'])) == [('composer', '2', {})]                  # any installed PHP will do for a command-line tool
    assert [a for a, _, _ in apps.plan({'id': 'composer', 'install_requirements': True}, state())] == ['php', 'composer']


def test_web_app_input_is_validated():
    ok = state(php=['8.3'], mariadb=['default'])
    (_, version, params), = apps.plan(WP, ok)
    assert version == 'latest' and params == {'site': 'blog-example-com', 'host': 'blog.example.com', 'root': '/var/www/blog.example.com', 'php': '8.3', 'ba_user': '', 'ba_pw': ''}
    assert apps.plan(dict(WP, site='My-Blog'), ok)[-1][2]['site'] == 'my-blog'
    assert apps.plan(dict(WP, php=''), ok)[-1][2]['php'] == '8.3'
    for bad, text in (({'hostname': ''}, 'Type the address'), ({'hostname': 'nodot'}, 'Type the address'), ({'hostname': 'bad host.com'}, 'Type the address'),
                      ({'path': ''}, 'looks wrong'), ({'path': '/etc/x'}, 'Choose a folder under'), ({'path': '/var/www'}, 'not /var/www itself'), ({'path': '/var/www/a;b'}, 'looks wrong'),
                      ({'php': '5.6'}, 'PHP 5.6 is not available'), ({'version': '1.0'}, 'not available'), ({'site': 'Bad Name'}, 'site name looks wrong'), ({'site': 'coderunner'}, 'site name looks wrong')):
        raises(apps.plan, dict(WP, **bad), ok, text=text)


def test_database_tools_need_a_site_password_and_others_may_have_one():
    ok = state(php=['8.3'], mariadb=['default'])
    pma = {'id': 'phpmyadmin', 'version': 'latest', 'hostname': 'db.example.com', 'path': '/var/www/db', 'php': '8.3'}
    raises(apps.plan, pma, ok, text='Site user name')
    raises(apps.plan, dict(pma, protect_user='admin'), ok, text='Site password')
    for bad in ('short', "has'quote-123456", 'has space 123456', 'dollar$sign-123456'):
        raises(apps.plan, dict(pma, protect_user='admin', protect_password=bad), ok, text='Site password')
    raises(apps.plan, dict(pma, protect_user='Ad min', protect_password='Good-Pass-12345'), ok, text='Site user name')
    params = apps.plan(dict(pma, protect_user='admin', protect_password='Good-Pass-12345'), ok)[-1][2]
    assert params['ba_user'] == 'admin' and params['ba_pw'] == 'Good-Pass-12345'
    assert apps.plan(dict(WP, protect_user='meuser', protect_password='Good-Pass-12345'), ok)[-1][2]['ba_user'] == 'meuser'      # optional for other apps
    raises(apps.plan, dict(WP, protect_user='meuser'), ok, text='Site password')


def test_laravel_needs_a_new_enough_php_and_one_app_per_address():
    ok = state(php=['8.1', '8.3'], composer=['2'])
    lv = {'id': 'laravel', 'version': '12', 'hostname': 'app.example.com', 'path': '/var/www/app', 'php': '8.3'}
    assert apps.plan(lv, ok)[-1][1] == '12'
    raises(apps.plan, dict(lv, php='8.1'), ok, text='Laravel 12 needs PHP 8.2 or newer')
    assert apps.plan(dict(lv, version='10', php='8.1'), ok)[-1][1] == '10'
    taken = state(php=['8.3'], mariadb=['default'], instances=[{'app': 'wordpress', 'name': 'blog', 'hostname': 'blog.example.com', 'path': '/x', 'version': '6', 'php': '8.3'}])
    raises(apps.plan, WP, taken, text='WordPress is already installed on blog.example.com')
    raises(apps.plan, dict(WP, hostname='other.example.com', site='blog'), taken, text='already installed')
    assert apps.plan(dict(WP, hostname='second.example.com'), taken)[-1][2]['host'] == 'second.example.com'            # a second site on another address is fine


def test_build_script_stops_at_the_first_error():
    steps = apps.plan(dict(WP, install_requirements=True), state())
    text = apps.build(steps)
    assert text.startswith('set -euo pipefail\n') and text.count('\n(\n') == 3 and text.index('PHP 8.3') < text.index('MariaDB') < text.index('WordPress')
    assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0


# ------------------------------------------------------------------ installing
def test_install_starts_a_job_and_reports_what_it_does():
    apps.status = lambda cfg: state(php=['8.3'], mariadb=['default'])
    started = []
    deploy.start_job = lambda fn, *a: started.append((fn, a)) or 'job-1'
    job, summary = apps.install(CFG, WP)
    assert job == 'job-1' and summary == 'WordPress latest on blog.example.com' and started[0][0] is apps._install_job and started[0][1][2] == []
    job, summary = apps.install(CFG, {'id': 'node', 'version': '22'})
    assert summary == 'Node.js 22' and started[1][1][1].startswith('set -euo pipefail')
    config.cloudflare_ready = lambda: False
    raises(apps.install, CFG, dict(WP, mapCf=True), text='Cloudflare mapping is not set up')
    config.cloudflare_ready = lambda: True
    apps.install(CFG, dict(WP, mapCf=True))
    assert started[-1][1][2] == ['blog.example.com']
    apps.install(CFG, {'id': 'node', 'version': '20', 'mapCf': True})
    assert started[-1][1][2] == []                                                                       # runtimes have no address to map
    raises(apps.install, CFG, {'id': 'node', 'version': '99'}, text='not available')
    assert len(started) == 4


class Job:
    def __init__(self):
        self.lines, self.ok = [], False

    def say(self, line):
        self.lines.append(line)


def test_install_job_outcomes():
    deploy.stream_script = lambda job, cfg, text: 0
    apps.cloudflare.map_hosts = lambda hosts: ['route added: ' + h for h in hosts]
    j = Job()
    apps._install_job(j, CFG, 'script', ['a.example.com'])
    assert j.ok and j.lines == ['Mapping in Cloudflare ...', '  route added: a.example.com', 'DONE']
    j = Job()
    apps._install_job(j, CFG, 'script', [])
    assert j.ok and j.lines == ['DONE']

    def broken(hosts):
        raise RuntimeError('API down')
    apps.cloudflare.map_hosts = broken
    j = Job()
    apps._install_job(j, CFG, 'script', ['a.example.com'])
    assert j.ok and 'Cloudflare mapping FAILED: API down' in j.lines[1] and j.lines[-1] == 'DONE'            # the app is installed even if mapping fails
    deploy.stream_script = lambda job, cfg, text: 3
    j = Job()
    apps._install_job(j, CFG, 'script', ['a.example.com'])
    assert not j.ok and j.lines == ['FAILED (exit code 3)']                                                  # nothing is mapped for a failed install


if __name__ == '__main__':
    run_tests(globals(), before=restore)
