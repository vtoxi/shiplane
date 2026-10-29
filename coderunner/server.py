"""Local HTTP server: serves the web app (web/) and the JSON API (/api/...).

Two ways to get in:
  * auth "token" (default, on your own computer): the launch link carries a one-time token that becomes a cookie.
  * auth "login" (on a server, CODERUNNER_AUTH=login): email + password accounts with roles (see auth.py).
API keys (Authorization: Bearer crk_...) work in both modes. Every request must carry an allowed Host header;
browser writes also need a custom header. Remote commands only ever run allowlisted scripts with validated arguments.
"""
import hmac
import http.cookies
import http.server
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import sys
import tempfile
import threading
import traceback
import time
from urllib.parse import parse_qs, urlparse

from . import __version__, activity, alerts, apikeys, apps, care, caredetail, gitdeploy, gitproviders, heal, mailtpl, metricslog, perf, watch, auth, backups, billing, cloudflare, config, dbengines, dnsproviders, wildcard, dbops, deploy, dns, maintenance, migrate, migrator, monitor, serverops, settings, uploads
from .runner import explain_ssh as runner_explain, popen, q, run, who

# CODERUNNER_TOKEN pins the launch token (token mode); otherwise a fresh one per launch.
STATE = {
    'port': 8765,
    'token': os.environ.get('CODERUNNER_TOKEN') or secrets.token_urlsafe(24),
    'allowed_hosts': [h.strip().lower() for h in os.environ.get('CODERUNNER_ALLOW_HOSTS', '').split(',') if h.strip()],
    'auth': 'login' if os.environ.get('CODERUNNER_AUTH') == 'login' else 'token',
    'secure_cookies': os.environ.get('CODERUNNER_SECURE_COOKIES') == '1',   # set when served over HTTPS (behind Cloudflare)
    'behind_proxy': os.environ.get('CODERUNNER_BEHIND_PROXY') == '1',       # trust CF-Connecting-IP for the client address
}
ROUTES = {}   # (method, path) -> {fn, role, public}
ctx = threading.local()  # per request: user, via_key, ip, cookies to set
TYPES = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml',
         '.json': 'application/json', '.md': 'text/markdown'}
CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
SESSION_COOKIE = 'cr_session'
MAX_JSON = 2 * 1024 * 1024        # API bodies are small; a bigger one is refused before it is read into memory


def route(method, path, role=None, public=False):
    """Register an API endpoint. Default role: viewer for GET, admin for POST. public=True needs no sign-in."""
    def deco(fn):
        ROUTES[(method, path)] = {'fn': fn, 'role': role or ('viewer' if method == 'GET' else 'admin'), 'public': public}
        return fn
    return deco


def cfg():
    return config.load_server()


def require_2fa():
    """Policy: everyone with an account must use two-factor sign-in (setting, or CODERUNNER_REQUIRE_2FA=1)."""
    return STATE['auth'] == 'login' and (os.environ.get('CODERUNNER_REQUIRE_2FA') == '1' or bool(settings.get('policy').get('require_2fa')))


MSG_2FA = 'Your administrator requires two-factor sign-in. Set it up in Settings first.'


def me():
    return getattr(ctx, 'user', None) or {'id': 0, 'email': '', 'role': 'viewer'}


def audit(kind, target='', detail='', ok=True):
    activity.log(kind, target, detail, me()['email'] or who(cfg()), ok)


def need_cf():
    if not config.cloudflare_ready():
        raise PermissionError('Cloudflare is not set up on this computer (no API token)')


def browser_only():
    if getattr(ctx, 'via_key', False):
        raise PermissionError('This is managed from the browser app, not with an API key')


def set_cookie(name, value, max_age):
    c = '%s=%s; HttpOnly; SameSite=Lax; Path=/; Max-Age=%d' % (name, value, max_age)
    if STATE['secure_cookies']:
        c += '; Secure'
    ctx.cookies.append(c)


# ------------------------------------------------------------------------ auth
@route('GET', '/api/auth/me', public=True)
def api_me(qs, body):
    u = getattr(ctx, 'user', None)
    return {'auth': STATE['auth'], 'authenticated': bool(u), 'user': u, 'version': __version__}


@route('POST', '/api/auth/login', public=True)
def api_login(qs, body):
    if STATE['auth'] != 'login':
        raise ValueError('Sign-in is not enabled on this instance')
    email = str(body.get('email', ''))[:120]
    try:
        token, user = auth.login(email, body.get('password', ''), ctx.ip)
    except ValueError as e:
        activity.log('login-failed', email, ctx.ip, '', False)
        raise e
    if not token:
        return {'need_2fa': True, 'challenge': user['challenge']}
    set_cookie(SESSION_COOKIE, token, auth.SESSION_MAX)
    activity.log('login', user['email'], ctx.ip, user['email'])
    return {'user': user}


@route('POST', '/api/auth/login-2fa', public=True)
def api_login_2fa(qs, body):
    if STATE['auth'] != 'login':
        raise ValueError('Sign-in is not enabled on this instance')
    try:
        token, user = auth.login_2fa(body.get('challenge', ''), body.get('code', ''), ctx.ip)
    except ValueError as e:
        activity.log('login-failed', '2fa code', ctx.ip, '', False)
        raise e
    set_cookie(SESSION_COOKIE, token, auth.SESSION_MAX)
    activity.log('login', user['email'], ctx.ip + ' (2fa)', user['email'])
    return {'user': user}


@route('POST', '/api/auth/activate', public=True)
def api_activate(qs, body):
    """First-time password setup with a one-time activation code."""
    if STATE['auth'] != 'login':
        raise ValueError('Sign-in is not enabled on this instance')
    user = auth.activate(body.get('email', ''), body.get('code', ''), body.get('password', ''), ctx.ip)
    email = user['email']
    activity.log('account-activated', email, ctx.ip, email)
    token, user = auth.login(email, body.get('password', ''), ctx.ip)
    if not token:
        return {'need_2fa': True, 'challenge': user['challenge']}
    set_cookie(SESSION_COOKIE, token, auth.SESSION_MAX)
    return {'user': user}


@route('POST', '/api/auth/logout', role='viewer')
def api_logout(qs, body):
    auth.logout(getattr(ctx, 'session_token', ''))
    set_cookie(SESSION_COOKIE, '', 0)
    return {'ok': True}


@route('POST', '/api/auth/change-password', role='viewer')
def api_change_password(qs, body):
    if STATE['auth'] != 'login' or not me()['id']:
        raise ValueError('Passwords are only used when sign-in is enabled')
    auth.change_password(me()['id'], body.get('old', ''), body.get('new', ''))
    audit('password-changed', me()['email'])
    return {'ok': True}


def need_account():
    if STATE['auth'] != 'login' or not me()['id']:
        raise ValueError('Two-factor sign-in belongs to an account, so it needs sign-in mode')
    return me()['id']


@route('POST', '/api/auth/2fa/start', role='viewer')
def api_2fa_start(qs, body):
    secret, uri = auth.totp_start(need_account())
    return {'secret': secret, 'uri': uri}


@route('POST', '/api/auth/2fa/enable', role='viewer')
def api_2fa_enable(qs, body):
    codes = auth.totp_enable(need_account(), body.get('code', ''), ctx.ip)
    audit('2fa-enabled', me()['email'])
    return {'codes': codes}


@route('POST', '/api/auth/2fa/disable', role='viewer')
def api_2fa_disable(qs, body):
    auth.totp_disable(need_account(), body.get('password', ''), body.get('code', ''), ctx.ip)
    audit('2fa-disabled', me()['email'])
    return {'ok': True}


# ----------------------------------------------------------------------- users
@route('GET', '/api/users', role='super_admin')
def api_users(qs, body):
    return {'users': auth.list_users(), 'me': me()['id']}


@route('POST', '/api/users/create', role='super_admin')
def api_users_create(qs, body):
    user, code = auth.create_user(body.get('email', ''), body.get('role', 'viewer'))
    audit('user-create', user['email'], user['role'])
    out = {'user': user, 'code': code, 'emailed': False, 'email_error': ''}
    if body.get('email_code'):                                  # the person asked for the code to be mailed (needs Settings, Alerts, E-mail)
        try:
            page, plain = mailtpl.activation(code)
            alerts.send_mail(user['email'], 'Your Shiplane account', plain, page)
            out['emailed'] = True
        except Exception as e:
            out['email_error'] = str(e)
    return out


@route('POST', '/api/users/update', role='super_admin')
def api_users_update(qs, body):
    auth.update_user(int(body.get('id', 0)), me()['id'], body.get('role'), body.get('status'))
    audit('user-update', str(body.get('id')), 'role=%s status=%s' % (body.get('role'), body.get('status')))
    return {'ok': True}


@route('POST', '/api/users/reset-code', role='super_admin')
def api_users_reset(qs, body):
    code = auth.reset_code(int(body.get('id', 0)))
    audit('user-reset', str(body.get('id')))
    return {'code': code}


@route('POST', '/api/users/reset-2fa', role='super_admin')
def api_users_reset_2fa(qs, body):
    auth.totp_reset(int(body.get('id', 0)))
    audit('2fa-reset', str(body.get('id')))
    return {'ok': True}


@route('POST', '/api/users/delete', role='super_admin')
def api_users_delete(qs, body):
    auth.delete_user(int(body.get('id', 0)), me()['id'])
    audit('user-delete', str(body.get('id')))
    return {'ok': True}


# --------------------------------------------------------------------- session
@route('GET', '/api/session')
def api_session(qs, body):
    c = cfg()
    return {'version': __version__, 'server': config.public_server(c), 'cloudflare': config.cloudflare_ready(), 'dnsProviders': dnsproviders.connected(), 'platform': sys.platform,
            'user': me()['email'] or who(c), 'me': me(), 'perf': {'profile': perf.name(), 'ui_poll': perf.get()['ui_poll'], 'live_poll': perf.get()['live_poll']}, 'auth': STATE['auth'], 'require2fa': require_2fa(), 'require2fa_env': os.environ.get('CODERUNNER_REQUIRE_2FA') == '1',
            'keyFound': c['mode'] == 'local' or c.get('auth') == 'password' or os.path.isfile(os.path.expanduser(c['key']))}


@route('POST', '/api/config', role='super_admin')
def api_config(qs, body):
    config.save_server(body)
    perf.invalidate()
    audit('settings', 'connection', 'connection settings changed')
    return {'ok': True}


@route('POST', '/api/test-connection', role='super_admin')
def api_test(qs, body):
    c = config.resolve_server(body)
    rc, out = run(c, 'echo "connected as $(whoami) on $(hostname)"; sudo -n true && echo "sudo: ok" || echo "sudo: needs a password (the app needs passwordless sudo)"', timeout=30)
    return {'ok': rc == 0, 'text': runner_explain(out, c) if rc else out.strip()}


# ----------------------------------------------------------------------- sites
@route('GET', '/api/status')
def api_status(qs, body):
    return deploy.status(cfg())


@route('GET', '/api/job')
def api_job(qs, body):
    job = deploy.JOBS.get(qs.get('id', [''])[0])
    if not job:
        raise LookupError('unknown job')
    lines, nxt = job.read(int(qs.get('since', ['0'])[0]))
    return {'lines': lines, 'next': nxt, 'done': job.done, 'ok': job.ok}


@route('GET', '/api/metrics')
def api_metrics(qs, body):
    return monitor.metrics(cfg())


@route('GET', '/api/alerts', role='super_admin')
def api_alerts(qs, body):
    return alerts.public()


@route('POST', '/api/alerts', role='super_admin')
def api_alerts_save(qs, body):
    alerts.save(body.get('telegram_token'), body.get('telegram_chat'), body.get('webhook_url'), body.get('email_user'), body.get('email_password'), body.get('email_to'),
                body.get('slack_url'), body.get('discord_url'), body.get('webhook_secret'))
    audit('alerts-config')
    return alerts.public()


@route('POST', '/api/alerts/test', role='super_admin')
def api_alerts_test(qs, body):
    return {'results': alerts.test()}


@route('GET', '/api/backups/schedule', role='admin')
def api_backup_schedule(qs, body):
    return dict(backups.get(), offsite=backups.offsite_public())


@route('POST', '/api/backups/offsite', role='super_admin')
def api_backup_offsite(qs, body):
    out = backups.save_offsite(body)
    audit('backup-offsite', '', ('bucket ' + out['bucket']) if out['configured'] else 'removed')
    return out


@route('POST', '/api/backups/offsite/test', role='super_admin')
def api_backup_offsite_test(qs, body):
    ok, text = backups.test_offsite(cfg())
    audit('backup-offsite-test', '', text[:200], ok)
    return {'ok': ok, 'text': text}


@route('POST', '/api/backups/schedule', role='admin')
def api_backup_schedule_save(qs, body):
    c = backups.save(body.get('enabled'), body.get('hour', 3), body.get('keep', 14), body.get('verify'))
    audit('backup-schedule', '', 'enabled=%s hour=%s keep=%s' % (c['enabled'], c['hour'], c['keep']))
    return c


@route('POST', '/api/backups/run', role='admin')
def api_backup_run(qs, body):
    audit('backup-run-start')
    return {'job': backups.start_run(cfg(), me()['email'] or who(cfg()))}


@route('POST', '/api/databases/restore', role='admin')
def api_db_restore(qs, body):
    name, file = str(body.get('name', '')), str(body.get('file', ''))
    job = backups.start_restore(cfg(), me()['email'] or who(cfg()), name, file, body.get('confirm'))
    audit('db-restore-start', name, file)
    return {'job': job}


@route('POST', '/api/databases/verify', role='admin')
def api_db_verify(qs, body):
    file = str(body.get('file', ''))
    job = backups.start_verify(cfg(), me()['email'] or who(cfg()), file)
    audit('backup-verify-start', file)
    return {'job': job}


@route('POST', '/api/policy', role='super_admin')
def api_policy(qs, body):
    on = bool(body.get('require_2fa'))
    if on and not me().get('totp'):
        raise ValueError('Turn on your own two-factor sign-in first, then require it for everyone')
    p = settings.get('policy')
    p['require_2fa'] = on
    settings.put('policy', p)
    audit('policy', '', 'require_2fa=%s' % on)
    return {'require2fa': require_2fa()}


@route('GET', '/api/billing', role='admin')
def api_billing(qs, body):
    return billing.overview()


@route('GET', '/api/monitor')
def api_monitor(qs, body):
    """How often the sites' public addresses are checked (seconds, or off), and the choices."""
    return dict(monitor.schedule(), options=list(monitor.OPTIONS))


@route('POST', '/api/monitor', role='super_admin')
def api_monitor_save(qs, body):
    out = monitor.set_schedule(body.get('interval'), body.get('enabled', True))
    audit('settings', 'uptime checks', 'every %d s, %s' % (out['interval'], 'on' if out['enabled'] else 'off'))
    return out


@route('GET', '/api/health')
def api_health(qs, body):
    return monitor.health()


@route('POST', '/api/site/check')
def api_site_check(qs, body):
    """Check one site's public address now and record it like a scheduled uptime check."""
    name = deploy._name(body.get('name', ''))
    site = next((x for x in deploy.status(cfg())['sites'] if x['name'] == name), None)
    if not site:
        raise ValueError('No site called %s' % name)
    out = monitor.check_now(name, site['hostname'])
    perf.invalidate('status')
    return out


@route('GET', '/api/site/env/values', role='admin')
def api_site_env_values(qs, body):
    """A site's environment variables with their values (admins only; every read is in Activity)."""
    name = deploy._name((qs.get('name') or [''])[0])
    out = deploy.env_values(cfg(), name)
    audit('env-read', name, '%d variables' % len(out))
    return {'vars': out}


@route('GET', '/api/logs', role='admin')
def api_logs(qs, body):
    return {'text': deploy.logs(cfg(), qs.get('name', [''])[0], qs.get('lines', ['200'])[0], qs.get('q', [''])[0], qs.get('level', [''])[0], qs.get('since', [''])[0])}


@route('GET', '/api/runtimes', role='admin')
def api_runtimes(qs, body):
    return {'node': deploy.runtimes(cfg())}


@route('GET', '/api/cloudflare/zones', role='admin')
def api_cf_zones(qs, body):
    own = list(settings.get('dns').get('zones') or {})          # zones on this server's own nameservers
    return {'zones': sorted(set((cloudflare.zone_names() if config.cloudflare_ready() else []) + dnsproviders.zone_names() + own)), 'own': own}


@route('GET', '/api/site/config', role='admin')
def api_site_config(qs, body):
    return {'sections': deploy.site_config(cfg(), qs.get('name', [''])[0])}


@route('POST', '/api/site/env')
def api_site_env(qs, body):
    rc, out = deploy.edit_env(cfg(), body)
    changed = ', '.join(list((body.get('set') or {}).keys()) + ['-' + k for k in body.get('unset') or []])
    audit('env', body.get('name', ''), 'variables: ' + changed, rc == 0)
    return {'ok': rc == 0, 'text': out.strip()}


@route('POST', '/api/site/rename', role='admin')
def api_site_rename(qs, body):
    """Change a site's address everywhere: Caddy, metadata, optionally its environment and its Cloudflare / DNS records."""
    lines = deploy.rename_host(cfg(), body)
    audit('rename', body.get('name', ''), 'new address %s' % str(body.get('hostname', ''))[:120])
    return {'lines': lines}


@route('POST', '/api/site/run', role='admin')
def api_site_run(qs, body):
    rc, out = deploy.run_command(cfg(), body)
    audit('run-command', body.get('name', ''), str(body.get('command', ''))[:200], rc == 0)
    return {'ok': rc == 0, 'text': out.strip()}


def check_source(app):
    """A deploy publishes a folder of THIS computer on the internet, so it must not reach this app's own secrets.
    Nobody may deploy a folder that is, holds or sits inside the data folder, the connection/provider files or SSH keys;
    only a super admin may deploy any other folder by path (admins and API keys upload a zip)."""
    real = os.path.realpath
    src = real(app['source'])

    def inside(path, folder):
        return path == folder or path.startswith(folder + os.sep)
    secrets_ = [real(p) for p in (config.DATA, config.SERVER_FILE, config.CF_FILE, os.path.join(config.CONF, 'dnsproviders.json'), os.path.expanduser('~/.ssh'),
                                  os.path.expanduser('~/.aws'), os.path.expanduser('~/.gnupg'))]
    if any(inside(p, src) or inside(src, p) for p in secrets_):
        raise PermissionError("That folder holds this app's settings or keys and cannot be deployed")
    tmp = real(tempfile.gettempdir())
    rel = os.path.relpath(src, tmp)
    uploaded = not rel.startswith('..') and rel.split(os.sep)[0].startswith(('coderunner-upload-', 'shiplane-empty-'))
    if me()['role'] != 'super_admin' and not uploaded:
        raise PermissionError('Only a super admin can deploy a folder by path. Upload it as a .zip instead')


@route('POST', '/api/deploy')
def api_deploy(qs, body):
    app = deploy.validate_app(deploy.with_starter(body) if body.get('empty') else body)
    check_source(app)
    audit('deploy', app['name'], '%s (%s)%s' % (app['hostname'], app['type'], ', cloudflare' if app['mapCf'] else ''))
    return {'job': deploy.start_deploy(app, cfg(), me()['email'] or who(cfg()))}


@route('POST', '/api/action')
def api_action(qs, body):
    rc, out = deploy.site_action(cfg(), body)
    audit(body.get('action', ''), body.get('name', ''), (body.get('release') or '') + (' +db' if body.get('dropDb') else ''), rc == 0)
    return {'ok': rc == 0, 'text': out.strip()}


@route('POST', '/api/pick-folder')
def api_pick(qs, body):
    if cfg()['mode'] == 'local':
        raise ValueError('The folder picker is not available when the app runs on the server. Paste a path or upload a zip.')
    return {'path': deploy.pick_folder()}


@route('POST', '/api/pick-file', role='admin')
def api_pick_file(qs, body):
    """Open a file dialog on this computer (for choosing a backup to migrate)."""
    if cfg()['mode'] == 'local':
        raise ValueError('The file picker is not available when the app runs on the server. Paste the path instead.')
    browser_only()
    return {'path': deploy.pick_file()}


# ------------------------------------------------------------------- databases
@route('GET', '/api/databases', role='admin')
def api_databases(qs, body):
    return dbops.list_all(cfg())


@route('POST', '/api/databases/create')
def api_db_create(qs, body):
    perf.invalidate('status')
    out = dbops.create(cfg(), body.get('name', ''), body.get('user'), body.get('password'))
    audit('db-create', out['name'])
    return out


@route('GET', '/api/dbengines', role='admin')
def api_dbengines(qs, body):
    """MySQL / MariaDB state and databases, and the SQL Server containers on the server."""
    return {'mysql': dbengines.mysql_state(cfg()), 'mssql': dbengines.mssql_containers(cfg())}


@route('POST', '/api/dbengines/mysql/create', role='admin')
def api_mysql_create(qs, body):
    out = dbengines.mysql_create(cfg(), body.get('name', ''), body.get('user'), body.get('password'))
    audit('db-create', out['name'], 'mysql')
    return out


@route('POST', '/api/dbengines/mysql/drop', role='admin')
def api_mysql_drop(qs, body):
    text = dbengines.mysql_drop(cfg(), body.get('name', ''), body.get('confirm'))
    audit('db-drop', body.get('name', ''), 'mysql')
    return {'text': text}


@route('POST', '/api/dbengines/mssql/create', role='admin')
def api_mssql_create(qs, body):
    out = dbengines.mssql_create(cfg(), body.get('container', ''), body.get('sa_password'), body.get('name', ''), body.get('user'), body.get('password'))
    audit('db-create', out['name'], 'sql server')
    return out


@route('POST', '/api/dbengines/mssql/drop', role='admin')
def api_mssql_drop(qs, body):
    text = dbengines.mssql_drop(cfg(), body.get('container', ''), body.get('sa_password'), body.get('name', ''), body.get('confirm'))
    audit('db-drop', body.get('name', ''), 'sql server')
    return {'text': text}


@route('POST', '/api/databases/backup')
def api_db_backup(qs, body):
    out = dbops.backup(cfg(), body.get('name', ''))
    audit('db-backup', body.get('name', ''), out['file'])
    return out


@route('POST', '/api/databases/drop')
def api_db_drop(qs, body):
    perf.invalidate('status')
    text = dbops.drop(cfg(), body.get('name', ''), body.get('confirm'))
    audit('db-drop', body.get('name', ''))
    return {'text': text}


@route('POST', '/api/databases/backup-delete')
def api_db_backup_delete(qs, body):
    text = dbops.delete_backup(cfg(), body.get('file', ''))
    audit('db-backup-delete', body.get('file', ''))
    return {'text': text}


# ---------------------------------------------------------------------- server
@route('GET', '/api/server/info', role='admin')
def api_server_info(qs, body):
    return serverops.info(cfg())


@route('POST', '/api/server/service')
def api_server_service(qs, body):
    rc, out = serverops.service(cfg(), body.get('name', ''), body.get('action', ''))
    if body.get('action') in ('start', 'stop', 'restart', 'enable', 'disable'):
        audit('service-' + body['action'], body.get('name', ''), '', rc == 0)
    perf.invalidate('status')
    return {'ok': rc == 0, 'text': out.strip()}


@route('POST', '/api/server/container')
def api_server_container(qs, body):
    rc, out = serverops.container(cfg(), body.get('name', ''), body.get('action', ''))
    if body.get('action') != 'logs':
        audit('container-' + body.get('action', ''), body.get('name', ''), '', rc == 0)
    return {'ok': rc == 0, 'text': out.strip()}


@route('POST', '/api/server/reboot', role='super_admin')
def api_server_reboot(qs, body):
    if body.get('confirm') != 'reboot':
        raise ValueError('Type "reboot" to confirm')
    rc, out = serverops.reboot(cfg())
    audit('reboot', 'server', '', rc == 0)
    return {'ok': rc == 0, 'text': out.strip()}


@route('POST', '/api/server/upgrade')
def api_server_upgrade(qs, body):
    audit('apt-upgrade', 'server')
    return {'job': deploy.start_script_job(cfg(), serverops.UPGRADE_SH)}


@route('GET', '/api/domains/health', role='admin')
def api_domains_health(qs, body):
    """DNS, SSL certificate expiry and HTTP status of every site's public address."""
    return {'domains': maintenance.domain_health(cfg())}


@route('GET', '/api/cron', role='admin')
def api_cron(qs, body):
    """Scheduled tasks of one site."""
    return {'tasks': maintenance.cron_list(cfg(), qs.get('name', [''])[0])}


@route('POST', '/api/cron/add')
def api_cron_add(qs, body):
    rc, out = maintenance.cron_add(cfg(), body)
    audit('cron-add', body.get('name', ''), '%s %s' % (body.get('schedule', ''), body.get('command', '')), rc == 0)
    return {'ok': rc == 0, 'text': out}


@route('POST', '/api/cron/delete')
def api_cron_delete(qs, body):
    rc, out = maintenance.cron_delete(cfg(), body)
    audit('cron-delete', body.get('name', ''), str(body.get('id', '')), rc == 0)
    return {'ok': rc == 0, 'text': out.strip()}


@route('GET', '/api/disk', role='admin')
def api_disk(qs, body):
    """Disk usage per site and release, plus system caches."""
    return maintenance.disk_report(cfg())


@route('POST', '/api/disk/clean')
def api_disk_clean(qs, body):
    rc, label, freed, out = maintenance.disk_clean(cfg(), body)
    audit('cleanup', label, 'freed %d bytes' % freed, rc == 0)
    return {'ok': rc == 0, 'freed': freed, 'text': out}


@route('POST', '/api/migrate/inspect', role='admin')
def api_migrate_inspect(qs, body):
    """Read an old hosting account (cPanel backup file or a live server) and list what can be moved. Changes nothing."""
    inv = migrate.inspect(body)
    audit('migrate-inspect', body.get('path') or body.get('host', ''), '%d sites, %d databases' % (len(inv['sites']), len(inv['databases'])))
    inv['capabilities'] = migrate.capabilities(cfg(), inv)
    inv['suggestions'] = migrator.suggestions(inv)
    return inv


@route('POST', '/api/migrate/prepare', role='admin')
def api_migrate_prepare(qs, body):
    """Install one missing component (PHP, MySQL, PostgreSQL) on the new server. Runs as a background job."""
    audit('migrate-install', body.get('id', ''))
    return {'job': migrate.install_capability(cfg(), body.get('id', ''))}


@route('POST', '/api/migrate/start', role='admin')
def api_migrate_start(qs, body):
    """Start moving the chosen sites and databases from the old hosting. Progress: GET /api/migrate/run."""
    run_id = migrator.start(cfg(), body, me()['email'] or who(cfg()))
    audit('migrate-start', body.get('path') or body.get('host', ''), '%d sites, %d databases' % (len(body.get('sites') or []), len(body.get('databases') or [])))
    return {'id': run_id}


@route('GET', '/api/migrate/run', role='admin')
def api_migrate_run(qs, body):
    """Steps, status and log of one migration."""
    return migrator.get(qs.get('id', [''])[0]).view(int(qs.get('since', ['0'])[0]))


@route('GET', '/api/migrate/active', role='admin')
def api_migrate_active(qs, body):
    """The running (or latest) migration, for the progress indicator."""
    r = migrator.active()
    return {'run': r.brief() if r else None}


@route('GET', '/api/apps', role='admin')
def api_apps(qs, body):
    """The app catalog and what is installed on the server."""
    return {'catalog': apps.catalog(), 'status': apps.status(cfg())}


@route('POST', '/api/apps/install', role='admin')
def api_apps_install(qs, body):
    """Install a runtime, service or web app. Progress: GET /api/job."""
    job, summary = apps.install(cfg(), body)
    audit('app-install', body.get('id', ''), summary)
    return {'job': job}


@route('GET', '/api/fs/list', role='admin')
def api_fs_list(qs, body):
    """Sub-folders of a folder on the server (inside /var/www, /srv and /home), for choosing where an app goes."""
    return apps.list_dir(cfg(), qs.get('path', ['/'])[0])


@route('POST', '/api/fs/mkdir', role='admin')
def api_fs_mkdir(qs, body):
    path = apps.make_dir(cfg(), body.get('path', ''))
    audit('mkdir', path)
    return {'path': path}


@route('GET', '/api/watch', role='admin')
def api_watch(qs, body):
    """The kinds of problem the server alerts about, and which are switched on."""
    on = watch.enabled()
    return {'kinds': [{'id': k, 'label': v, 'on': on[k]} for k, v in watch.KINDS.items()]}


@route('POST', '/api/watch', role='super_admin')
def api_watch_save(qs, body):
    watch.set_enabled(body.get('values') or {})
    audit('settings', 'smart alerts', ', '.join('%s=%s' % (k, 'on' if v else 'off') for k, v in sorted((body.get('values') or {}).items())))
    return api_watch(qs, body)


@route('GET', '/api/heal', role='admin')
def api_heal(qs, body):
    """What auto-heal may do, and which options are on."""
    on = heal.enabled()
    return {'kinds': [{'id': k, 'label': v, 'on': on[k]} for k, v in heal.KINDS.items()]}


@route('POST', '/api/heal', role='super_admin')
def api_heal_save(qs, body):
    heal.set_enabled(body.get('values') or {})
    audit('settings', 'auto-heal', ', '.join('%s=%s' % (k, 'on' if v else 'off') for k, v in sorted((body.get('values') or {}).items())))
    return api_heal(qs, body)


def _git_guard(*bodies):
    """Admins may connect repositories and deploy them; the commands a build or start runs on the server stay with super admins."""
    if auth.RANK[me()['role']] >= auth.RANK['super_admin']:
        return
    for b in bodies:
        if isinstance(b, dict) and (b.get('build') == 'commands' or b.get('type') == 'command'):
            raise PermissionError('Only a super admin can set build or start commands. Use npm or no build, or ask a super admin.')


@route('GET', '/api/git', role='admin')
def api_git(qs, body):
    """The Git setup of one site (never the webhook secret)."""
    return gitdeploy.public(qs.get('name', [''])[0])


@route('POST', '/api/git/save', role='admin')
def api_git_save(qs, body):
    """Connect a site to a repository. The webhook secret is returned only when it is created."""
    _git_guard(body)
    view, secret = gitdeploy.save(cfg(), body)
    audit('git-connect', body.get('name', ''), '%s (%s)' % (view['repo'], view['branch']))
    return dict(view, secret=secret)


def _public_base():
    """https://host of this portal when it has a public name (so a webhook can reach it), else ''."""
    host = ctx.headers.get('Host') or ''
    if ':' in host or host.startswith('[') or host == 'localhost' or host.endswith(('.localhost', '.local')) or not re.search(r'[A-Za-z]', host):
        return ''                                              # a port, an IP address or a local name: GitHub/GitLab cannot reach it
    return 'https://' + host.lower()


@route('POST', '/api/git/create', role='admin')
def api_git_create(qs, body):
    """Create a new site from a repository. With a `session` (GitHub/GitLab sign-in) and `repo` it also adds the deploy key and webhook. Progress: GET /api/job."""
    _git_guard(body.get('git'), body.get('app'))
    sid, repo = body.get('session'), body.get('repo')
    before = None
    if sid:
        body = dict(body, git=dict(body.get('git') or {}, repo=gitproviders.clone_address(sid, repo)))
        before = lambda name: gitdeploy.provider_link(cfg(), name, sid, repo, _public_base())
    out = gitdeploy.create(cfg(), body, me()['email'] or who(cfg()), before)
    audit('git-create', (body.get('app') or {}).get('name', ''), (body.get('git') or {}).get('repo', ''))
    return out


@route('GET', '/api/gitprov', role='admin')
def api_gitprov(qs, body):
    out = gitproviders.providers()
    if auth.RANK[me()['role']] < auth.RANK['super_admin']:
        out = [{k: v for k, v in p.items() if k != 'client_id'} for p in out]       # the id is public, but only a super admin manages it
    return {'providers': out}


@route('POST', '/api/gitprov/oauth', role='super_admin')
def api_gitprov_oauth(qs, body):
    """Save the OAuth client ids (public identifiers, not secrets) used by the device login."""
    gitproviders.save_client_ids(body.get('values') or {})
    audit('settings', 'git-login')
    return api_gitprov(qs, body)


@route('POST', '/api/gitprov/device', role='admin')
def api_gitprov_device(qs, body):
    return gitproviders.device_start(body.get('provider'))


@route('POST', '/api/gitprov/poll', role='admin')
def api_gitprov_poll(qs, body):
    return gitproviders.device_poll(body.get('session'))


@route('POST', '/api/gitprov/token', role='admin')
def api_gitprov_token(qs, body):
    out = gitproviders.use_token(body.get('provider'), body.get('token'))
    audit('git-login', body.get('provider', ''), out['user'])
    return out


@route('GET', '/api/gitprov/repos', role='admin')
def api_gitprov_repos(qs, body):
    return {'repos': gitproviders.repos(qs.get('session', [''])[0])}


@route('GET', '/api/gitprov/branches', role='admin')
def api_gitprov_branches(qs, body):
    return {'branches': gitproviders.branches(qs.get('session', [''])[0], qs.get('repo', [''])[0])}


@route('GET', '/api/gitprov/detect', role='admin')
def api_gitprov_detect(qs, body):
    return gitproviders.detect(qs.get('session', [''])[0], qs.get('repo', [''])[0], qs.get('branch', ['main'])[0])


@route('POST', '/api/gitprov/forget', role='admin')
def api_gitprov_forget(qs, body):
    gitproviders.forget(body.get('session'))
    return {'ok': True}


@route('POST', '/api/gitprov/connect', role='admin')
def api_gitprov_connect(qs, body):
    """Connect an existing site to a repository picked after signing in: saves the setup, adds the deploy key and webhook."""
    _git_guard(body)
    sid, repo, name = body.get('session'), body.get('repo'), body.get('name', '')
    view, secret = gitdeploy.save(cfg(), dict(body, repo=gitproviders.clone_address(sid, repo)))
    linked = gitdeploy.provider_link(cfg(), name, sid, repo, _public_base())
    audit('git-connect', name, '%s (%s)' % (repo, view['branch']))
    return dict(gitdeploy.public(name), secret=secret, linked=linked)


@route('POST', '/api/git/key', role='admin')
def api_git_key(qs, body):
    """Create the site's deploy key and return its public half (add it to the repository as a read-only deploy key)."""
    key = gitdeploy.make_key(cfg(), body.get('name', ''))
    audit('git-key', body.get('name', ''))
    return {'public_key': key}


@route('POST', '/api/git/deploy', role='admin')
def api_git_deploy(qs, body):
    """Deploy the connected branch now. Progress: GET /api/job."""
    out = gitdeploy.trigger(cfg(), body.get('name', ''), me()['email'] or who(cfg()), 'manual')
    audit('git-deploy-start', body.get('name', ''), 'queued' if out.get('queued') else '')
    return out


@route('POST', '/api/git/disconnect', role='admin')
def api_git_disconnect(qs, body):
    gitdeploy.disconnect(cfg(), body.get('name', ''))
    audit('git-disconnect', body.get('name', ''))
    return {'ok': True}


@route('POST', '/api/hooks/git', public=True)
def api_hooks_git(qs, body):
    """Webhook for GitHub / GitLab pushes. Needs the site's secret: GitHub signs the body (X-Hub-Signature-256), GitLab sends the secret as X-Gitlab-Token."""
    site = qs.get('site', [''])[0]
    out = gitdeploy.hook(cfg(), site, ctx.headers, ctx.raw, body, ctx.ip)
    if not out.get('ignored'):
        activity.log('git-webhook', site, out.get('commit', ''), ctx.ip, True)
    return out


@route('GET', '/api/metrics/history')
def api_metrics_history(qs, body):
    """CPU, memory, disk, load and network for the last 1h / 6h / 24h, one point per uptime round."""
    span = qs.get('range', ['24h'])[0]
    return {'range': span, 'interval': monitor.schedule()['interval'], 'now': int(time.time()), 'points': metricslog.history(span)}


@route('GET', '/api/care/detail', role='admin')
def api_care_detail(qs, body):
    """What is behind one card of the checklist (banned addresses, biggest folders, memory users ...)."""
    return caredetail.detail(cfg(), qs.get('id', [''])[0], qs.get('path', [None])[0])


@route('POST', '/api/care/action', role='super_admin')
def api_care_action(qs, body):
    """Ban or unban an address in fail2ban (needs "confirm": "apply")."""
    text = caredetail.action(cfg(), body.get('action'), body.get('ip'), body.get('confirm'), ctx.ip)
    audit('care-' + body['action'], str(body.get('ip', '')), text)
    return {'text': text}


@route('GET', '/api/care', role='admin')
def api_care(qs, body):
    """Security and health checklist of the server, with the fixes that are safe to run right now."""
    return care.report(cfg())


@route('POST', '/api/care/fix', role='super_admin')
def api_care_fix(qs, body):
    """Run one fix from the checklist (needs "confirm": "apply"). Progress: GET /api/job."""
    job = care.start_fix(cfg(), body.get('fix', ''), body.get('confirm'))
    audit('care-fix', body.get('fix', ''), care.FIXES[body['fix']][1])
    return {'job': job}


@route('GET', '/api/perf', role='admin')
def api_perf(qs, body):
    """Memory, threads and cache figures of this app, and the performance profiles to choose from."""
    return {'stats': perf.stats(), 'profiles': {k: {'label': v['label'], 'about': v['about']} for k, v in perf.PROFILES.items()}, 'forced': bool(os.environ.get('CODERUNNER_PROFILE'))}


@route('POST', '/api/perf', role='super_admin')
def api_perf_save(qs, body):
    if os.environ.get('CODERUNNER_PROFILE'):
        raise ValueError('The profile is fixed by the server settings (CODERUNNER_PROFILE)')
    p = perf.set_profile(body.get('profile'))
    perf.apply_limits(cfg())
    audit('settings', 'performance', 'profile=%s' % p['name'])
    return {'stats': perf.stats()}


@route('POST', '/api/perf/trim', role='admin')
def api_perf_trim(qs, body):
    """Hand unused memory back to the system now."""
    freed = perf.trim()
    return {'freed_mb': freed, 'stats': perf.stats()}


@route('GET', '/api/activity')
def api_activity(qs, body):
    return {'events': activity.recent(300)}


# ------------------------------------------------------------------- api keys
@route('GET', '/api/keys', role='super_admin')
def api_keys(qs, body):
    browser_only()
    return {'keys': apikeys.list_keys()}


@route('POST', '/api/keys/create', role='super_admin')
def api_keys_create(qs, body):
    browser_only()
    token, entry = apikeys.create(body.get('name', ''))
    audit('api-key-create', entry['name'])
    return {'token': token, 'key': entry}


@route('POST', '/api/keys/revoke', role='super_admin')
def api_keys_revoke(qs, body):
    browser_only()
    apikeys.revoke(str(body.get('id', '')))
    audit('api-key-revoke', str(body.get('id', '')))
    return {'ok': True}


@route('GET', '/api/routes')
def api_routes(qs, body):
    """Every endpoint of the API (method, path, who may call it), for building another client."""
    return {'routes': sorted([{'method': m, 'path': p, 'role': r['role'] if not r['public'] else 'public', 'summary': (r['fn'].__doc__ or '').strip().split('\n')[0]}
                              for (m, p), r in ROUTES.items()], key=lambda r: (r['path'], r['method']))}


# ------------------------------------------------------------------ cloudflare
@route('GET', '/api/mapping')
def api_mapping(qs, body):
    if not config.cloudflare_ready():
        return {'ready': False, 'rows': []}
    return {'ready': True, 'rows': cloudflare.state()[3]}


@route('GET', '/api/cloudflare/verify', role='admin')
def api_cf_verify(qs, body):
    need_cf()
    return cloudflare.verify()


@route('GET', '/api/cloudflare/connection', role='admin')
def api_cf_connection(qs, body):
    return cloudflare.connection()


@route('POST', '/api/cloudflare/discover', role='super_admin')
def api_cf_discover(qs, body):
    """Check a pasted API token and list its accounts and tunnels (nothing is saved)."""
    return cloudflare.discover(body.get('token'), body.get('account_id'))


@route('POST', '/api/cloudflare/connect', role='super_admin')
def api_cf_connect(qs, body):
    out = cloudflare.connect(body.get('token'), body.get('account_id'), body.get('tunnel_id'))
    audit('cf-connect', out['account_id'][:8], out['tunnel_id'])
    return out


@route('GET', '/api/hetzner', role='admin')
def api_hetzner(qs, body):
    return billing.hetzner_connection()


@route('POST', '/api/hetzner/connect', role='super_admin')
def api_hetzner_connect(qs, body):
    out = billing.hetzner_connect(body.get('token'))
    audit('hetzner-connect', '', '%d servers' % out['servers'])
    return out


@route('POST', '/api/hetzner/disconnect', role='super_admin')
def api_hetzner_disconnect(qs, body):
    audit('hetzner-disconnect', '', '')
    return billing.hetzner_disconnect()


@route('GET', '/api/dnsproviders', role='admin')
def api_dnsp(qs, body):
    """Route 53 / DigitalOcean: which are connected and the server IP their records point at (never a secret)."""
    out = dnsproviders.connection()
    if not out['ip']:
        out['detected_ip'] = dnsproviders.detect_ip(cfg())
    return out


@route('GET', '/api/wildcards', role='admin')
def api_wildcards(qs, body):
    """Wildcard certificates on the server, and the domains a connected DNS provider can get one for."""
    return {'certs': wildcard.state(cfg()), 'zones': wildcard.zones()}


@route('POST', '/api/wildcards/issue', role='super_admin')
def api_wildcard_issue(qs, body):
    job = wildcard.start_issue(cfg(), body.get('zone'), body.get('email'))
    audit('wildcard-issue', str(body.get('zone', ''))[:100])
    return {'job': job}


@route('POST', '/api/wildcards/remove', role='super_admin')
def api_wildcard_remove(qs, body):
    lines = wildcard.remove(cfg(), body.get('zone'), body.get('confirm'))
    audit('wildcard-remove', str(body.get('zone', ''))[:100])
    return {'lines': lines}


@route('GET', '/api/dnsproviders/records', role='admin')
def api_dnsp_records(qs, body):
    """A records in one connected provider (Route 53 or DigitalOcean), marking those that point at this server."""
    return {'records': dnsproviders.records((qs.get('provider') or [''])[0])}


@route('POST', '/api/dnsproviders/map', role='admin')
def api_dnsp_map(qs, body):
    hosts = config.clean_hosts(body.get('hosts'))
    lines = dnsproviders.map_hosts(hosts)
    audit('dns-map', ', '.join(hosts)[:200])
    return {'lines': lines}


@route('POST', '/api/dnsproviders/unmap', role='admin')
def api_dnsp_unmap(qs, body):
    hosts = config.clean_hosts(body.get('hosts'))
    lines = dnsproviders.unmap_hosts(hosts)
    audit('dns-unmap', ', '.join(hosts)[:200])
    return {'lines': lines}


@route('POST', '/api/dnsproviders/connect', role='super_admin')
def api_dnsp_connect(qs, body):
    out = dnsproviders.connect(body.get('provider'), body.get('creds'), body.get('ip'))
    audit('dns-connect', body.get('provider', ''), out['ip'])
    return out


@route('POST', '/api/dnsproviders/disconnect', role='super_admin')
def api_dnsp_disconnect(qs, body):
    out = dnsproviders.disconnect(body.get('provider'))
    audit('dns-disconnect', body.get('provider', ''), '')
    return out


@route('POST', '/api/cloudflare/disconnect', role='super_admin')
def api_cf_disconnect(qs, body):
    out = cloudflare.disconnect()
    audit('cf-disconnect', '', '')
    return out


# ------------------------------------------------------------------ own nameservers
@route('GET', '/api/dns', role='super_admin')
def api_dns(qs, body):
    return dns.view()


@route('GET', '/api/dns/status', role='super_admin')
def api_dns_status(qs, body):
    return dns.status(cfg())


@route('GET', '/api/dns/zone', role='super_admin')
def api_dns_zone(qs, body):
    return dns.records(qs.get('domain', [''])[0])


@route('POST', '/api/dns/nameservers', role='super_admin')
def api_dns_ns(qs, body):
    out = dns.set_nameservers(body)
    audit('dns-nameservers', ', '.join(out['ns']))
    return out


@route('POST', '/api/dns/zone', role='super_admin')
def api_dns_add_zone(qs, body):
    out = dns.add_zone(body.get('domain'), body.get('ip'))
    audit('dns-zone-add', out['domain'])
    return out


@route('POST', '/api/dns/zone/delete', role='super_admin')
def api_dns_del_zone(qs, body):
    dns.delete_zone(body.get('domain'), body.get('confirm'))
    audit('dns-zone-delete', body.get('domain', ''))
    return dns.view()


@route('POST', '/api/dns/record', role='super_admin')
def api_dns_record(qs, body):
    out = dns.put_record(body.get('domain'), body)
    audit('dns-record', out['domain'], '%s %s %s' % (body.get('name'), body.get('type'), body.get('value')))
    return out


@route('POST', '/api/dns/record/delete', role='super_admin')
def api_dns_record_delete(qs, body):
    out = dns.delete_record(body.get('domain'), body.get('id'))
    audit('dns-record-delete', out['domain'], str(body.get('id')))
    return out


@route('POST', '/api/dns/apply', role='super_admin')
def api_dns_apply(qs, body):
    """Write the zones to the server and start/reload NSD. Progress: GET /api/job."""
    out = dns.apply(cfg(), me()['email'] or who(cfg()))
    audit('dns-apply', '', '')
    return out


@route('POST', '/api/dns/ports', role='super_admin')
def api_dns_ports(qs, body):
    """Open port 53 (dns) or 80 and 443 (web) in the firewall. Needs confirm = "open"."""
    lines = dns.ports(cfg(), body.get('which'), body.get('confirm'))
    audit('dns-ports', str(body.get('which')), '; '.join(lines)[:200])
    return {'lines': lines}


@route('POST', '/api/dns/direct', role='super_admin')
def api_dns_direct(qs, body):
    """Let a site answer on ports 80/443 itself (or go back to the tunnel only). Needs confirm = the site name."""
    lines = dns.site_direct(cfg(), body.get('name', ''), bool(body.get('on')), body.get('confirm'))
    audit('site-direct', body.get('name', ''), 'on' if body.get('on') else 'off')
    return {'lines': lines}


@route('POST', '/api/mapping/check')
def api_map_check(qs, body):
    need_cf()
    rows = cloudflare.state(config.clean_hosts(body.get('hosts')))[3]
    return {'rows': rows, 'text': cloudflare.describe(rows)}


@route('POST', '/api/mapping/map')
def api_map(qs, body):
    need_cf()
    hosts = config.clean_hosts(body.get('hosts'))
    lines = cloudflare.map_hosts(hosts)
    audit('cf-map', ', '.join(hosts), '; '.join(lines)[:200])
    return {'lines': lines}


@route('POST', '/api/mapping/replace', role='super_admin')
def api_map_replace(qs, body):
    """Replace a conflicting DNS record by the tunnel. The caller must send the hostname as `confirm`: the screen asks the user to type it."""
    need_cf()
    host = (config.clean_hosts([body.get('host')]) or [''])[0]
    lines = cloudflare.replace_dns(host, body.get('confirm'))
    audit('cf-replace', host, '; '.join(lines)[:300])
    return {'lines': lines}


@route('POST', '/api/mapping/unmap')
def api_unmap(qs, body):
    need_cf()
    hosts = config.clean_hosts(body.get('hosts'))
    lines = cloudflare.unmap_hosts(hosts)
    audit('cf-unmap', ', '.join(hosts), '; '.join(lines)[:200])
    return {'lines': lines}


# ------------------------------------------------------------------------ HTTP
NEED_LINK = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Open the portal from the app</title>
<style>body{font:16px/1.5 system-ui,sans-serif;background:#0b0b0f;color:#e8e8ee;margin:0;display:grid;min-height:100vh;place-items:center}main{max-width:34rem;padding:2rem}
h1{font-size:1.25rem;margin:0 0 .75rem}code{background:#1c1c24;padding:.1rem .4rem;border-radius:.3rem}li{margin:.5rem 0}p{color:#a9a9b8}</style></head><body><main>
<h1>This browser does not have access yet</h1>
<p>The portal opens with a private link that the app creates every time it starts. This page was opened without it (for example from a bookmark or an old tab).</p>
<ol><li>Look for the window where the app is running (it was started with <code>start.bat</code> or <code>./start.sh</code>) and open the link that begins with <code>http://127.0.0.1:</code> and ends with <code>?t=...</code></li>
<li>No window any more? Start the app again with <code>start.bat</code> (Windows) or <code>./start.sh</code>: it opens the portal for you.</li></ol>
<p>Tip: set <code>CODERUNNER_TOKEN</code> to a secret of your own before starting, and the link stays the same every time.</p></main></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # -- plumbing
    def reply(self, body, code=200, ctype='application/json', headers=()):
        if not isinstance(body, bytes):
            body = (body if isinstance(body, str) else json.dumps(body)).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith(('text/', 'application/json')) else ''))
        self.send_header('Content-Length', str(len(body)))
        if not any(k == 'Cache-Control' for k, _ in headers):
            self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        if STATE['secure_cookies']:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        for k, v in headers:
            self.send_header(k, v)
        for c in getattr(ctx, 'cookies', []):
            self.send_header('Set-Cookie', c)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def host_ok(self):
        host = self.headers.get('Host', '')
        return host in ('127.0.0.1:%d' % STATE['port'], 'localhost:%d' % STATE['port']) or host.split(':')[0].lower() in STATE['allowed_hosts']

    def client_ip(self):
        if STATE['behind_proxy']:
            ip = self.headers.get('CF-Connecting-IP') or (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            if ip:
                return ip[:45]
        return self.client_address[0]

    def identify(self):
        """Work out who is calling: API key, signed-in account (login mode) or the launch-token cookie (token mode)."""
        ctx.user, ctx.via_key, ctx.cookies, ctx.session_token, ctx.ip = None, False, [], '', self.client_ip()
        authz = self.headers.get('Authorization', '')
        if authz.startswith('Bearer '):
            if apikeys.verify(authz[7:].strip()):
                ctx.via_key = True
                ctx.user = {'id': 0, 'email': 'api-key', 'role': 'admin'}
            return
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return
        if STATE['auth'] == 'login':
            ctx.session_token = jar[SESSION_COOKIE].value if SESSION_COOKIE in jar else ''
            ctx.user = auth.session_user(ctx.session_token)
        elif 'cr' in jar and hmac.compare_digest(jar['cr'].value, STATE['token']):
            ctx.user = {'id': 0, 'email': who(cfg()), 'role': 'super_admin'}

    def static(self, rel):
        full = os.path.normpath(os.path.join(config.WEB, rel))
        if not full.startswith(config.WEB + os.sep) or not os.path.isfile(full):
            return self.reply({'error': 'not found'}, 404)
        st = os.stat(full)
        tag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
        if self.headers.get('If-None-Match') == tag:     # unchanged since the browser last loaded it: no body, no file read
            return self.reply(b'', 304, 'text/plain', [('ETag', tag), ('Cache-Control', 'no-cache')])
        with open(full, 'rb') as f:
            data = f.read()
        self.reply(data, 200, TYPES.get(os.path.splitext(full)[1], mimetypes.guess_type(full)[0] or 'application/octet-stream'), [('ETag', tag), ('Cache-Control', 'no-cache')])

    def api(self, method, u, body):
        r = ROUTES.get((method, u.path))
        if not r:
            return self.reply({'error': 'not found'}, 404)
        if not r['public']:
            if not ctx.user:
                return self.reply({'error': 'Sign in required', 'auth': STATE['auth']}, 401)
            if self.blocked() and not (u.path.startswith('/api/auth/') or u.path == '/api/session'):
                return self.reply({'error': MSG_2FA, 'need_2fa_setup': True}, 403)
            if auth.RANK[ctx.user['role']] < auth.RANK[r['role']]:
                return self.reply({'error': 'Your role (%s) cannot do this' % ctx.user['role']}, 403)
        try:
            return self.reply(r['fn'](parse_qs(u.query), body))
        except LookupError as e:
            return self.reply({'error': str(e)}, 404)
        except PermissionError as e:
            return self.reply({'error': str(e)}, 429 if str(e).startswith('Too many') else 403)
        except ValueError as e:
            return self.reply({'error': str(e)}, 400)
        except (TypeError, KeyError):  # a value of the wrong type or a missing field in the request body: the caller's mistake, not a server fault
            traceback.print_exc()
            return self.reply({'error': 'Some values in the request are missing or have the wrong type'}, 400)
        except Exception as e:  # surface the reason in the UI
            return self.reply({'error': str(e)}, 500)

    def blocked(self):
        """Signed in with a password, but two-factor is required and not set up yet: only the setup calls work."""
        u = ctx.user
        return bool(u and not ctx.via_key and u['id'] and not u.get('totp') and require_2fa())

    def download_backup(self, qs):
        if self.blocked():
            return self.reply({'error': MSG_2FA, 'need_2fa_setup': True}, 403)
        if auth.RANK[ctx.user['role']] < auth.RANK['admin']:
            return self.reply({'error': 'Your role (%s) cannot do this' % ctx.user['role']}, 403)
        try:
            path = dbops.backup_path(qs.get('file', [''])[0])
            c = cfg()
            rc, out = run(c, 'sudo stat -c %%s %s' % q(path))
            if rc or not out.strip().isdigit():
                return self.reply({'error': 'backup not found'}, 404)
            audit('db-download', os.path.basename(path))
            self.send_response(200)
            self.send_header('Content-Type', 'application/gzip')
            self.send_header('Content-Length', out.strip())
            self.send_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(path))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            p = popen(c, 'sudo cat %s' % q(path), stdin=False)
            shutil.copyfileobj(p.stdout, self.wfile)
            p.wait()
        except ValueError as e:
            self.reply({'error': str(e)}, 400)

    def stream_logs(self, qs):
        """Server-Sent Events: follow a log until the browser goes away."""
        if self.blocked():
            return self.reply({'error': MSG_2FA, 'need_2fa_setup': True}, 403)
        if auth.RANK[ctx.user['role']] < auth.RANK['admin']:
            return self.reply({'error': 'Your role (%s) cannot do this' % ctx.user['role']}, 403)
        if not perf.streams.acquire(timeout=0):
            return self.reply({'error': 'Too many live logs are open. Close one and try again.'}, 429)
        try:
            p = popen(cfg(), monitor.log_command(qs.get('kind', ['site'])[0], qs.get('name', [''])[0]), stdin=False)
        except (ValueError, RuntimeError) as e:
            perf.streams.release()
            return self.reply({'error': str(e)}, 400)
        lines = queue.Queue(maxsize=500)    # a slow browser slows the reader down instead of filling memory
        stop = threading.Event()

        def pump():
            for l in iter(p.stdout.readline, b''):
                while not stop.is_set():
                    try:
                        lines.put(l, timeout=1)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
            lines.put(None)
        threading.Thread(target=pump, daemon=True).start()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            while True:
                try:
                    l = lines.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b': ping\n\n')   # keeps the tunnel open and notices a closed browser
                    self.wfile.flush()
                    continue
                if l is None:
                    self.wfile.write(b'event: end\ndata: \n\n')
                    break
                self.wfile.write(b'data: ' + json.dumps(l.decode('utf-8', 'replace').rstrip('\r\n')).encode() + b'\n\n')
                self.wfile.flush()
        except OSError:
            pass
        finally:
            stop.set()
            p.kill()
            perf.streams.release()

    # -- verbs
    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if not self.host_ok():
            return self.reply('bad host', 403, 'text/plain')
        self.identify()
        login_mode = STATE['auth'] == 'login'
        if u.path == '/' and not login_mode and hmac.compare_digest(qs.get('t', [''])[0], STATE['token']):
            ctx.cookies.append('cr=%s; HttpOnly; SameSite=Lax; Path=/' % STATE['token'])
            return self.reply('', 302, 'text/plain', [('Location', '/')])
        if u.path == '/':
            if login_mode or ctx.user:
                return self.static('index.html')
            return self.reply(NEED_LINK, 401, 'text/html')
        if u.path.startswith('/static/'):
            rel = u.path[len('/static/'):]
            if (not login_mode or rel == 'manual.md') and not ctx.user:   # the sign-in page needs the app files; the manual needs sign-in
                return self.reply({'error': 'not authorised'}, 401)
            return self.static(rel)
        if u.path == '/api/logs/stream':
            if not ctx.user:
                return self.reply({'error': 'Sign in required', 'auth': STATE['auth']}, 401)
            return self.stream_logs(qs)
        if u.path == '/api/databases/download':
            if not ctx.user:
                return self.reply({'error': 'Sign in required', 'auth': STATE['auth']}, 401)
            return self.download_backup(qs)
        if u.path.startswith('/api/'):
            return self.api('GET', u, {})
        self.reply({'error': 'not found'}, 404)

    do_HEAD = do_GET  # health checkers use HEAD; the body is left out in reply()

    def do_POST(self):
        u = urlparse(self.path)
        if not self.host_ok():
            return self.reply({'error': 'not authorised'}, 403)
        self.identify()
        webhook = u.path == '/api/hooks/git'          # called by GitHub/GitLab, which cannot send our header: it is protected by its signature instead
        if not webhook and not ctx.via_key and self.headers.get('X-Requested-With') != 'coderunner':
            return self.reply({'error': 'not authorised'}, 403)
        length = int(self.headers.get('Content-Length', 0) or 0)
        if u.path == '/api/upload':
            if not ctx.user:
                return self.reply({'error': 'Sign in required', 'auth': STATE['auth']}, 401)
            if auth.RANK[ctx.user['role']] < auth.RANK['admin']:
                return self.reply({'error': 'Your role (%s) cannot do this' % ctx.user['role']}, 403)
            if self.blocked():
                return self.reply({'error': MSG_2FA, 'need_2fa_setup': True}, 403)
            try:
                path = uploads.save_zip(self.rfile, length)
                audit('upload', os.path.basename(os.path.dirname(path)), '%.1f MB zip' % (length / 1e6))
                return self.reply({'path': path})
            except ValueError as e:
                return self.reply({'error': str(e)}, 400)
        if length > MAX_JSON:
            return self.reply({'error': 'request too large'}, 413)
        raw = self.rfile.read(length) or b''
        ctx.raw, ctx.headers = raw, self.headers          # a webhook signature is computed over the exact bytes received
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            return self.reply({'error': 'bad JSON'}, 400)
        self.api('POST', u, body)


class BoundedServer(http.server.ThreadingHTTPServer):
    """One thread per request as before, but never more than the profile allows: extra callers wait a moment, then get a clear 503."""
    daemon_threads = True
    request_queue_size = 32

    def process_request(self, request, client_address):
        if perf.slots.acquire(timeout=2.0):
            return super().process_request(request, client_address)
        try:
            request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nRetry-After: 2\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        except OSError:
            pass
        self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            perf.slots.release()


def make_server(port, bind='127.0.0.1'):
    """First free port from `port` upwards. Prepares the accounts database in login mode."""
    if STATE['auth'] == 'login':
        auth.init(os.environ.get('CODERUNNER_ADMIN_EMAIL', ''))
    for p in range(port, port + 20):
        try:
            srv = BoundedServer((bind, p), Handler)
            STATE['port'] = p
            return srv
        except OSError:
            continue
    raise OSError('no free port found')
