"""Site types in the deploy pipeline: validation and the generated server script, including the PHP type (no server needed).

    python tests/test_deploy_types.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import config, deploy  # noqa: E402

SRC = tempfile.mkdtemp()
config.CF_FILE = os.path.join(SRC, 'no-cloudflare.json')   # never read the real token in tests
os.environ.pop('CLOUDFLARE_API_TOKEN', None)
BASE = {'name': 'demo', 'hostname': 'demo.example.com', 'type': 'static', 'source': SRC}


def raises(fn, *args, text=''):
    try:
        fn(*args)
    except ValueError as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def test_php_sites_need_no_port_and_validate_like_static():
    app = deploy.validate_app(dict(BASE, type='php', redirectFrom=['WWW.demo.example.com', '']))
    assert app['type'] == 'php' and app['port'] == '' and app['redirectFrom'] == ['www.demo.example.com'] and app['mapCf'] is False


def test_validation_rejects_bad_sites():
    for bad, text in (({'name': 'Bad Name'}, 'Name:'), ({'name': 'coderunner'}, 'reserved'), ({'hostname': 'nodot'}, 'Hostname'), ({'hostname': 'bad host.com'}, 'Hostname'),
                      ({'type': 'ruby'}, 'Pick a type'), ({'source': os.path.join(SRC, 'missing')}, 'Source folder'), ({'redirectFrom': ['bad host!']}, 'redirect'),
                      ({'type': 'node'}, 'Port must be a number'), ({'type': 'node', 'port': '80'}, 'free port'), ({'type': 'node', 'port': 8765}, 'free port'),
                      ({'type': 'dotnet', 'port': 5000, 'dll': 'x.exe'}, 'DLL'), ({'type': 'node', 'port': 5000, 'start': '../x.js'}, 'Start file'),
                      ({'type': 'node', 'port': 5000, 'start': 'a.js', 'env': {'1BAD': 'x'}}, 'environment variable'), ({'mapCf': True}, 'Cloudflare')):
        raises(deploy.validate_app, dict(BASE, **bad), text=text)
    node = deploy.validate_app(dict(BASE, type='node', port='5000', start='server/app.mjs', env={'A_B': 1}))
    assert node['port'] == 5000 and node['env'] == {'A_B': '1'}
    assert deploy.validate_app(dict(BASE, type='dotnet', port=5001, dll='My.Api.dll'))['dll'] == 'My.Api.dll'


def rendered(**kw):
    return deploy.deploy_script(deploy.validate_app(dict(BASE, **kw)), 'bob')


def test_php_script_serves_through_php_fpm_without_a_service():
    s = rendered(type='php')
    assert 'php_fastcgi unix//run/php/php-fpm.sock' in s and 'reverse_proxy 127.0.0.1' not in s and 'TYPE=php' in s and 'root * /srv/demo/current' in s
    assert 'chown -R www-data:www-data' in s and 'case "$TYPE" in static|php) SVC=false' in s


def test_other_types_keep_their_behaviour():
    st = rendered(type='static')
    assert 'try_files {path} /index.html' in st and 'php_fastcgi' not in st
    nd = rendered(type='node', port=5000, start='app.js', env={'K': 'v'}, redirectFrom=['www.demo.example.com'])
    assert 'reverse_proxy 127.0.0.1:5000' in nd and "setenv PORT 5000" in nd and "setenv K v" in nd and 'redir https://demo.example.com{uri} permanent' in nd
    dn = rendered(type='dotnet', port=5001, dll='Api.dll')
    assert 'ASPNETCORE_URLS' in dn and 'DLL=Api.dll' in dn


def test_generated_scripts_are_valid_bash():
    for kw in ({'type': 'static'}, {'type': 'php'}, {'type': 'node', 'port': 5000, 'start': 'a.js'}, {'type': 'dotnet', 'port': 5001, 'dll': 'A.dll'}):
        p = subprocess.run(['bash', '-n'], input=rendered(**kw).encode(), capture_output=True)
        assert p.returncode == 0, (kw, p.stderr.decode())


def test_site_action_rules():
    ran = []
    deploy.run = lambda cfg, cmd, inp=None, timeout=60: ran.append(cmd) or (0, 'ok')
    try:
        raises(deploy.site_action, {}, {'name': 'coderunner', 'action': 'delete'}, text='cannot be deleted')
        raises(deploy.site_action, {}, {'name': 'coderunner', 'action': 'stop'}, text='cannot be stopped')
        raises(deploy.site_action, {}, {'name': 'Bad Name', 'action': 'restart'}, text='bad site name')
        raises(deploy.site_action, {}, {'name': 'demo', 'action': 'activate', 'release': '../etc'}, text='bad release')
        raises(deploy.site_action, {}, {'name': 'demo', 'action': 'delete', 'confirm': 'nope'}, text='type the site name')
        raises(deploy.site_action, {}, {'name': 'demo', 'action': 'explode'}, text='unknown action')
        assert not ran
        assert deploy.site_action({}, {'name': 'demo', 'action': 'restart'})[0] == 0 and 'systemctl restart demo' in ran[0]
    finally:
        deploy.run = __import__('coderunner.runner', fromlist=['run']).run


def test_command_sites_validate_and_render():
    for bad, text in (({'type': 'command', 'port': 5000}, 'Start command is required'),
                      ({'type': 'command', 'port': 5000, 'start': 'a\nb'}, 'single line'),
                      ({'type': 'command', 'port': 5000, 'start': 'x' * 501}, 'single line'),
                      ({'type': 'command', 'port': 5000, 'start': 'x', 'build': 'a\x00b'}, 'Build command'),
                      ({'type': 'command', 'start': 'x'}, 'Port must be a number')):
        raises(deploy.validate_app, dict(BASE, **bad), text=text)
    s = rendered(type='command', port=5000, start="python app.py --port $PORT; echo 'hi'", build='pip install -r requirements.txt')
    assert "START='python app.py --port $PORT; echo '\"'\"'hi'\"'\"''" in s and "BUILD='pip install -r requirements.txt'" in s
    assert "setenv PORT 5000" in s and 'reverse_proxy 127.0.0.1:5000' in s and '.shiplane-start.sh' in s and 'sudo -u app_$NAME' in s
    assert '"build": "pip install -r requirements.txt"' in s
    assert subprocess.run(['bash', '-n'], input=s.encode(), capture_output=True).returncode == 0


def test_docker_sites_validate_and_render():
    for bad, text in (({'type': 'docker', 'port': 5000, 'dockerfile': '../x'}, 'Dockerfile path'),
                      ({'type': 'docker', 'port': 5000, 'dockerfile': '/etc/x'}, 'Dockerfile path'),
                      ({'type': 'docker', 'port': 5000, 'cport': 'abc'}, 'Container port'),
                      ({'type': 'docker', 'port': 5000, 'cport': 70000}, 'Container port'),
                      ({'type': 'docker'}, 'Port must be a number')):
        raises(deploy.validate_app, dict(BASE, **bad), text=text)
    app = deploy.validate_app(dict(BASE, type='docker', port=5000))
    assert app['dockerfile'] == 'Dockerfile' and app['cport'] == 8080
    s = rendered(type='docker', port=5000, cport=3000, dockerfile='docker/Dockerfile.prod', env={'K': 'v'})
    assert 'CPORT=3000' in s and 'DOCKERFILE=docker/Dockerfile.prod' in s and 'docker build' in s and 'setenv K v' in s
    assert 'reverse_proxy 127.0.0.1:5000' in s and '"cport": 3000' in s and '__' not in s.replace('__pycache__', '')
    assert subprocess.run(['bash', '-n'], input=s.encode(), capture_output=True).returncode == 0


def test_actions_and_logs_fall_back_to_the_container():
    ran = []
    deploy.run = lambda cfg, cmd, inp=None, timeout=60: ran.append(cmd) or (0, 'ok')
    try:
        deploy.site_action({}, {'name': 'demo', 'action': 'stop'})
        deploy.site_action({}, {'name': 'demo', 'action': 'start'})
        deploy.logs({}, 'demo', 50)
    finally:
        deploy.run = __import__('coderunner.runner', fromlist=['run']).run
    assert 'docker stop -t 30 shiplane-demo' in ran[0] and 'docker start shiplane-demo' in ran[1]
    assert 'docker logs --tail 50 shiplane-demo' in ran[2]


def test_docker_site_status_and_heal():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'coderunner', 'remote'))
    import status
    row = lambda c: '%s' % c
    got = status.docker_site('demo', shell=lambda c: 'running|2|2026-01-01T00:00:00Z|abc', read=lambda p: '1048576\n')
    assert got['service'] == 'active' and got['mem'] == 1048576 and got['restarts'] == '2' and got['has_service']
    assert status.docker_site('demo', shell=lambda c: 'exited|0|x|abc', read=lambda p: '')['service'] == 'failed'
    assert status.docker_site('demo', shell=lambda c: 'paused|0|x|abc', read=lambda p: 'n/a')['mem'] is None
    assert status.docker_site('demo', shell=lambda c: '')['sub'] == 'missing'
    from coderunner import heal
    st = {'sites': [{'name': 'demo', 'type': 'docker', 'has_service': True, 'service': 'failed'}]}
    on = {'sites_down': True, 'sites_hung': True}
    assert heal.candidates(st, {}, on, {}) == []                       # Docker's own restart policy does that job


def test_an_empty_site_gets_a_starter_page_and_is_deployed_like_any_other():
    base = dict(BASE, hostname='demo.example.com<b>')
    raises(deploy.with_starter, dict(BASE, type='docker'), text='Static, PHP, Node or Commands')
    raises(deploy.with_starter, dict(BASE, type='dotnet'), text='Static, PHP, Node or Commands')
    raises(deploy.with_starter, base, text='Hostname')
    st = deploy.with_starter(dict(BASE, type='static'))
    page = open(os.path.join(st['source'], 'index.html'), encoding='utf-8').read()
    assert 'demo.example.com is ready' in page and os.listdir(st['source']) == ['index.html']
    assert os.listdir(deploy.with_starter(dict(BASE, type='php'))['source']) == ['index.php']
    nd = deploy.with_starter(dict(BASE, type='node', port=5200))
    assert sorted(os.listdir(nd['source'])) == ['index.html', 'server.js'] and nd['start'] == 'server.js'
    assert 'process.env.PORT' in open(os.path.join(nd['source'], 'server.js')).read()
    cm = deploy.with_starter(dict(BASE, type='command', port=5201))
    assert cm['start'] == deploy.STARTER_START and deploy.with_starter(dict(BASE, type='command', port=5201, start='node x.js'))['start'] == 'node x.js'
    app = deploy.validate_app(nd)                                           # it goes through the normal checks and script
    s = deploy.deploy_script(app, 'bob')
    assert 'START=server.js' in s and 'reverse_proxy 127.0.0.1:5200' in s
    assert subprocess.run(['bash', '-n'], input=s.encode(), capture_output=True).returncode == 0


if __name__ == '__main__':
    run_tests(globals())
