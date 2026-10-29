"""Nightly Postgres backups with retention, and restore. Backups stay on the server in /var/backups/coderunner."""
import datetime
import threading
import time

import json
import re

from . import activity, alerts, config, dbengines, dbops, deploy, settings
from .runner import run, script

DEFAULTS = {'enabled': False, 'hour': 3, 'keep': 14, 'verify': False, 'last': None}   # hour is UTC


def get():
    c = dict(DEFAULTS)
    c.update(settings.get('backups'))
    return c


def save(enabled, hour, keep, verify=None):
    hour, keep = int(hour), int(keep)
    if not 0 <= hour <= 23:
        raise ValueError('Hour must be 0 to 23')
    if not 1 <= keep <= 90:
        raise ValueError('Keep between 1 and 90 backups per database')
    c = get()
    c.update(enabled=bool(enabled), hour=hour, keep=keep)
    if verify is not None:
        c['verify'] = bool(verify)
    settings.put('backups', c)
    return c


# ---- off-server copy: any S3-compatible bucket (Cloudflare R2, AWS S3, Backblaze B2 ...). The server uploads straight from its own disk;
# the secret key is kept in the owner-only settings file, sent to the server over SSH only while uploading, and never shown again.
OFFSITE_RULES = {'endpoint': r'https://[A-Za-z0-9.-]+(:\d+)?', 'bucket': r'[a-z0-9][a-z0-9.-]{1,61}', 'region': r'[a-z0-9-]{2,30}',
                 'access_key': r'[A-Za-z0-9/+=_.-]{8,200}', 'prefix': r'[A-Za-z0-9/_.-]{0,100}'}


def _offsite():
    return settings.get('backups_offsite')


def offsite_public():
    o = _offsite()
    return {'configured': bool(o.get('bucket')), 'endpoint': o.get('endpoint', ''), 'bucket': o.get('bucket', ''), 'region': o.get('region', 'auto'),
            'access_key': o.get('access_key', ''), 'prefix': o.get('prefix', ''), 'has_secret': bool(o.get('secret_key'))}


def save_offsite(v):
    """Save the bucket details. An empty bucket removes the setting; an empty secret keeps the saved one."""
    old = _offsite()
    if not str(v.get('bucket') or '').strip():
        settings.put('backups_offsite', {})
        return offsite_public()
    new = {k: str(v.get(k) if v.get(k) is not None else '').strip() for k in OFFSITE_RULES}
    new['region'] = new['region'] or 'auto'
    for k, rule in OFFSITE_RULES.items():
        if not re.fullmatch(rule, new[k]):
            raise ValueError('%s looks wrong' % k.replace('_', ' '))
    secret = str(v.get('secret_key') or '').strip() or old.get('secret_key', '')
    if not re.fullmatch(r'[A-Za-z0-9/+=_.-]{16,200}', secret):
        raise ValueError('Secret key is required (it is saved on this computer and never shown again)')
    new['secret_key'] = secret
    settings.put('backups_offsite', new)
    return offsite_public()


def _upload(cfg, path, key):
    o = _offsite()
    pre = o['prefix'].strip('/')
    conf = dict({k: o[k] for k in list(OFFSITE_RULES) + ['secret_key']}, key=(pre + '/' if pre else '') + key)
    text = script('s3put.py').replace('__CONF__', repr(json.dumps(conf))).replace('__FILE__', repr(path))
    rc, out = run(cfg, 'sudo python3 -', inp=text.encode(), timeout=900)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'upload failed')


def test_offsite(cfg):
    """Upload a tiny file to prove the bucket and keys work."""
    if not _offsite().get('bucket'):
        return False, 'Off-server copy is not set up'
    tmp = ''
    try:
        rc, out = run(cfg, "f=$(mktemp) && echo 'shiplane test' > $f && echo $f")
        tmp = out.strip()
        _upload(cfg, tmp, 'shiplane-test.txt')
        return True, 'Test file shiplane-test.txt uploaded to the bucket'
    except Exception as e:
        return False, str(e)
    finally:
        if re.fullmatch(r'/tmp/tmp\.[A-Za-z0-9]+', tmp):
            run(cfg, 'rm -f ' + tmp)


def prune(cfg, keep, lines=None):
    """Delete the oldest backups of each database beyond `keep` (manual ones count too)."""
    by = {}
    for b in dbops.list_all(cfg)['backups']:
        by.setdefault((b['file'].rsplit('-', 2)[0], dbops.engine_of(b['file'])), []).append(b['file'])     # a Postgres and a MySQL database of the same name are kept apart
    removed = 0
    for files in by.values():
        for f in sorted(files, reverse=True)[keep:]:
            dbops.delete_backup(cfg, f)
            removed += 1
            if lines is not None:
                lines.append('  removed old backup ' + f)
    return removed


def run_all(cfg, say=print):
    """Back up every database except the system ones, then prune. Returns (ok, summary)."""
    c = get()
    names = [(d['name'], 'postgres') for d in dbops.list_all(cfg)['databases'] if d['name'] not in dbops.PROTECTED and config.DB_RE.match(d['name'])]
    try:
        my = dbengines.mysql_state(cfg)
        names += [(d['name'], 'mysql') for d in my['databases'] if my['running'] and d['name'] not in dbengines.MYSQL_PROTECTED and config.DB_RE.match(d['name'])]
    except Exception:
        pass     # MySQL is optional: a problem reading it must not stop the Postgres backups
    bad = []
    for n, engine in names:
        say('Backing up %s%s ...' % (n, ' (MySQL)' if engine == 'mysql' else ''))
        try:
            r = dbops.backup(cfg, n, engine)
            say('  %s (%d bytes)' % (r['file'], r['size']))
            if _offsite().get('bucket'):
                _upload(cfg, dbops.backup_path(r['file']), r['file'])
                say('  copied off the server')
            if c['verify']:
                say('  test restore: %d table(s) came back' % dbops.verify(cfg, r['file'])['tables'])
        except Exception as e:
            bad.append(n)
            say('  FAILED: %s' % e)
    removed = prune(cfg, c['keep'], None)
    if removed:
        say('Removed %d old backup(s), keeping %d per database' % (removed, c['keep']))
    ok = not bad
    summary = '%d database(s) backed up' % (len(names) - len(bad)) + ('' if ok else ', FAILED: ' + ', '.join(bad))
    c = get()
    c['last'] = {'day': (c['last'] or {}).get('day'), 't': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()), 'ok': ok, 'summary': summary}
    settings.put('backups', c)
    return ok, summary


def _job(job, cfg, who):
    ok, summary = run_all(cfg, job.say)
    activity.log('backup-run', '', summary, who, ok)
    if not ok:
        alerts.notify('[Shiplane] Backup problem: ' + summary)
    job.ok = ok
    job.say('DONE: ' + summary if ok else 'FAILED: ' + summary)


def start_run(cfg, who):
    return deploy.start_job(_job, cfg, who)


def _verify_job(job, cfg, who, file):
    job.say('Loading %s into a scratch database (the live data is not touched) ...' % file)
    try:
        r = dbops.verify(cfg, file)
    except Exception as e:
        activity.log('backup-verify', file, str(e)[:200], who, False)
        alerts.notify('[Shiplane] Backup %s could NOT be restored: %s' % (file, e))
        job.say('FAILED: %s' % e)
        return
    activity.log('backup-verify', file, '%d tables' % r['tables'], who, True)
    job.ok = True
    job.say('DONE: %s restores correctly (%d tables).' % (file, r['tables']))


def start_verify(cfg, who, file):
    dbops.backup_path(file)
    return deploy.start_job(_verify_job, cfg, who, file)


def _restore_job(job, cfg, who, name, file):
    job.say('Taking a safety backup of %s first ...' % name)
    safe = dbops.backup(cfg, name, dbops.engine_of(file))
    job.say('  saved as %s' % safe['file'])
    job.say('Restoring %s from %s ...' % (name, file))
    out = dbops.restore(cfg, name, file)
    for line in out.strip().splitlines():
        job.say('  ' + line)
    activity.log('db-restore', name, file, who, True)
    job.ok = True
    job.say('DONE: %s restored. The previous data is in %s' % (name, safe['file']))


def start_restore(cfg, who, name, file, confirm):
    dbops._name(name)
    dbops.backup_path(file)
    if not file.startswith(name + '-'):
        raise ValueError('That backup belongs to a different database')
    if name in dbops.PROTECTED:
        raise ValueError('That database is protected')
    if confirm != name:
        raise ValueError('Type the database name to confirm')
    return deploy.start_job(_restore_job, cfg, who, name, file)


def tick(cfg, now=None):
    """Run the nightly backup when the hour has come and today's has not run yet. True if it ran."""
    c = get()
    now = now or datetime.datetime.utcnow()
    today = now.strftime('%Y-%m-%d')
    if not c['enabled'] or now.hour != c['hour'] or (c['last'] or {}).get('day') == today:
        return False
    c['last'] = {'day': today, 't': '', 'ok': None, 'summary': 'running'}   # marked first so a slow run is not started twice
    settings.put('backups', c)
    ok, summary = run_all(cfg)
    c = get()
    c['last']['day'] = today
    settings.put('backups', c)
    activity.log('backup-scheduled', '', summary, 'scheduler', ok)
    if not ok:
        alerts.notify('[Shiplane] Nightly backup problem: ' + summary)
    return True


def start(load_cfg):
    def loop():
        while True:
            try:
                tick(load_cfg())
            except Exception as e:
                activity.log('backup-scheduled', '', str(e)[:200], 'scheduler', False)
            time.sleep(60)

    threading.Thread(target=loop, daemon=True).start()
