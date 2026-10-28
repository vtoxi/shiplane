"""Deploying and managing sites: validation, the remote scripts, background jobs, status."""
import concurrent.futures
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import activity, alerts, cloudflare, config, dnsproviders, perf, settings
from .runner import NOWIN, popen, q, run, script

JOBS = {}
ON_SITE_ACTION = None     # set by heal: told about every start/stop/restart/deploy/delete so auto-heal respects what the user did
PUBLIC_LOOKUP = None     # set by monitor: a recent uptime result for a host, so status does not probe the same address again
PROTECTED = {'coderunner'}   # the site that serves this app: cannot be redeployed over, stopped or deleted from itself


# ------------------------------------------------------------------ validation
STARTER_TYPES = ('static', 'php', 'node', 'command')
STARTER_START = 'python3 -m http.server $PORT --bind 127.0.0.1'
STARTER_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%(host)s</title>'
    '<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f5f7;color:#111827;font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}'
    'main{max-width:30rem;margin:1.5rem;padding:2.2rem;background:#fff;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}h1{margin:0 0 .5rem;font-size:1.4rem}'
    'p{margin:.4rem 0;color:#4b5563}code{background:#f3f4f6;padding:.1rem .35rem;border-radius:4px}</style></head><body><main>'
    '<h1>%(host)s is ready</h1><p>This is a starter page. The address, the certificate and the server are set up.</p>'
    '<p>Put your own site here with <code>Redeploy</code> on its page in Shiplane, or connect a Git repository.</p></main></body></html>\n')
STARTER_NODE = (
    "const http = require('http');\nconst page = require('fs').readFileSync(__dirname + '/index.html');\n"
    "http.createServer((req, res) => { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(page); })\n"
    "  .listen(process.env.PORT || 3000, process.env.HOST || '127.0.0.1');\n")


def with_starter(a):
    """The same request, but with a source folder that holds a small starter page: for a site created before there is any code to upload."""
    t = a.get('type')
    if t not in STARTER_TYPES:
        raise ValueError('An empty site can be Static, PHP, Node or Commands')
    host = str(a.get('hostname', '')).strip().lower()
    if not config.HOST_RE.match(host) or '.' not in host:
        raise ValueError('Hostname looks wrong')
    d = tempfile.mkdtemp(prefix='shiplane-empty-')
    page = STARTER_PAGE % {'host': html.escape(host)}
    files = {'index.php' if t == 'php' else 'index.html': page}
    if t == 'node':
        files['server.js'] = STARTER_NODE
    for name, text in files.items():
        with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
            f.write(text)
    out = dict(a, source=d)
    if t == 'node':
        out['start'] = 'server.js'
    if t == 'command':
        out['start'] = str(a.get('start') or '').strip() or STARTER_START
        out['build'] = out.get('build') or ''
    return out


def _one_line(v, label, required=True):
    """A shell command typed by a super admin: one line, no control characters, bounded."""
    s = str(v or '').strip()
    if len(s) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in s):
        raise ValueError('%s must be a single line of at most 500 characters' % label)
    return s


def validate_app(a, need_source=True):
    app = {'name': str(a.get('name', '')).strip().lower(), 'hostname': str(a.get('hostname', '')).strip().lower(),
           'type': a.get('type'), 'postgresDb': bool(a.get('postgresDb')), 'mapCf': bool(a.get('mapCf')), 'mapDns': bool(a.get('mapDns')), 'direct': bool(a.get('direct'))}
    if not config.NAME_RE.match(app['name']):
        raise ValueError('Name: use a-z, 0-9 and - only')
    if app['name'] in PROTECTED:
        raise ValueError('That name is reserved for this app itself')
    if not config.HOST_RE.match(app['hostname']) or '.' not in app['hostname']:
        raise ValueError('Hostname looks wrong')
    if app['type'] not in ('static', 'dotnet', 'node', 'php', 'command', 'docker'):
        raise ValueError('Pick a type')
    src = os.path.expanduser(str(a.get('source', '')).strip())
    if need_source and not os.path.isdir(src):
        raise ValueError('Source folder not found: %s' % src)
    app['source'] = src
    app['redirectFrom'] = []
    for r in a.get('redirectFrom') or []:
        r = str(r).strip().lower()
        if r:
            if not config.HOST_RE.match(r):
                raise ValueError('Bad redirect hostname: ' + r)
            app['redirectFrom'].append(r)
    app['port'] = ''
    app['dll'] = app['start'] = app['build'] = app['dockerfile'] = ''
    app['cport'] = ''
    if app['type'] in ('dotnet', 'node', 'command', 'docker'):
        try:
            app['port'] = int(a.get('port'))
        except (TypeError, ValueError):
            raise ValueError('Port must be a number')
        if not 1024 <= app['port'] <= 65535 or app['port'] in (1433, 5432, 9443, 2019, 8765):
            raise ValueError('Pick a free port between 1024 and 65535 (not 1433, 5432, 8765, 9443 or 2019)')
    app['healthPath'] = str(a.get('healthPath') or '/').strip() or '/'
    if not re.match(r'^/[\w./~%:+=&?-]{0,198}$', app['healthPath']) or '..' in app['healthPath']:
        raise ValueError('Health check path looks wrong (example: /health/ready)')
    app['healthStatus'] = ''
    if str(a.get('healthStatus') or '').strip():
        try:
            app['healthStatus'] = int(a.get('healthStatus'))
        except (TypeError, ValueError):
            raise ValueError('Expected health status must be a number such as 200')
        if not 100 <= app['healthStatus'] <= 599:
            raise ValueError('Expected health status must be between 100 and 599')
    app['runtime'] = str(a.get('runtime') or '').strip()
    if app['runtime'] and (app['type'] != 'node' or not re.match(r'^/(usr|opt)/[\w.@/-]+/node$', app['runtime']) or '..' in app['runtime']):
        raise ValueError('Pick a Node runtime from the list (it must be installed under /usr or /opt)')
    if app['type'] == 'dotnet':
        app['dll'] = str(a.get('dll', '')).strip()
        if not re.match(r'^[\w.-]+\.dll$', app['dll']):
            raise ValueError('DLL name looks wrong (example: MyApi.dll)')
    if app['type'] == 'node':
        app['start'] = str(a.get('start', '')).strip()
        if not re.match(r'^[\w./-]+$', app['start']) or '..' in app['start']:
            raise ValueError('Start file looks wrong (example: server/server.mjs)')
    if app['type'] == 'command':
        app['start'] = _one_line(a.get('start'), 'Start command')
        app['build'] = _one_line(a.get('build'), 'Build command', required=False)
        if not app['start']:
            raise ValueError('Start command is required')
    if app['type'] == 'docker':
        app['dockerfile'] = str(a.get('dockerfile') or 'Dockerfile').strip()
        if not re.match(r'^[\w./-]+$', app['dockerfile']) or '..' in app['dockerfile'] or app['dockerfile'].startswith('/'):
            raise ValueError('Dockerfile path looks wrong (example: Dockerfile or docker/Dockerfile.prod)')
        try:
            app['cport'] = int(a.get('cport') or 8080)
        except (TypeError, ValueError):
            raise ValueError('Container port must be a number')
        if not 1 <= app['cport'] <= 65535:
            raise ValueError('Container port must be between 1 and 65535')
    app['env'] = {}
    for k, v in (a.get('env') or {}).items():
        if not config.KEY_RE.match(k):
            raise ValueError('Bad environment variable name: ' + k)
        app['env'][k] = str(v)
    if app['mapCf'] and not config.cloudflare_ready():
        raise ValueError('Cloudflare mapping is not set up on this computer')
    own = settings.get('dns').get('zones') or {}
    if any(app['hostname'] == z or app['hostname'].endswith('.' + z) for z in own):
        app['direct'] = True              # a domain on our own nameservers points straight at the server: no tunnel, Caddy serves HTTPS
    if app['mapDns']:
        if app['mapCf']:
            raise ValueError('Map the hostname in Cloudflare or in a DNS provider, not both')
        if not dnsproviders.connected():
            raise ValueError('No DNS provider is connected: connect Route 53 or DigitalOcean on Integrations')
        app['direct'] = True              # an A record to the server: Caddy must answer on 443 itself
    return app


def deploy_script(app, user):
    t = app['type']
    env = ['setenv %s %s' % (q(k), q(v)) for k, v in app['env'].items()]
    if t == 'dotnet':
        env.append('setenv ASPNETCORE_URLS %s' % q('http://127.0.0.1:%s' % app['port']))
    if t in ('node', 'command'):
        env += ['setenv PORT %s' % q(app['port']), "setenv HOST '127.0.0.1'"]
    if t == 'static':
        body = '\troot * /srv/%s/current\n\tencode gzip\n\ttry_files {path} /index.html\n\tfile_server' % app['name']
    elif t == 'php':
        https = '' if app.get('direct') else ' {\n\t\tenv HTTPS on\n\t}'   # behind the tunnel Caddy only sees http
        body = '\troot * /srv/%s/current\n\tencode gzip\n\tphp_fastcgi unix//run/php/php-fpm.sock%s\n\tfile_server' % (app['name'], https)
    elif app.get('direct'):
        body = '\tencode gzip\n\treverse_proxy 127.0.0.1:%s' % app['port']
    else:   # tunnel traffic arrives as http, so tell the app the visitor used https (secure cookies, redirects)
        body = '\tencode gzip\n\treverse_proxy 127.0.0.1:%s {\n\t\theader_up X-Forwarded-Proto https\n\t}' % app['port']
    head = 'http://%s, https://%s' % (app['hostname'], app['hostname']) if app.get('direct') else 'http://%s' % app['hostname']
    site = '%s {\n%s\n}' % (head, body)
    for r in app['redirectFrom']:
        site += '\n\nhttp://%s {\n\tredir https://%s{uri} permanent\n}' % (r, app['hostname'])
    meta = {k: app[k] for k in ('name', 'hostname', 'type', 'port', 'dll', 'start', 'postgresDb', 'redirectFrom')}
    if t == 'command':
        meta['build'] = app['build']
    if t == 'docker':
        meta.update(cport=app['cport'], dockerfile=app['dockerfile'])
    if app.get('direct'):
        meta['direct'] = True
    if app['type'] not in ('static', 'php'):
        meta.update(healthPath=app.get('healthPath') or '/', healthStatus=app.get('healthStatus') or '', runtime=app.get('runtime') or '')
    meta['deployedBy'] = user
    meta['deployedAt'] = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    if app.get('git'):
        meta['git'] = app['git']            # which commit this release was built from
    return (script('deploy_docker.sh' if t == 'docker' else 'deploy.sh').replace('__NAME__', app['name']).replace('__HOST__', app['hostname']).replace('__TYPE__', t)
            .replace('__CPORT__', str(app.get('cport', ''))).replace('__DOCKERFILE__', q(app.get('dockerfile', '')))
            .replace('__BUILD__', q(app.get('build', '')))
            .replace('__HEALTH__', q(app.get('healthPath') or '/')).replace('__EXPECT__', q(app.get('healthStatus') or '')).replace('__NODE__', q(app.get('runtime') or ''))
            .replace('__PORT__', str(app['port'])).replace('__DLL__', q(app['dll'])).replace('__START__', q(app['start']))
            .replace('__DB__', 'true' if app['postgresDb'] else 'false').replace('__ENV__', '\n  '.join(env))
            .replace('__META__', json.dumps(meta, indent=1)).replace('__SITE__', site))


# ------------------------------------------------------------------------ jobs
class Job:
    """Output of a background task. Only the newest lines are kept (the profile's limit), so a long apt upgrade cannot grow without bound."""

    def __init__(self):
        self.lines, self.dropped, self.done, self.ok, self.started = [], 0, False, False, time.time()

    def say(self, s):
        self.lines.append(s.rstrip('\r\n'))
        cap = perf.get()['job_lines']
        if len(self.lines) > cap:
            cut = len(self.lines) - cap + cap // 4          # drop a quarter at a time, not one line per call
            del self.lines[:cut]
            self.dropped += cut

    def read(self, since):
        """(new lines, next position). Positions count every line ever said, so a reader that fell behind just skips the dropped ones."""
        return self.lines[max(0, since - self.dropped):], self.dropped + len(self.lines)


def prune_jobs(keep):
    """Forget the oldest finished jobs beyond `keep` (running ones are never removed)."""
    finished = sorted((j.started, k) for k, j in JOBS.items() if j.done)
    for _, key in finished[:max(0, len(finished) - keep)]:
        JOBS.pop(key, None)


def start_job(fn, *args):
    jid = uuid.uuid4().hex
    prune_jobs(perf.get()['job_keep'])
    job = JOBS[jid] = Job()

    def target():
        try:
            fn(job, *args)
        except Exception as e:  # shown to the user in the log
            job.say('ERROR: %s' % e)
        job.done = True
        perf.invalidate('status')   # a deploy, install or upgrade changed the server

    threading.Thread(target=target, daemon=True).start()
    return jid


def stream_script(job, cfg, text, remote='bash -s'):
    p = popen(cfg, remote)
    p.stdin.write(text.encode())
    p.stdin.close()
    for line in iter(p.stdout.readline, b''):
        job.say(line.decode('utf-8', 'replace'))
    return p.wait()


def build_tgz(src, typ):
    fd, path = tempfile.mkstemp(suffix='.tgz')
    os.close(fd)
    # node_modules is dropped (the server reinstalls it) only when there is a package.json to reinstall from
    skip = {'.git'} | ({'node_modules'} if typ == 'node' and os.path.isfile(os.path.join(src, 'package.json')) else set())
    with tarfile.open(path, 'w:gz') as t:
        for e in sorted(os.listdir(src)):
            if e not in skip:
                t.add(os.path.join(src, e), arcname=e)
    return path


def _deploy_job(job, app, cfg, user):
    job.say('Packing %s ...' % app['source'])
    tgz = build_tgz(app['source'], app['type'])
    job.say('Uploading %.1f MB ...' % (os.path.getsize(tgz) / 1e6))
    p = popen(cfg, 'cat > /tmp/%s.tgz' % app['name'])
    with open(tgz, 'rb') as f:
        shutil.copyfileobj(f, p.stdin)
    p.stdin.close()
    err = p.stdout.read().decode('utf-8', 'replace')
    os.remove(tgz)
    if p.wait():
        raise RuntimeError('upload failed: ' + err.strip())
    rc = stream_script(job, cfg, deploy_script(app, user))
    job.ok = rc == 0
    if ON_SITE_ACTION:
        ON_SITE_ACTION(app['name'], 'deploy')
    if job.ok and app['mapCf']:
        job.say('Mapping in Cloudflare ...')
        try:
            for line in cloudflare.map_hosts([app['hostname']] + app['redirectFrom']):
                job.say('  ' + line)
        except Exception as e:  # the server part worked, so report instead of failing the whole deploy
            job.say('  Cloudflare mapping FAILED: %s' % e)
            job.say('  The site is deployed. Map it from the Domains page later.')
    if job.ok and app.get('mapDns'):
        job.say('Adding the DNS record ...')
        try:
            for line in dnsproviders.map_hosts([app['hostname']] + app['redirectFrom']):
                job.say('  ' + line)
        except Exception as e:
            job.say('  DNS record FAILED: %s' % e)
            job.say('  The site is deployed. Add an A record to this server by hand.')
    if rc == 4:   # deploy.sh: the first release failed its health check; there was nothing to roll back to
        msg = '%s failed its health check on its first deploy, so the service was stopped (no earlier release exists)' % app['name']
        activity.log('deploy-failed', app['name'], '', str(user), False)
        alerts.notify('[Shiplane] FIRST DEPLOY FAILED: ' + msg, site=app['name'])
        job.say('FAILED: ' + msg + '. Check the logs, fix the app and deploy again.')
    if rc == 3:   # deploy.sh: the new release failed its health check and the previous one was put back
        msg = '%s failed its health check, so the previous release is serving again' % app['name']
        activity.log('deploy-rollback', app['name'], '', str(user), False)
        alerts.notify('[Shiplane] ROLLED BACK: ' + msg, site=app['name'])
        job.say('ROLLED BACK: ' + msg)
    job.say('DONE: deployed https://%s' % app['hostname'] if job.ok else 'FAILED (exit code %s)' % rc)


def start_deploy(app, cfg, user):
    return start_job(_deploy_job, app, cfg, user)


def _script_job(job, cfg, text):
    rc = stream_script(job, cfg, text)
    job.ok = rc == 0
    job.say('DONE' if job.ok else 'FAILED (exit code %s)' % rc)


def start_script_job(cfg, text):
    return start_job(_script_job, cfg, text)


# ---------------------------------------------------------------------- actions
def _name(name):
    if not config.NAME_RE.match(str(name)):
        raise ValueError('bad site name')
    return name


def site_action(cfg, a):
    perf.invalidate('status')
    name, act = _name(a.get('name', '')), a.get('action')
    if ON_SITE_ACTION and act in ('start', 'stop', 'restart', 'activate', 'delete') and name not in PROTECTED:
        ON_SITE_ACTION(name, act)
    if name in PROTECTED and act in ('stop', 'delete'):
        raise ValueError('This site runs the app you are using, so it cannot be %s from here' % ('stopped' if act == 'stop' else 'deleted'))
    if act in ('restart', 'start', 'stop'):
        t = '-t 30 ' if act != 'start' else ''          # give the app time to shut down cleanly
        return run(cfg, 'if test -f /etc/systemd/system/%s.service; then sudo systemctl %s %s && echo ok; '
                        'elif sudo docker inspect shiplane-%s >/dev/null 2>&1; then sudo docker %s %sshiplane-%s >/dev/null && echo ok; '
                        'else echo "this site has no service (static sites do not need one)"; fi' % (name, act, name, name, act, t, name))
    if act == 'activate':
        rel = str(a.get('release', ''))
        if not config.REL_RE.match(rel):
            raise ValueError('bad release')
        return run(cfg, 'bash -s', inp=script('activate.sh').replace('__NAME__', name).replace('__REL__', rel).encode())
    if act == 'delete':
        if a.get('confirm') != name:
            raise ValueError('type the site name to confirm deletion')
        rc, out = run(cfg, 'bash -s', inp=script('delete.sh').replace('__NAME__', name).replace('__DROPDB__', 'true' if a.get('dropDb') else 'false').encode(), timeout=120)
        text = out.strip()
        if rc == 0 and a.get('unmapCf') and config.cloudflare_ready():
            try:
                text += '\n' + '\n'.join(cloudflare.unmap_hosts(config.clean_hosts(a.get('hosts'))))
            except Exception as e:
                text += '\nCloudflare cleanup FAILED: %s' % e
        return rc, text
    raise ValueError('unknown action')


def edit_env(cfg, a):
    perf.invalidate('status')
    """Set/unset environment variables of a site (values are write-only) and restart its service."""
    name = _name(a.get('name', ''))
    ops = []
    for k, v in (a.get('set') or {}).items():
        if not config.KEY_RE.match(k):
            raise ValueError('Bad variable name: ' + k)
        ops.append('setenv %s %s' % (q(k), q(v)))
    for k in a.get('unset') or []:
        if not config.KEY_RE.match(k):
            raise ValueError('Bad variable name: ' + k)
        ops.append('unsetenv %s' % q(k))
    if not ops:
        raise ValueError('Nothing to change')
    return run(cfg, 'bash -s', inp=script('env.sh').replace('__NAME__', name).replace('__OPS__', '\n'.join(ops)).encode(), timeout=60)


def env_values(cfg, name):
    """[{key, value}] from the site's environment file, in file order."""
    name = _name(name)
    rc, out = run(cfg, 'sudo cat /etc/%s.env 2>/dev/null' % name)
    if rc:
        return []
    return [{'key': k, 'value': v} for k, _, v in (line.partition('=') for line in out.splitlines()) if config.KEY_RE.match(k)]


def run_command(cfg, a):
    """One-off command (migrations, first admin user) as the site's own user with its environment, in the current release."""
    name, cmd = _name(a.get('name', '')), str(a.get('command') or '').strip()
    if not cmd or len(cmd) > 2000 or '\0' in cmd:
        raise ValueError('Type a command (up to 2000 characters)')
    return run(cfg, 'bash -s', inp=script('runcmd.sh').replace('__NAME__', name).replace('__CMD__', q(cmd)).encode(), timeout=330)


def rename_host(cfg, a):
    """Give a site a new address: Caddy, metadata, optionally its environment, then the DNS mapping (new name added before the old one goes).
    Returns the lines to show."""
    name = _name(a.get('name', ''))
    if name in PROTECTED:
        raise ValueError('The address of this app itself is set when it is installed, not here')
    new = str(a.get('hostname') or '').strip().lower()
    if not config.HOST_RE.match(new) or '.' not in new:
        raise ValueError('The new address looks wrong (example: shop.example.com)')
    site = next((x for x in status(cfg)['sites'] if x['name'] == name), None)
    if not site:
        raise ValueError('No site called %s' % name)
    old = site['hostname']
    if new == old:
        raise ValueError('That is already its address')
    if new in site.get('redirects', []):
        raise ValueError('%s already redirects to this site: remove the redirect first' % new)
    rc, out = run(cfg, 'bash -s', inp=script('rename_host.sh').replace('__NAME__', q(name)).replace('__OLD__', q(old)).replace('__NEW__', q(new))
                  .replace('__ENVTOO__', 'true' if a.get('env') else 'false').encode(), timeout=120)
    perf.invalidate('status')
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'could not change the address')
    lines = out.strip().splitlines()
    for flag, ready, module, label in (('mapCf', config.cloudflare_ready(), cloudflare, 'Cloudflare'), ('mapDns', bool(dnsproviders.connected()), dnsproviders, 'DNS')):
        if not (a.get(flag) and ready):
            continue
        try:
            lines += module.map_hosts([new])
            lines += module.unmap_hosts([old])
        except Exception as e:  # the site already answers on the new name; say what is left to do
            lines.append('%s step FAILED: %s. Fix it on the Domains page.' % (label, e))
    if ON_SITE_ACTION:
        ON_SITE_ACTION(name, 'deploy')
    return lines


def site_config(cfg, name):
    _name(name)
    rc, out = run(cfg, 'for f in /etc/caddy/sites/{0}.caddy /etc/systemd/system/{0}.service /etc/deploy-meta/{0}.json; do echo "### $f"; sudo cat "$f" 2>/dev/null || echo "(not present)"; echo; done'.format(name))
    sections, cur = [], None
    for line in out.splitlines():
        if line.startswith('### '):
            cur = {'path': line[4:], 'text': ''}
            sections.append(cur)
        elif cur is not None:
            cur['text'] += line + '\n'
    for s in sections:
        s['text'] = s['text'].strip()
    return sections


LOG_SINCE = {'15m': ('15 min ago', '15m'), '1h': ('1 hour ago', '1h'), '6h': ('6 hours ago', '6h'), '24h': ('24 hours ago', '24h'), '7d': ('7 days ago', '168h')}   # journalctl, docker
LOG_LEVEL = {'error': 'error|fatal|exception|panic|crit|fail', 'warning': 'warn|error|fatal|exception|panic|crit|fail'}


def logs(cfg, name, lines, query='', level='', since=''):
    """The site's log. Optional filters: text (case-insensitive, literal), level (error / warning, by keyword) and a time range."""
    _name(name)
    lines = max(10, min(1000, int(lines)))
    query = str(query or '').strip()
    if len(query) > 200 or any(ord(c) < 32 for c in query):
        raise ValueError('Search text must be one line of at most 200 characters')
    if level and level not in LOG_LEVEL or since and since not in LOG_SINCE:
        raise ValueError('Unknown log filter')
    fetch = 5000 if (query or level) else lines          # filtering needs a longer stretch to look through
    js, ds = (" --since '%s'" % LOG_SINCE[since][0]) if since else '', (' --since %s' % LOG_SINCE[since][1]) if since else ''
    cmd = ('{ if test -f /etc/systemd/system/%s.service; then sudo journalctl -u %s -n %d%s --no-pager; '
           'elif sudo docker inspect shiplane-%s >/dev/null 2>&1; then sudo docker logs --tail %d%s shiplane-%s 2>&1; '
           'else echo "static site: there is no service log"; fi; }' % (name, name, fetch, js, name, fetch, ds, name))
    if level:
        cmd += " | grep -iE %s" % q(LOG_LEVEL[level])
    if query:
        cmd += " | grep -iF -e %s" % q(query)
    out = run(cfg, cmd + ' | tail -n %d' % lines)[1]
    return out if out.strip() else '(no matching lines)'


def runtimes(cfg):
    """Node versions installed where a service can use them (/usr, /opt)."""
    rc, out = run(cfg, 'bash -s', inp=script('runtimes.sh').encode(), timeout=30)
    found = []
    for line in out.splitlines():
        path, _, ver = line.partition(' ')
        if re.match(r'^/(usr|opt)/[\w.@/-]+/node$', path) and re.match(r'^v[\d.]+$', ver.strip()):
            found.append({'path': path, 'version': ver.strip()})
    return found


def status(cfg):
    """The server and its sites. Read-only: the same result is shared for a few seconds (the profile's cache time) and dropped after any change."""
    return perf.cached(('status', cfg.get('host'), cfg.get('user')), lambda: _status_now(cfg))


def _status_now(cfg):
    rc, out = run(cfg, 'sudo python3 -', inp=script('status.py').encode(), timeout=60)
    if rc or '{' not in out:
        raise RuntimeError(out.strip()[-500:] or 'could not read the server')
    data = json.loads(out[out.index('{'):out.rindex('}') + 1])

    def check(s):
        if not s['hostname'] or '*' in s['hostname']:
            return
        recent = PUBLIC_LOOKUP(s['hostname']) if PUBLIC_LOOKUP else None
        if recent:                                    # the uptime check ran moments ago: reuse it instead of another request
            s['public'], s['public_ms'] = recent
            return
        t0 = time.time()
        try:
            req = urllib.request.Request('https://%s/' % s['hostname'], headers={'User-Agent': 'coderunner'})
            with urllib.request.urlopen(req, timeout=8) as r:
                s['public'] = r.status
        except urllib.error.HTTPError as e:
            s['public'] = e.code
        except Exception:
            s['public'] = 'err'
        s['public_ms'] = int((time.time() - t0) * 1000)

    with concurrent.futures.ThreadPoolExecutor(max(1, min(perf.get()['workers'], len(data['sites']) or 1))) as ex:
        list(ex.map(check, data['sites']))
    return data


def pick_file():
    """Native "open file" dialog on this computer, for backup files. Returns the chosen path, or '' when cancelled or unavailable."""
    code = ("import tkinter as tk; from tkinter import filedialog as fd; r=tk.Tk(); r.withdraw(); r.attributes('-topmost', True); "
            "print(fd.askopenfilename(title='Choose a cPanel or Plesk backup', filetypes=[('Backups', '*.tar.gz *.tgz *.tar *.zip'), ('All files', '*.*')]))")
    try:
        p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=300, creationflags=NOWIN)
    except Exception:
        return ''
    return p.stdout.strip()


def pick_folder():
    code = ("import tkinter as tk; from tkinter import filedialog as fd; r=tk.Tk(); r.withdraw(); "
            "r.attributes('-topmost', True); print(fd.askdirectory(title='Choose the folder to deploy'))")
    try:
        p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=300, creationflags=NOWIN)
    except Exception:
        return ''
    return p.stdout.strip()
