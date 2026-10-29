"""Postgres databases: list, create (with its own login), back up to the server, download, drop."""
import re
from urllib.parse import quote

from . import config
from .runner import q, run

BACKUP_DIR = '/var/backups/coderunner'
BACKUP_RE = re.compile(r'^[a-z][a-z0-9_]{0,40}-\d{8}-\d{6}(\.my)?\.sql\.gz$')      # `.my` marks a MySQL / MariaDB dump
PROTECTED = {'postgres', 'template0', 'template1'}


def _name(name):
    if not config.DB_RE.match(str(name)):
        raise ValueError('Database name: start with a letter, then a-z, 0-9 and _ only')
    return name


def list_all(cfg):
    rc, out = run(cfg, "cd /tmp && sudo -u postgres psql -tAF'|' -c \"select d.datname, pg_get_userbyid(d.datdba), pg_database_size(d.datname) from pg_database d where not d.datistemplate order by 1\"")
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'could not list databases')
    dbs = []
    for line in out.strip().splitlines():
        p = line.split('|')
        if len(p) == 3 and p[2].isdigit():
            dbs.append({'name': p[0], 'owner': p[1], 'size': int(p[2])})
    rc, out = run(cfg, "sudo find %s -maxdepth 1 -name '*.sql.gz' -printf '%%f|%%s|%%TY-%%Tm-%%Td %%TH:%%TM\\n' 2>/dev/null | sort -r" % BACKUP_DIR)
    backups = []
    for line in out.strip().splitlines():
        p = line.split('|')
        if len(p) == 3 and BACKUP_RE.match(p[0]):
            backups.append({'file': p[0], 'size': int(p[1]), 'time': p[2]})
    return {'databases': dbs, 'backups': backups}


def create(cfg, name, user=None, password=None):
    _name(name)
    user, password = config.db_login(name, user, password)
    script = '''set -euo pipefail
cd /tmp
N=%s
U=%s
PW=%s
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$N'" </dev/null | grep -q 1; then echo "database $N already exists"; exit 1; fi
[ -n "$PW" ] || PW=$(openssl rand -hex 16)
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$U'" </dev/null | grep -q 1; then
  sudo -u postgres psql -qc "ALTER ROLE $U PASSWORD '$PW'" </dev/null
else
  sudo -u postgres psql -qc "CREATE ROLE $U LOGIN PASSWORD '$PW'" </dev/null
fi
sudo -u postgres createdb -O $U $N </dev/null
echo "PASSWORD=$PW"
''' % (q(name), q(user), q(password or ''))
    rc, out = run(cfg, 'bash -s', inp=script.encode())
    m = re.search(r'^PASSWORD=(\S+)$', out, re.M)
    if rc or not m:
        raise RuntimeError(out.strip()[-300:] or 'could not create the database')
    pw = m.group(1)
    return {'name': name, 'user': user, 'password': pw, 'url': 'postgresql://%s:%s@127.0.0.1:5432/%s' % (user, quote(pw, safe=''), name)}


def engine_of(file):
    return 'mysql' if str(file).endswith('.my.sql.gz') else 'postgres'


def backup(cfg, name, engine='postgres'):
    _name(name)
    if engine == 'mysql':
        return _mysql_backup(cfg, name)
    script = '''set -euo pipefail
N=%s
sudo install -d -m 700 %s
F=%s/$N-$(date +%%Y%%m%%d-%%H%%M%%S).sql.gz
cd /tmp
sudo -u postgres pg_dump "$N" | gzip | sudo tee "$F" >/dev/null
sudo chmod 600 "$F"
echo "FILE=$(basename "$F") SIZE=$(sudo stat -c %%s "$F")"
''' % (q(name), BACKUP_DIR, BACKUP_DIR)
    rc, out = run(cfg, 'bash -s', inp=script.encode(), timeout=600)
    m = re.search(r'FILE=(\S+) SIZE=(\d+)', out)
    if rc or not m:
        raise RuntimeError(out.strip()[-300:] or 'backup failed')
    return {'file': m.group(1), 'size': int(m.group(2))}


def _mysql_backup(cfg, name):
    script = '''set -euo pipefail
N=%s
sudo install -d -m 700 %s
F=%s/$N-$(date +%%Y%%m%%d-%%H%%M%%S).my.sql.gz
cd /tmp
sudo mysqldump --single-transaction --routines --triggers --events --default-character-set=utf8mb4 "$N" | gzip | sudo tee "$F" >/dev/null
sudo chmod 600 "$F"
echo "FILE=$(basename "$F") SIZE=$(sudo stat -c %%s "$F")"
''' % (q(name), BACKUP_DIR, BACKUP_DIR)
    rc, out = run(cfg, 'bash -s', inp=script.encode(), timeout=600)
    m = re.search(r'FILE=(\S+) SIZE=(\d+)', out)
    if rc or not m:
        raise RuntimeError(out.strip()[-300:] or 'backup failed')
    return {'file': m.group(1), 'size': int(m.group(2))}


def _mysql_restore(cfg, name, file):
    """MySQL cannot rename a database, so the dump is first proven on a scratch database; only then is the real one dropped, recreated and loaded.
    The job takes a safety backup beforehand, which is the way back if the second load were to fail."""
    script = '''set -uo pipefail
cd /tmp
N=%s; F=%s; T=${N}_restore
my() { sudo mysql "$@" </dev/null; }
sudo test -f "$F" || { echo "backup file not found"; exit 1; }
my -e "SELECT 1 FROM information_schema.schemata WHERE schema_name='$N'" | grep -q 1 || { echo "database $N does not exist"; exit 1; }
my -e "DROP DATABASE IF EXISTS \\`$T\\`"
my -e "CREATE DATABASE \\`$T\\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
if ! sudo gunzip -c "$F" | sudo mysql "$T"; then
  echo "the backup could not be loaded; $N is unchanged"; my -e "DROP DATABASE IF EXISTS \\`$T\\`"; exit 1
fi
my -e "DROP DATABASE \\`$T\\`"
my -e "DROP DATABASE \\`$N\\`" && my -e "CREATE DATABASE \\`$N\\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
if ! sudo gunzip -c "$F" | sudo mysql "$N"; then
  echo "loading into $N failed after it was emptied: restore the safety backup taken just before"; exit 2
fi
echo "restored $N from $(basename "$F")"
''' % (q(name), q(backup_path(file)))
    rc, out = run(cfg, 'bash -s', inp=script.encode(), timeout=900)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'restore failed')
    return out


def restore(cfg, name, file):
    """Load a backup into a new database, then swap it in, so a bad dump leaves the current data untouched."""
    _name(name)
    if engine_of(file) == 'mysql':
        return _mysql_restore(cfg, name, file)
    script = '''set -euo pipefail
cd /tmp
N=%s; F=%s; T=${N}_restore; OLD=${N}_old
pg() { sudo -u postgres psql -q "$@" </dev/null; }
kick() { pg -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1' AND pid<>pg_backend_pid()" >/dev/null; }
OWNER=$(sudo -u postgres psql -tAc "select pg_get_userbyid(datdba) from pg_database where datname='$N'" </dev/null)
[ -n "$OWNER" ] || { echo "database $N does not exist"; exit 1; }
sudo test -f "$F" || { echo "backup file not found"; exit 1; }
pg -c "DROP DATABASE IF EXISTS $T"; pg -c "DROP DATABASE IF EXISTS $OLD"
sudo -u postgres createdb -O "$OWNER" "$T" </dev/null
if ! sudo gunzip -c "$F" | sudo -u postgres psql -q -v ON_ERROR_STOP=1 "$T" >/dev/null; then
  echo "the backup could not be loaded; $N is unchanged"; pg -c "DROP DATABASE IF EXISTS $T"; exit 1
fi
for i in 1 2 3 4 5; do kick $N; kick $T; pg -c "ALTER DATABASE $N RENAME TO $OLD" && break || sleep 1; done
if ! sudo -u postgres psql -tAc "select 1 from pg_database where datname='$OLD'" </dev/null | grep -q 1; then
  echo "the database stayed busy, nothing was swapped; $N is unchanged"; pg -c "DROP DATABASE IF EXISTS $T"; exit 1
fi
pg -c "ALTER DATABASE $T RENAME TO $N"
pg -c "DROP DATABASE $OLD"
echo "restored $N from $(basename "$F")"
''' % (q(name), q(backup_path(file)))
    rc, out = run(cfg, 'bash -s', inp=script.encode(), timeout=900)
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'restore failed')
    return out


MYSQL_VERIFY = '''set -uo pipefail
cd /tmp
F=%s
T=shiplane_verify_$(openssl rand -hex 4)
sudo test -f "$F" || { echo "backup file not found"; exit 1; }
cleanup() { sudo mysql -e "DROP DATABASE IF EXISTS \\`$T\\`" </dev/null >/dev/null 2>&1; }
trap cleanup EXIT
sudo mysql -e "CREATE DATABASE \\`$T\\`" </dev/null || { echo "could not create a scratch database"; exit 1; }
if ! sudo gunzip -c "$F" | sudo mysql "$T" 2>/tmp/shiplane-verify.err; then
  echo "NOT RESTORABLE: $(tail -n 2 /tmp/shiplane-verify.err | tr '\\n' ' ')"; rm -f /tmp/shiplane-verify.err; exit 1
fi
rm -f /tmp/shiplane-verify.err
echo "TABLES=$(sudo mysql -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$T'" </dev/null)"
'''


def verify(cfg, file):
    """Prove a backup can be restored: load it into a throwaway database, count what came back, drop the throwaway. The live database is never touched."""
    script = (MYSQL_VERIFY if engine_of(file) == 'mysql' else '''set -uo pipefail
cd /tmp
F=%s
T=shiplane_verify_$(openssl rand -hex 4)
sudo test -f "$F" || { echo "backup file not found"; exit 1; }
cleanup() { sudo -u postgres psql -qc "DROP DATABASE IF EXISTS $T" </dev/null >/dev/null 2>&1; }
trap cleanup EXIT
sudo -u postgres createdb "$T" </dev/null || { echo "could not create a scratch database"; exit 1; }
if ! sudo gunzip -c "$F" | sudo -u postgres psql -q -v ON_ERROR_STOP=1 "$T" >/dev/null 2>/tmp/shiplane-verify.err; then
  echo "NOT RESTORABLE: $(tail -n 2 /tmp/shiplane-verify.err | tr '\\n' ' ')"; rm -f /tmp/shiplane-verify.err; exit 1
fi
rm -f /tmp/shiplane-verify.err
echo "TABLES=$(sudo -u postgres psql -tAc "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')" "$T" </dev/null)"
''') % q(backup_path(file))
    rc, out = run(cfg, 'bash -s', inp=script.encode(), timeout=900)
    m = re.search(r'^TABLES=(\d+)$', out, re.M)
    if rc or not m:
        raise RuntimeError(out.strip()[-300:] or 'could not verify the backup')
    n = int(m.group(1))
    if n == 0:
        raise RuntimeError('The backup loads but contains no tables')
    return {'file': file, 'tables': n}


def drop(cfg, name, confirm):
    _name(name)
    if name in PROTECTED:
        raise ValueError('That database is protected')
    if confirm != name:
        raise ValueError('Type the database name to confirm')
    rc, out = run(cfg, 'cd /tmp && sudo -u postgres psql -qc "DROP DATABASE %s" </dev/null && (sudo -u postgres psql -qc "DROP ROLE IF EXISTS %s" </dev/null; echo dropped %s)' % (name, name, name))
    if rc:
        raise RuntimeError(out.strip()[-300:])
    return out.strip()


def backup_path(file):
    if not BACKUP_RE.match(str(file)):
        raise ValueError('bad backup file name')
    return BACKUP_DIR + '/' + file


def delete_backup(cfg, file):
    rc, out = run(cfg, 'sudo rm -f %s && echo deleted' % q(backup_path(file)))
    if rc:
        raise RuntimeError(out.strip())
    return out.strip()
