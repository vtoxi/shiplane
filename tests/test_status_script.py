"""The remote status script (runs on the server as root): batching, parsing and the local HTTP probe. Tested here with temp folders and fakes.

    python tests/test_status_script.py
"""
import http.client
import importlib.util
import json
import os
import sys
import tempfile
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('status_script', os.path.join(ROOT, 'coderunner', 'remote', 'status.py'))
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

UNITS = '''Id=api.service
ActiveState=active
SubState=running
MemoryCurrent=52428800
NRestarts=2
ExecMainStartTimestamp=Sun 2026-10-25 20:01:54 UTC

Id=worker.service
ActiveState=failed
SubState=failed
MemoryCurrent=[not set]
NRestarts=0
ExecMainStartTimestamp=
'''


def make_world():
    base = tempfile.mkdtemp()
    for d in ('caddy', 'meta', 'srv', 'etc', 'systemd'):
        os.makedirs(os.path.join(base, d))
    st.CADDY, st.META, st.SRV, st.ETC, st.SYSTEMD = (os.path.join(base, d) for d in ('caddy', 'meta', 'srv', 'etc', 'systemd'))

    def w(rel, text):
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w').write(text)
    w('caddy/api.caddy', 'http://api.example.com {\n  reverse_proxy 127.0.0.1:5000\n}\nhttp://www.api.example.com {\n  redir https://api.example.com{uri}\n}\n')
    w('caddy/worker.caddy', 'http://w.example.com {\n  reverse_proxy 127.0.0.1:5001\n}\n')
    w('caddy/site.caddy', 'http://site.example.com {\n  root * /srv/site/current\n  file_server\n}\n')
    w('caddy/Bad Name.caddy', 'http://x {\n}\n')
    w('systemd/api.service', '[Unit]\n')
    w('systemd/worker.service', '[Unit]\n')
    w('meta/api.json', json.dumps({'hostname': 'api.example.com', 'type': 'node', 'port': 5000}))
    w('meta/site.json', '{broken json')
    w('etc/api.env', 'PORT=5000\nDATABASE_URL=postgresql://x\n# comment\n')
    w('srv/site/releases/20260101-000000/index.html', 'x')
    w('srv/site/releases/20260102-000000/index.html', 'x')
    try:
        os.symlink(os.path.join(base, 'srv/site/releases/20260102-000000'), os.path.join(st.SRV, 'site', 'current'))
        make_world.linked = True
    except (OSError, NotImplementedError):       # creating links needs a privilege on Windows
        make_world.linked = False
    return base


make_world.linked = False


def shell(command):
    if command.startswith('systemctl show'):
        shell.calls.append(command)
        return UNITS
    if 'pg_database' in command:
        shell.calls.append(command)
        return 'postgres\napp_api\napp_site\n'
    return ''


shell.calls = []


def probe(host):
    return {'api.example.com': '200', 'w.example.com': '502'}.get(host, '404')


def test_unit_blocks_are_split_by_unit():
    out = st.unit_blocks(UNITS)
    assert set(out) == {'api.service', 'worker.service'} and out['api.service']['MemoryCurrent'] == '52428800' and out['worker.service']['ActiveState'] == 'failed'
    assert st.unit_blocks('') == {} and st.unit_blocks('\n\n') == {} and st.unit_blocks('garbage without equals') == {}


def test_sites_are_read_with_one_batch_per_kind_of_question():
    make_world()
    shell.calls.clear()
    sites = {s['name']: s for s in st.read_sites(probe=probe, shell=shell)}
    assert sorted(sites) == ['api', 'site', 'worker']                                                  # a bad file name is ignored
    assert len([c for c in shell.calls if c.startswith('systemctl show')]) == 1                        # one systemctl call for every service
    assert len([c for c in shell.calls if 'pg_database' in c]) == 1 and 'api.service worker.service' in shell.calls[0]
    api = sites['api']
    assert api['hostname'] == 'api.example.com' and api['redirects'] == ['www.api.example.com'] and api['type'] == 'node' and api['port'] == 5000
    assert api['has_service'] and api['service'] == 'active' and api['sub'] == 'running' and api['mem'] == 52428800 and api['restarts'] == '2'
    assert api['env_keys'] == ['DATABASE_URL', 'PORT'] and api['db'] is True and api['local_http'] == '200' and api['releases'] == [] and api['current'] is None
    worker = sites['worker']
    assert worker['service'] == 'failed' and worker['mem'] is None and worker['local_http'] == '502' and worker['db'] is False and worker['type'] == 'app'
    site = sites['site']
    assert site['has_service'] is False and 'service' not in site and site['type'] == 'static' and site['hostname'] == 'site.example.com' and site['meta'] == {}
    assert site['releases'] == ['20260101-000000', '20260102-000000'] and site['db'] is True
    assert site['current'] == ('20260102-000000' if make_world.linked else None)


def test_nothing_is_asked_that_is_not_needed():
    base = make_world()
    for f in ('api.caddy', 'worker.caddy'):
        os.remove(os.path.join(base, 'caddy', f))
    shell.calls.clear()
    sites = st.read_sites(probe=probe, shell=shell, postgres=False)
    assert [s['name'] for s in sites] == ['site'] and shell.calls == []                                # no service: no systemctl; postgres off: no psql
    assert sites[0]['db'] is False
    for f in os.listdir(os.path.join(base, 'caddy')):
        os.remove(os.path.join(base, 'caddy', f))
    assert st.read_sites(probe=probe, shell=shell) == []                                               # an empty server is fine


def test_local_http_probe():
    class Conn:
        def __init__(self, host, port, timeout=0):
            self.host = host

        def request(self, method, path, headers=None):
            assert method == 'GET' and path == '/' and headers == {'Host': 'a.example.com'} and self.host == '127.0.0.1'

        def getresponse(self):
            return namedtuple('R', 'status')(204)

        def close(self):
            pass
    assert st.local_http('a.example.com', connect=Conn) == '204'

    class Refused(Conn):
        def request(self, *a, **k):
            raise ConnectionRefusedError('no')

    class Broken(Conn):
        def request(self, *a, **k):
            raise http.client.BadStatusLine('x')
    assert st.local_http('a.example.com', connect=Refused) == '000' and st.local_http('a.example.com', connect=Broken) == '000'


UNIT_FILES = 'ssh.service\nmariadb.service\nphp8.3-fpm.service\nphp8.4-fpm.service\nphp-fpm.service\nmssql-server.service\nredis-server.service\nfoo.service\n'
SHOW = """Id=caddy.service
ActiveState=active
UnitFileState=enabled
MemoryCurrent=31457280

Id=docker.service
ActiveState=active
UnitFileState=enabled
MemoryCurrent=104857600

Id=mssql-server.service
ActiveState=inactive
UnitFileState=disabled
MemoryCurrent=[not set]

Id=mariadb.service
ActiveState=active
UnitFileState=enabled
MemoryCurrent=209715200

Id=php8.3-fpm.service
ActiveState=failed
UnitFileState=enabled
MemoryCurrent=[not set]
"""


def test_os_name_and_server_facts():
    real_rd = st.rd
    try:
        st.rd = lambda p: 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 26.04 LTS"\n' if p == '/etc/os-release' else ''
        assert st.os_name() == 'Ubuntu 26.04 LTS'
        st.rd = lambda p: ''
        assert st.os_name() == 'Linux'
        files = {'/proc/meminfo': 'MemTotal: 4000 kB\nMemAvailable: 1000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\nBroken line\n', '/proc/uptime': '123.4 5.6\n', '/proc/loadavg': '0.1 0.2 0.3 1/2 3\n',
                 '/etc/os-release': 'PRETTY_NAME="Debian 13"\n'}
        st.rd = lambda p: files.get(p, '')
        st.os.statvfs = lambda p: namedtuple('V', 'f_blocks f_frsize f_bavail')(1000, 4096, 250)
        st.os.uname = lambda: namedtuple('U', 'release')('6.8.0')
        calls = []

        def shell(c):
            calls.append(c)
            if c.startswith('systemctl list-unit-files'):
                return UNIT_FILES
            if c.startswith('systemctl show'):
                return SHOW
            if c.startswith('docker ps -a'):
                return 'sqlsrv|Up 3 hours\nportainer|Exited (0) 2 days ago'
            if c.startswith('docker inspect'):
                return '/sqlsrv|unless-stopped|abc123\n/portainer||def456\n'
            return ''
        out = st.read_server(shell=shell)
        assert out['ram_total'] == 4000 * 1024 and out['ram_avail'] == 1000 * 1024 and out['uptime'] == 123.4 and out['load'] == ['0.1', '0.2', '0.3']
        assert out['os'] == 'Debian 13' and out['kernel'] == '6.8.0' and out['disk_total'] == 1000 * 4096 and out['disk_free'] == 250 * 4096
        # the core six are always listed; installed extras and every PHP-FPM version are added; other units are not
        assert list(out['services']) == ['cloudflared', 'caddy', 'docker', 'postgresql', 'fail2ban', 'ufw', 'mssql-server', 'mariadb', 'redis-server', 'php8.3-fpm', 'php8.4-fpm']
        assert out['services']['caddy'] == 'active' and out['services']['mssql-server'] == 'inactive' and out['services']['php8.3-fpm'] == 'failed'
        assert out['services']['ufw'] == 'unknown' and out['services']['php8.4-fpm'] == 'unknown'                     # asked about, but systemctl said nothing
        assert out['service_info']['mariadb'] == {'enabled': 'enabled', 'mem': 209715200} and out['service_info']['mssql-server'] == {'enabled': 'disabled', 'mem': None}
        assert out['service_info']['ufw'] == {'enabled': '', 'mem': None}
        assert len([c for c in calls if c.startswith('systemctl show')]) == 1                                         # one call for every service, however many there are
        assert out['containers'] == ['sqlsrv|Up 3 hours', 'portainer|Exited (0) 2 days ago']                          # stopped containers are listed too, so they can be started
        assert out['container_info']['sqlsrv']['policy'] == 'unless-stopped' and out['container_info']['portainer']['policy'] == 'no'
        calls.clear()
        none = st.read_server(shell=lambda c: 'ssh.service' if c.startswith('systemctl list') else '')
        assert none['containers'] == [] and none['container_info'] == {} and all(v == 'unknown' for v in none['services'].values())      # docker not running: not asked
    finally:
        st.rd = real_rd


def test_container_memory_comes_from_the_cgroup_without_a_subprocess():
    shell = lambda c: 'a|Up' if c.startswith('docker ps') else '/a|always|c0ffee\n'  # noqa: E731
    mem = {'/sys/fs/cgroup/system.slice/docker-c0ffee.scope/memory.current': '52428800\n'}
    lines, info = st.container_facts(shell, read=lambda p: mem.get(p, ''))
    assert lines == ['a|Up'] and info == {'a': {'policy': 'always', 'mem': 52428800}}
    assert st.container_facts(shell, read=lambda p: '')[1]['a']['mem'] is None                                      # cgroup v1 or no accounting: no figure
    assert st.container_facts(lambda c: '', read=lambda p: '') == ([], {})


def test_mysql_is_not_listed_twice_when_it_is_only_an_alias_of_mariadb():
    units = lambda *names: (lambda c: '\n'.join(names) + '\n')  # noqa: E731
    names = st.service_names(units('mariadb.service', 'mysql.service', 'mysqld.service'))
    assert 'mariadb' in names and 'mysql' not in names
    only = st.service_names(units('mysql.service'))
    assert 'mysql' in only and 'mariadb' not in only                                                                  # real MySQL has no MariaDB beside it
    assert st.service_names(lambda c: '') == list(st.SERVICES)                                                        # the core six are always there


def release(files):
    d = tempfile.mkdtemp()
    for name, text in files.items():
        path = os.path.join(d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w').write(text)
    return d


def test_the_framework_is_read_from_a_few_small_files():
    fw = st.detect_framework
    assert fw(release({'package.json': json.dumps({'dependencies': {'@angular/core': '17'}})}), {'type': 'node'}) == 'angular'
    assert fw(release({'package.json': json.dumps({'dependencies': {'next': '14', 'react': '18'}})}), {'type': 'node'}) == 'next'      # the more specific framework wins
    assert fw(release({'package.json': json.dumps({'devDependencies': {'vite': '5'}, 'dependencies': {'vue': '3'}})}), {'type': 'node'}) == 'vue'
    assert fw(release({'package.json': json.dumps({'dependencies': {'express': '4'}})}), {'type': 'node'}) == 'express'
    assert fw(release({'composer.json': json.dumps({'require': {'laravel/framework': '11'}})}), {'type': 'php'}) == 'laravel'
    assert fw(release({'composer.json': json.dumps({'require': {'symfony/framework-bundle': '7'}})}), {'type': 'php'}) == 'symfony'
    assert fw(release({'artisan': '#!/usr/bin/env php'}), {'type': 'php'}) == 'laravel'
    assert fw(release({'wp-config.php': '<?php'}), {'type': 'php'}) == 'wordpress'
    assert fw(release({'wp-includes/x.php': '<?php'}), {'type': 'php'}) == 'wordpress'
    assert fw(release({'configuration.php': '<?php', 'administrator/index.php': ''}), {'type': 'php'}) == 'joomla'


def test_built_static_sites_are_recognised_from_their_index_page():
    fw = st.detect_framework
    assert fw(release({'index.html': '<app-root></app-root><script>ng-version="17"</script>'}), {'type': 'static'}) == 'angular'
    assert fw(release({'index.html': '<div id="root"></div>'}), {'type': 'static'}) == 'react'
    assert fw(release({'index.html': '<div id="app"></div>'}), {'type': 'static'}) == 'vue'
    assert fw(release({'index.html': '<script src="/_next/static/x.js">'}), {'type': 'static'}) == 'next'
    assert fw(release({'index.html': '<link href="/_astro/a.css">'}), {'type': 'static'}) == 'astro'
    assert fw(release({'index.html': '<h1>plain</h1>'}), {'type': 'static'}) == ''
    assert fw(release({'index.html': '<div id="root"></div>'}), {'type': 'php'}) == ''                                          # only static and node sites are read this way


def test_apps_python_and_nothing_known():
    fw = st.detect_framework
    assert fw('', {'app': 'wordpress'}) == 'wordpress' and fw('', {}) == '' and fw('/nonexistent', {'type': 'static'}) == ''
    assert fw(release({}), {'type': 'command', 'start': 'python manage.py runserver 0.0.0.0:$PORT'}) == 'django'
    assert fw(release({}), {'type': 'command', 'start': 'uvicorn main:app --port $PORT'}) == 'fastapi'
    assert fw(release({}), {'type': 'command', 'start': 'gunicorn app:app'}) == 'flask'
    assert fw(release({}), {'type': 'command', 'start': 'node app.js'}) == ''
    assert fw(release({'package.json': '{broken', 'composer.json': '[1]'}), {'type': 'node'}) == ''                           # broken files never raise


def test_every_detectable_framework_has_a_logo_or_falls_back_to_its_language():
    logos = open(os.path.join(ROOT, 'web', 'js', 'logos.js'), encoding='utf-8').read()
    for fw in {f for _, f in st.JS_DEPS} | {f for _, f in st.PHP_DEPS} | {f for _, f in st.HTML_MARKERS} | {f for _, f in st.PY_WORDS} | {'wordpress', 'joomla', 'laravel'}:
        assert '"%s":{' % fw in logos, fw


if __name__ == '__main__':
    run_tests(globals())
