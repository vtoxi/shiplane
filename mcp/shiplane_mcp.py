#!/usr/bin/env python3
"""Shiplane MCP server: lets an AI (Claude Code, Claude Desktop, any MCP client) manage a Shiplane server. Python standard library only.

It is a thin client of Shiplane's own HTTP API, so it can do exactly what the API key you give it can do and nothing more
(an admin key cannot reach the super-admin-only routes or the browser Terminal; a viewer key is read-only).

    export SHIPLANE_URL=https://shiplane.example.com
    export SHIPLANE_KEY=crk_...          # Settings > API keys. From the environment or an env file, never from the command line.
    python mcp/shiplane_mcp.py           # speaks MCP (JSON-RPC, one message per line) on stdin/stdout

Optional environment:
    SHIPLANE_ENV_FILE=path     read these settings from a file instead (default: ./.env, then ~/.shiplane.env; real environment variables win)
    SHIPLANE_MCP_READONLY=1    hide every tool that changes something (and make shiplane_api GET-only)
    SHIPLANE_MCP_WAIT=900      seconds to wait for a deploy/backup/upgrade job to finish before returning its job id
Everything that deletes something needs an explicit `confirm` argument; tools carry MCP readOnly/destructive hints too.
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

VERSION = '1.1.0'


def load_env_file():
    """Fill SHIPLANE_* settings from an env file when they are not already in the environment (the real environment always wins).
    Looked up in order: $SHIPLANE_ENV_FILE, ./.env, ~/.shiplane.env. Lines are KEY=VALUE (optionally `export KEY=VALUE`, quoted values allowed);
    only SHIPLANE_* names are read. Values are never printed."""
    for path in (os.environ.get('SHIPLANE_ENV_FILE'), os.path.join(os.getcwd(), '.env'), os.path.expanduser('~/.shiplane.env')):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8-sig') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('export '):
                        line = line[7:].lstrip()
                    k, sep, v = line.partition('=')
                    k, v = k.strip(), v.strip()
                    if sep and k.startswith('SHIPLANE_') and k not in os.environ:
                        if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                            v = v[1:-1]
                        elif ' #' in v:
                            v = v.split(' #', 1)[0].rstrip()
                        os.environ[k] = v
        except (OSError, UnicodeDecodeError):
            pass


load_env_file()
PROTOCOLS = ['2025-06-18', '2025-03-26', '2024-11-05']
READONLY = os.environ.get('SHIPLANE_MCP_READONLY', '') not in ('', '0', 'false')
WAIT = int(os.environ.get('SHIPLANE_MCP_WAIT') or 900)
INSTRUCTIONS = ('Tools to run a Shiplane server: sites, deploys, databases, backups, DNS/domains, server care, mail, firewall, files, Git deploy. '
                'Start with shiplane_status. Tools that start a long job (deploy, backup, upgrade) wait for it and return its log. '
                'Destructive tools need confirm; for a site delete, confirm must be the exact site name. '
                'For anything without a dedicated tool use shiplane_routes then shiplane_api.')


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------- HTTP
def base_url():
    u = (os.environ.get('SHIPLANE_URL') or '').strip().rstrip('/')
    if not u.startswith(('http://', 'https://')):
        raise ApiError('SHIPLANE_URL is not set (example: https://shiplane.example.com)')
    return u


def call(method, path, query=None, body=None, raw=None, timeout=120):
    key = os.environ.get('SHIPLANE_KEY', '').strip()
    if not key:
        raise ApiError('SHIPLANE_KEY is not set (create an API key in Shiplane: Settings > API keys)')
    if not path.startswith('/api/') or '..' in path or '\n' in path:
        raise ApiError('path must start with /api/')
    url = base_url() + path
    q = {k: v for k, v in (query or {}).items() if v is not None and v != ''}
    if q:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list, bool)) else v) for k, v in q.items()})
    data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode() if method == 'POST' else None)
    req = urllib.request.Request(url, data, method=method, headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'shiplane-mcp/' + VERSION,
                                 'Content-Type': 'application/zip' if raw is not None else 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read()
            ctype = r.headers.get('Content-Type', '')
            return json.loads(text or b'null') if 'json' in ctype or not ctype else text.decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get('error')
        except ValueError:
            msg = ''
        hint = {401: ' (check SHIPLANE_KEY)', 403: ' (this API key\'s role is too low for that; super-admin routes and the Terminal are browser-only)'}.get(e.code, '')
        raise ApiError('%s %s failed: HTTP %d %s%s' % (method, path, e.code, msg or e.reason, hint))
    except urllib.error.URLError as e:
        raise ApiError('cannot reach %s: %s' % (base_url(), e.reason))


def wait_job(job, since=0, timeout=None, kind='job'):
    """Follow a Shiplane job until it ends (or the wait runs out). Returns (done, ok, log text)."""
    lines, end = [], time.time() + (timeout if timeout is not None else WAIT)
    while True:
        r = call('GET', '/api/job', {'id': job, 'since': since})
        lines += r.get('lines') or []
        since = r.get('next', since)
        if r.get('done'):
            return True, bool(r.get('ok')), '\n'.join(lines)
        if time.time() > end:
            return False, None, '\n'.join(lines)
        time.sleep(2)


# --------------------------------------------------------------------------- tool table
def P(type_, desc, req=False, enum=None, items=None):
    return {'type': type_, 'desc': desc, 'req': req, 'enum': enum, 'items': items}


TOOLS = []     # {name, kind r|w|d, method, path, desc, props}


def tool(_name, _kind, _method, _path, _desc, /, _fixed=None, **props):      # positional-only: tools may have properties called name, path, method...
    TOOLS.append({'name': _name, 'kind': _kind, 'method': _method, 'path': _path, 'desc': _desc, 'props': props, 'fixed': _fixed or {}})


NAME = P('string', 'Site name', True)
CONFIRM = P('boolean', 'Must be true: you are sure', True)

# ---- overview, jobs, logs
tool('shiplane_status', 'r', 'GET', '/api/status', 'Everything at a glance: every site (type, address, up/down, service state, releases, database), the server, runtimes and warnings. Start here.')
tool('shiplane_job', 'r', 'GET', '/api/job', 'Read the log of a background job (deploy, backup, upgrade...). Pass the since value returned earlier to read only new lines.', id=P('string', 'Job id', True), since=P('integer', 'Line to start from (default 0)'))
tool('shiplane_metrics', 'r', 'GET', '/api/metrics', 'Live CPU, memory, disk and load of the server.')
tool('shiplane_metrics_history', 'r', 'GET', '/api/metrics/history', 'The last 24 hours of server metrics.', range=P('string', 'Window, e.g. 1h, 6h, 24h'))
tool('shiplane_activity', 'r', 'GET', '/api/activity', 'The activity log: who did what and when (deploys, deletes, logins, API calls).')
tool('shiplane_logs', 'r', 'GET', '/api/logs', 'Application logs of one site (journal), with level, text and time filters.', name=NAME, lines=P('integer', 'How many lines (default 200)'),
     level=P('string', 'error / warn / info'), q=P('string', 'Only lines containing this text'), since=P('string', 'For example "1 hour ago"'))
tool('shiplane_server_info', 'r', 'GET', '/api/server/info', 'Server details: OS, kernel, uptime, services, containers, packages waiting for upgrade.')
tool('shiplane_disk', 'r', 'GET', '/api/disk', 'Disk usage by area and what can be cleaned safely.')
tool('shiplane_billing', 'r', 'GET', '/api/billing', 'Provider usage and cost information, when a hosting provider is connected.')
tool('shiplane_perf', 'r', 'GET', '/api/perf', 'The performance profile (light/balanced/full) and the app\'s own resource use.')
tool('shiplane_runtimes', 'r', 'GET', '/api/runtimes', 'Installed runtimes (Node, .NET, PHP, Python...) and versions.')
tool('shiplane_health', 'r', 'GET', '/api/health', 'Is Shiplane itself healthy and which version is it.')

# ---- sites
tool('shiplane_site_config', 'r', 'GET', '/api/site/config', 'The saved settings of one site (type, address, port, start command, health check...).', name=NAME)
tool('shiplane_site_env_values', 'r', 'GET', '/api/site/env/values', 'The environment variables of a site WITH their values (may contain secrets).', name=NAME)
tool('shiplane_site_check', 'r', 'POST', '/api/site/check', 'Check now whether a site answers over HTTP (status code and milliseconds).', name=NAME)
tool('shiplane_site_start', 'w', 'POST', '/api/action', 'Start a site\'s service or container.', name=NAME, _fixed={'action': 'start'})
tool('shiplane_site_stop', 'w', 'POST', '/api/action', 'Stop a site\'s service or container (the site goes offline).', name=NAME, _fixed={'action': 'stop'})
tool('shiplane_site_restart', 'w', 'POST', '/api/action', 'Restart a site\'s service or container.', name=NAME, _fixed={'action': 'restart'})
tool('shiplane_site_activate_release', 'w', 'POST', '/api/action', 'Roll back or forward: make an earlier release the live one.', name=NAME, release=P('string', 'Release folder name from shiplane_status', True), _fixed={'action': 'activate'})
tool('shiplane_site_delete', 'd', 'POST', '/api/action', 'DELETE a site: stops it and removes its files, service and address. With purge also deletes its databases, their backups, its Git connection and scheduled tasks. Irreversible.',
     name=NAME, _fixed={'action': 'delete'}, confirm=P('string', 'The exact site name, to confirm', True), purge=P('boolean', 'Also delete databases, backups, Git connection, cron (recommended for a full cleanup)'),
     dropDb=P('boolean', 'Also drop its Postgres database'), unmapCf=P('boolean', 'Also remove its Cloudflare route and DNS record'), unmapDns=P('boolean', 'Also remove its A records at Route 53 / DigitalOcean'), hosts=P('array', 'Hostnames to unmap', items='string'))
tool('shiplane_site_env_set', 'w', 'POST', '/api/site/env', 'Set and/or unset environment variables of a site and restart it. Values are write-only afterwards.', name=NAME,
     set=P('object', 'Variables to set, e.g. {"API_KEY": "..."}'), unset=P('array', 'Variable names to remove', items='string'))
tool('shiplane_site_rename', 'w', 'POST', '/api/site/rename', 'Change the public address (hostname) of a site.', name=NAME, hostname=P('string', 'New hostname', True))
tool('shiplane_site_run', 'w', 'POST', '/api/site/run', 'Run one of the site\'s saved/allowed commands (for example a migration) on the server.', name=NAME, command=P('string', 'The command', True))
tool('shiplane_site_promote', 'w', 'POST', '/api/site/promote', 'Promote staging to production: the live release of site `from` becomes the live release of site `to`, with health gate and automatic rollback.',
     **{'from': P('string', 'Staging site', True), 'to': P('string', 'Production site', True), 'confirm': P('string', 'The production site name', True)})

# ---- deploy
tool('shiplane_deploy_git', 'w', 'POST', '/api/git/deploy', 'Deploy a site from its connected Git repository now (pull, build, release, health check).', name=NAME)
tool('shiplane_git_info', 'r', 'GET', '/api/git', 'Git connection of a site (repository, branch, last deploy), or of all sites.', name=P('string', 'Site name (omit for all)'))
tool('shiplane_git_save', 'w', 'POST', '/api/git/save', 'Save a site\'s Git settings (repository, branch, build command, output folder, auto-deploy).', name=NAME, repo=P('string', 'Repository URL'), branch=P('string', 'Branch'), build=P('string', 'npm / none / ...'),
     buildCmd=P('string', 'Build command'), output=P('string', 'Output folder'), auto=P('boolean', 'Deploy on every push'))
tool('shiplane_git_create', 'w', 'POST', '/api/git/create', 'Create a NEW site from a Git repository and deploy it.', app=P('object', 'Site settings: name, hostname, type...', True), git=P('object', 'Git settings: repo, branch, build...', True),
     repo=P('string', 'Repository'), session=P('string', 'Git provider sign-in session id'))
tool('shiplane_git_key', 'w', 'POST', '/api/git/key', 'Create (or show) the deploy key of a site; add its public key to the repository.', name=NAME)
tool('shiplane_git_disconnect', 'd', 'POST', '/api/git/disconnect', 'Remove a site\'s Git connection, its key and its checkout.', name=NAME, confirm=CONFIRM)
tool('shiplane_gitprov_repos', 'r', 'GET', '/api/gitprov/repos', 'Repositories visible to a signed-in GitHub/GitLab session.', session=P('string', 'Session id', True))
tool('shiplane_gitprov_branches', 'r', 'GET', '/api/gitprov/branches', 'Branches of a repository.', session=P('string', 'Session id', True), repo=P('string', 'Repository', True))
tool('shiplane_gitprov_detect', 'r', 'GET', '/api/gitprov/detect', 'Detect the type/build of a repository branch (static, Node, .NET, Docker...).', session=P('string', 'Session id', True), repo=P('string', 'Repository', True), branch=P('string', 'Branch'))
tool('shiplane_gitprov_token', 'w', 'POST', '/api/gitprov/token', 'Sign in to GitHub/GitLab with a personal token (kept in memory for 30 minutes).', provider=P('string', 'github or gitlab', True, ['github', 'gitlab']), token=P('string', 'Personal access token', True))
tool('shiplane_gitprov_connect', 'w', 'POST', '/api/gitprov/connect', 'One click: connect a site to a repository (deploy key + webhook).', name=NAME, repo=P('string', 'Repository', True), session=P('string', 'Session id', True))
tool('shiplane_apps_catalog', 'r', 'GET', '/api/apps', 'The one-click app catalog (runtimes, databases, WordPress, Laravel, phpMyAdmin...) and what is installed.')
tool('shiplane_apps_install', 'w', 'POST', '/api/apps/install', 'Install an app or runtime from the catalog.', id=P('string', 'Catalog id from shiplane_apps_catalog', True))

# ---- databases and backups
tool('shiplane_databases', 'r', 'GET', '/api/databases', 'Postgres databases with sizes, plus the backup files on the server.')
tool('shiplane_dbengines', 'r', 'GET', '/api/dbengines', 'MySQL/MariaDB and SQL Server status and databases.')
tool('shiplane_database_create', 'w', 'POST', '/api/databases/create', 'Create a Postgres database with its own login.', name=P('string', 'Database name', True), user=P('string', 'Login name'), password=P('string', 'Password (generated when omitted)'))
tool('shiplane_database_backup', 'w', 'POST', '/api/databases/backup', 'Back up one database now.', name=P('string', 'Database name', True), engine=P('string', 'postgres (default) or mysql', False, ['postgres', 'mysql']))
tool('shiplane_database_restore', 'd', 'POST', '/api/databases/restore', 'Restore a database from a backup file (overwrites the current data).', name=P('string', 'Database name', True), file=P('string', 'Backup file name', True), confirm=P('string', 'The database name, to confirm', True))
tool('shiplane_database_verify', 'w', 'POST', '/api/databases/verify', 'Test-restore a backup into a scratch database to prove it is usable.', file=P('string', 'Backup file name', True))
tool('shiplane_database_drop', 'd', 'POST', '/api/databases/drop', 'DROP a Postgres database and its data. Irreversible.', name=P('string', 'Database name', True), confirm=P('string', 'The database name, to confirm', True))
tool('shiplane_backup_delete', 'd', 'POST', '/api/databases/backup-delete', 'Delete one backup file.', file=P('string', 'Backup file name', True), confirm=CONFIRM)
tool('shiplane_mysql_create', 'w', 'POST', '/api/dbengines/mysql/create', 'Create a MySQL/MariaDB database with its own login.', name=P('string', 'Database name', True), user=P('string', 'Login'), password=P('string', 'Password'))
tool('shiplane_mysql_drop', 'd', 'POST', '/api/dbengines/mysql/drop', 'DROP a MySQL/MariaDB database. Irreversible.', name=P('string', 'Database name', True), confirm=P('string', 'The database name', True))
tool('shiplane_backups_schedule', 'r', 'GET', '/api/backups/schedule', 'Nightly backup settings (hour, retention, test restore) and off-server copy status.')
tool('shiplane_backups_schedule_set', 'w', 'POST', '/api/backups/schedule', 'Change the nightly backup schedule.', enabled=P('boolean', 'On/off', True), hour=P('integer', 'Hour 0-23'), keep=P('integer', 'Backups to keep per database'), verify=P('boolean', 'Test-restore after each backup'))
tool('shiplane_backups_run', 'w', 'POST', '/api/backups/run', 'Back up every database now and prune old backups.')

# ---- server operations
tool('shiplane_server_service', 'w', 'POST', '/api/server/service', 'Start, stop, restart, enable or disable an allow-listed system service (caddy, postgresql, redis, docker...).', name=P('string', 'Service', True),
     action=P('string', 'What to do', True, ['start', 'stop', 'restart', 'enable', 'disable']))
tool('shiplane_server_container', 'w', 'POST', '/api/server/container', 'Start, stop or restart a Docker container.', name=P('string', 'Container', True), action=P('string', 'What to do', True, ['start', 'stop', 'restart']))
tool('shiplane_server_upgrade', 'w', 'POST', '/api/server/upgrade', 'Install pending operating system package upgrades (apt). Returns the log.')
tool('shiplane_disk_clean', 'w', 'POST', '/api/disk/clean', 'Free disk space safely (journal, apt cache, old releases...). Pass `what` from shiplane_disk.', what=P('string', 'Which cleanup', True))
tool('shiplane_perf_trim', 'w', 'POST', '/api/perf/trim', 'Trim the app\'s own logs, jobs and caches now.')
tool('shiplane_cron', 'r', 'GET', '/api/cron', 'Scheduled tasks (cron) of a site.', name=P('string', 'Site name'))
tool('shiplane_cron_add', 'w', 'POST', '/api/cron/add', 'Add a scheduled task to a site.', name=NAME, schedule=P('string', 'Cron schedule, e.g. "30 3 * * *"', True), command=P('string', 'The command', True))
tool('shiplane_cron_delete', 'd', 'POST', '/api/cron/delete', 'Remove a scheduled task.', name=NAME, id=P('string', 'Task id from shiplane_cron', True), confirm=CONFIRM)

# ---- care, monitoring
tool('shiplane_care', 'r', 'GET', '/api/care', 'Server care checklist: security, early warnings and what can be fixed.')
tool('shiplane_care_detail', 'r', 'GET', '/api/care/detail', 'Details behind one care card.', id=P('string', 'Care item id', True), path=P('string', 'Folder, when the card needs one'))
tool('shiplane_care_preview', 'r', 'GET', '/api/care/preview', 'Preview exactly what a care fix would do, without doing it.', fix=P('string', 'Fix id', True))
tool('shiplane_monitor', 'r', 'GET', '/api/monitor', 'Uptime monitoring settings and the latest results.')
tool('shiplane_watch', 'r', 'GET', '/api/watch', 'Smart alerts: what is being watched and what fired.')
tool('shiplane_heal', 'r', 'GET', '/api/heal', 'Auto-heal: its settings and what it repaired.')

# ---- domains, DNS, Cloudflare
tool('shiplane_domain_check', 'r', 'GET', '/api/domaincheck', 'Who hosts a domain\'s DNS, where it points, and what to change to point it at this server.', domain=P('string', 'Domain', True), ip=P('string', 'Server IP to compare with'))
tool('shiplane_domains_health', 'r', 'GET', '/api/domains/health', 'Domain and SSL certificate health of every site (expiry, DNS, redirects).')
tool('shiplane_mapping', 'r', 'GET', '/api/mapping', 'Which hostnames are mapped through Cloudflare / DNS providers.')
tool('shiplane_mapping_check', 'r', 'POST', '/api/mapping/check', 'Check hostnames before mapping them (zone found, conflicts).', hosts=P('array', 'Hostnames', True, items='string'))
tool('shiplane_mapping_map', 'w', 'POST', '/api/mapping/map', 'Map hostnames to the server through Cloudflare (tunnel + DNS).', hosts=P('array', 'Hostnames', True, items='string'))
tool('shiplane_mapping_unmap', 'd', 'POST', '/api/mapping/unmap', 'Remove hostnames\' Cloudflare routes and DNS records.', hosts=P('array', 'Hostnames', True, items='string'), confirm=CONFIRM)
tool('shiplane_cloudflare_zones', 'r', 'GET', '/api/cloudflare/zones', 'Cloudflare zones available to the connected token.')
tool('shiplane_cloudflare_connection', 'r', 'GET', '/api/cloudflare/connection', 'Is Cloudflare connected and what the token can do.')
tool('shiplane_dnsproviders', 'r', 'GET', '/api/dnsproviders', 'Connected DNS providers (Route 53, DigitalOcean).')
tool('shiplane_dnsproviders_records', 'r', 'GET', '/api/dnsproviders/records', 'DNS records at a provider.', provider=P('string', 'Provider id', True))
tool('shiplane_dnsproviders_map', 'w', 'POST', '/api/dnsproviders/map', 'Create A records pointing hostnames at this server (Route 53 / DigitalOcean).', hosts=P('array', 'Hostnames', True, items='string'))
tool('shiplane_dnsproviders_unmap', 'd', 'POST', '/api/dnsproviders/unmap', 'Remove those A records.', hosts=P('array', 'Hostnames', True, items='string'), confirm=CONFIRM)
tool('shiplane_wildcards', 'r', 'GET', '/api/wildcards', 'Wildcard certificates (*.domain) that exist.')

# ---- firewall, mail
tool('shiplane_firewall', 'r', 'GET', '/api/firewall', 'Firewall status and rules.')
tool('shiplane_firewall_add', 'w', 'POST', '/api/firewall/add', 'Add a firewall rule. Use preview=true to see the commands first.', action=P('string', 'allow or deny', True, ['allow', 'deny']), port=P('string', 'Port, e.g. 443', True),
     proto=P('string', 'tcp (default) or udp'), **{'from': P('string', 'Source address or "any"')}, preview=P('boolean', 'Only show what would run'))
tool('shiplane_firewall_delete', 'd', 'POST', '/api/firewall/delete', 'Delete a firewall rule (never lock yourself out: preview first).', action=P('string', 'allow or deny', True, ['allow', 'deny']), port=P('string', 'Port', True),
     proto=P('string', 'tcp or udp'), **{'from': P('string', 'Source')}, preview=P('boolean', 'Only show what would run'), confirm=CONFIRM)
tool('shiplane_email_check', 'r', 'GET', '/api/email/check', 'Check a domain\'s email setup: MX, SPF, DKIM, DMARC.', domain=P('string', 'Domain', True), selector=P('string', 'DKIM selector'))
tool('shiplane_mail', 'r', 'GET', '/api/mail', 'The mail server: domains, mailboxes, aliases and health.')
tool('shiplane_mail_dns', 'r', 'GET', '/api/mail/dns', 'The DNS records a mail domain needs (MX, SPF, DKIM, DMARC).', domain=P('string', 'Domain', True))
tool('shiplane_mail_check', 'r', 'GET', '/api/mail/check', 'Check that a mail domain\'s DNS is correct.', domain=P('string', 'Domain', True))
tool('shiplane_mail_account_add', 'w', 'POST', '/api/mail/account/add', 'Create a mailbox.', email=P('string', 'Address', True), password=P('string', 'Password', True), name=P('string', 'Display name'), quota_mb=P('integer', 'Quota in MB'))
tool('shiplane_mail_account_update', 'd', 'POST', '/api/mail/account/update', 'Change or delete a mailbox (op: password, quota, disable, enable, delete).', email=P('string', 'Address', True), op=P('string', 'Operation', True), confirm=P('string', 'The address, for delete'), delete_mail=P('boolean', 'Also delete the stored mail'))
tool('shiplane_mail_alias_add', 'w', 'POST', '/api/mail/alias/add', 'Create an alias that forwards to one or more addresses.', source=P('string', 'Alias address', True), dests=P('array', 'Destination addresses', True, items='string'))
tool('shiplane_mail_alias_remove', 'd', 'POST', '/api/mail/alias/remove', 'Remove an alias.', source=P('string', 'Alias address', True), confirm=CONFIRM)

# ---- files
tool('shiplane_files_list', 'r', 'GET', '/api/files/list', 'List a folder on the server (admins reach /var/www, /srv, /home). Protected folders carry a `lock` reason.', path=P('string', 'Folder path, "/" for the roots', True))
tool('shiplane_files_read', 'r', 'GET', '/api/files/read', 'Read a text file (up to 2 MB) with its modified time.', path=P('string', 'File path', True))
tool('shiplane_files_write', 'w', 'POST', '/api/files/write', 'Write a text file. Pass expect_mtime from shiplane_files_read to avoid overwriting someone else\'s change.', path=P('string', 'File path', True), content=P('string', 'New content', True),
     create=P('boolean', 'Create a new file'), expect_mtime=P('integer', 'Modified time you read'), force=P('boolean', 'Overwrite despite a conflict'))
tool('shiplane_files_mkdir', 'w', 'POST', '/api/files/mkdir', 'Create a folder.', path=P('string', 'Folder path', True))
tool('shiplane_files_rename', 'w', 'POST', '/api/files/rename', 'Rename or move a file or folder. Folders of live sites, account homes and folders in use are refused.', path=P('string', 'Current path', True), name=P('string', 'New name (same folder)'), new_path=P('string', 'New full path (move)'))
tool('shiplane_files_delete', 'd', 'POST', '/api/files/delete', 'Delete a file or folder. Protected folders (live sites, account homes, /etc folders, folders in use) are refused by the server.', path=P('string', 'Path', True), recursive=P('boolean', 'Delete a non-empty folder and everything in it'), confirm=CONFIRM)

# ---- migration
tool('shiplane_migrate_inspect', 'w', 'POST', '/api/migrate/inspect', 'Read a cPanel/Plesk backup on the server (path) or a live account (host) and list its sites and databases.', path=P('string', 'Backup path on the server'), host=P('string', 'Live account host'))
tool('shiplane_migrate_start', 'w', 'POST', '/api/migrate/start', 'Start migrating the chosen sites and databases. Follow with shiplane_migrate_run.', path=P('string', 'Backup path'), host=P('string', 'Live host'), sites=P('array', 'Site names', items='string'), databases=P('array', 'Database names', items='string'))
tool('shiplane_migrate_run', 'r', 'GET', '/api/migrate/run', 'Progress of a migration run.', id=P('string', 'Run id', True), since=P('integer', 'Step to start from'))
tool('shiplane_migrate_active', 'r', 'GET', '/api/migrate/active', 'Is a migration running right now.')


# --------------------------------------------------------------------------- composite tools
def local_zip(folder):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, folder))
    return buf.getvalue()


DEPLOY_FIELDS = ('type', 'hostname', 'port', 'dll', 'start', 'build', 'cport', 'dockerfile', 'postgresDb', 'redirectFrom', 'healthPath', 'healthStatus', 'runtime')


def deploy_folder(a):
    folder, site = os.path.expanduser(str(a.get('folder') or '')), str(a.get('site') or '')
    if not os.path.isdir(folder) or not site:
        raise ApiError('folder must be an existing folder on the machine running this MCP server, and site is required')
    app = {'name': site, 'mapCf': False}
    known = next((s for s in call('GET', '/api/status').get('sites', []) if s.get('name') == site), None)
    if known:
        app.update({k: v for k, v in (known.get('meta') or {}).items() if k in DEPLOY_FIELDS})
    app.update({k: a[k] for k in DEPLOY_FIELDS if a.get(k) not in (None, '')})
    if not app.get('type') or not app.get('hostname'):
        raise ApiError('This site does not exist yet: pass type and hostname for the first deploy')
    app['source'] = call('POST', '/api/upload', raw=local_zip(folder), timeout=600)['path']
    job = call('POST', '/api/deploy', body=app)['job']
    return job_result(job, a)


def job_result(job, a):
    if a.get('wait') is False:
        return {'job': job, 'note': 'started; follow it with shiplane_job'}
    done, ok, log = wait_job(job)
    return {'job': job, 'done': done, 'ok': ok, 'log': log[-6000:], **({} if done else {'note': 'still running: follow it with shiplane_job'})}


def generic_api(a):
    method = str(a.get('method') or 'GET').upper()
    if method not in ('GET', 'POST'):
        raise ApiError('method must be GET or POST')
    path = str(a.get('path') or '')
    if method == 'POST' and READONLY:
        raise ApiError('read-only mode (SHIPLANE_MCP_READONLY is set): POST is disabled')
    if path.startswith('/api/terminal'):
        raise ApiError('The Terminal is browser-only (super admins) and is not available through API keys')
    return call(method, path, query=a.get('query'), body=a.get('body') if method == 'POST' else None)


def routes(_a):
    return call('GET', '/api/routes')


COMPOSITE = [
    {'name': 'shiplane_deploy_folder', 'kind': 'w', 'desc': 'Deploy a folder from the machine running this MCP server to a site: zips it, uploads it, starts the deploy and returns the log (rolls back automatically if the health check fails). '
     'An existing site keeps its saved settings; a first deploy needs type and hostname.', 'fn': deploy_folder,
     'props': {'folder': P('string', 'Folder to deploy (local path)', True), 'site': P('string', 'Site name', True), 'type': P('string', 'static / node / dotnet / php / command / docker (first deploy)'),
               'hostname': P('string', 'Public address (first deploy)'), 'port': P('integer', 'App port'), 'start': P('string', 'Node start file or command'), 'build': P('string', 'Build command'), 'dll': P('string', '.NET dll'),
               'healthPath': P('string', 'Health check path'), 'healthStatus': P('integer', 'Expected status'), 'runtime': P('string', 'Runtime version'), 'postgresDb': P('boolean', 'Create a Postgres database'),
               'wait': P('boolean', 'Wait for the deploy to finish (default true)')}},
    {'name': 'shiplane_routes', 'kind': 'r', 'desc': 'List every API route of this Shiplane and the role it needs. Use it to find routes that have no dedicated tool, then call them with shiplane_api.', 'fn': routes, 'props': {}},
    {'name': 'shiplane_api', 'kind': 'w', 'desc': 'Call any Shiplane API route directly (GET or POST). For things without a dedicated tool; check shiplane_routes first. The Terminal is not reachable this way.', 'fn': generic_api,
     'props': {'method': P('string', 'GET or POST', True, ['GET', 'POST']), 'path': P('string', 'Route path starting with /api/', True), 'query': P('object', 'Query string parameters'), 'body': P('object', 'JSON body for POST')}},
]


def schema(props):
    s = {'type': 'object', 'properties': {}, 'required': []}
    for k, p in props.items():
        d = {'type': p['type'], 'description': p['desc']}
        if p['enum']:
            d['enum'] = p['enum']
        if p['type'] == 'array':
            d['items'] = {'type': p['items'] or 'string'}
        s['properties'][k] = d
        if p['req']:
            s['required'].append(k)
    if not s['required']:
        del s['required']
    return s


def visible():
    for t in TOOLS + COMPOSITE:
        if READONLY and t['kind'] != 'r':
            continue
        yield t


def listing():
    out = []
    for t in visible():
        ann = {'readOnlyHint': t['kind'] == 'r', 'destructiveHint': t['kind'] == 'd', 'openWorldHint': False}
        if t['name'] == 'shiplane_api':
            ann['destructiveHint'] = True
        out.append({'name': t['name'], 'description': t['desc'], 'inputSchema': schema(t['props']), 'annotations': ann})
    return out


def run_tool(name, a):
    t = next((t for t in visible() if t['name'] == name), None)
    if not t:
        raise ApiError('unknown tool (or hidden by read-only mode): %s' % name)
    a = a or {}
    for k, p in t['props'].items():
        if p['req'] and a.get(k) in (None, ''):
            raise ApiError('missing required argument: %s' % k)
    if t['kind'] == 'd' and not a.get('confirm'):
        raise ApiError('%s deletes or overwrites data: pass confirm (true, or the exact name it asks for) to proceed' % name)
    if 'fn' in t:
        return t['fn'](a)
    wait = a.get('wait')
    a = {k: v for k, v in a.items() if k in t['props']}
    if t['method'] == 'GET':
        return call('GET', t['path'], query=a)
    a.update(t.get('fixed') or {})
    if name == 'shiplane_site_delete':
        if a.get('confirm') != a.get('name'):
            raise ApiError('confirm must be exactly the site name (%s)' % a.get('name'))
        a['dropDb'] = bool(a.get('dropDb') or a.get('purge'))
    r = call('POST', t['path'], body=a, timeout=300)
    if isinstance(r, dict) and isinstance(r.get('job'), str):          # deploy, backup, upgrade, promote...: follow the job so the answer holds the log
        return job_result(r['job'], {'wait': wait})
    return r


# --------------------------------------------------------------------------- MCP (JSON-RPC over stdio)
def send(msg):
    sys.stdout.write(json.dumps(msg, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def handle(m):
    method, mid, params = m.get('method'), m.get('id'), m.get('params') or {}
    if mid is None:
        return                                  # notification (initialized, cancelled...): nothing to answer
    try:
        if method == 'initialize':
            want = params.get('protocolVersion')
            result = {'protocolVersion': want if want in PROTOCOLS else PROTOCOLS[0], 'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'shiplane', 'version': VERSION}, 'instructions': INSTRUCTIONS + (' READ-ONLY mode: nothing can be changed.' if READONLY else '')}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': listing()}
        elif method == 'tools/call':
            try:
                out = run_tool(params.get('name'), params.get('arguments'))
                text = out if isinstance(out, str) else json.dumps(out, indent=1, ensure_ascii=False)
                result = {'content': [{'type': 'text', 'text': text[:200000]}], 'isError': isinstance(out, dict) and out.get('ok') is False and 'log' in out}
            except ApiError as e:
                result = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
        else:
            return send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'method not found: %s' % method}})
        send({'jsonrpc': '2.0', 'id': mid, 'result': result})
    except Exception as e:                      # a bug must not kill the connection
        send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': 'internal error: %s' % e}})


def main():
    if '--version' in sys.argv:
        print(VERSION)
        return
    if '--list-tools' in sys.argv:
        for t in visible():
            print('%s  [%s]' % (t['name'], t['kind']))
        return
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            send({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}})
            continue
        if isinstance(m, dict):
            handle(m)


if __name__ == '__main__':
    main()
