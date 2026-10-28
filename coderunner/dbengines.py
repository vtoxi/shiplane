"""MySQL / MariaDB and SQL Server databases, next to the Postgres ones in dbops.py: list, create (with its own login) and drop.

MySQL and MariaDB are reached as the server's root through the local socket. SQL Server runs in a Docker container, so its administrator (sa)
password is needed for every change: it is typed in the screen, sent to the server inside the script (never in a command line) and never stored.
"""
import re
import secrets
from urllib.parse import quote

from . import config
from .runner import q, run
from .serverops import CONTAINER_RE

MYSQL_PROTECTED = {'mysql', 'information_schema', 'performance_schema', 'sys'}
SA_RE = re.compile(r'^[\x21-\x7e]{8,128}$')


def _name(name):
    if not config.DB_RE.match(str(name)):
        raise ValueError('Database name: start with a letter, then a-z, 0-9 and _ only')
    return str(name)


def _fail(out, default):
    raise RuntimeError(out.strip()[-300:] or default)


# ------------------------------------------------------------------- MySQL / MariaDB
def mysql_state(cfg):
    """{'installed': bool, 'running': bool, 'databases': [{name, size}]}. Never raises for "not installed": that is an answer."""
    rc, out = run(cfg, 'if command -v mariadb >/dev/null 2>&1 || command -v mysql >/dev/null 2>&1; then echo INSTALLED; fi; '
                       'for s in mariadb mysql; do systemctl is-active --quiet $s 2>/dev/null && echo RUNNING; done; true')
    state = {'installed': 'INSTALLED' in out, 'running': 'RUNNING' in out, 'databases': []}
    if not (state['installed'] and state['running']):
        return state
    rc, out = run(cfg, 'sudo mysql -N -B -e "SELECT s.schema_name, IFNULL(SUM(t.data_length + t.index_length), 0) FROM information_schema.schemata s '
                       'LEFT JOIN information_schema.tables t ON t.table_schema = s.schema_name '
                       "WHERE s.schema_name NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY s.schema_name ORDER BY 1\"")
    if rc:
        _fail(out, 'could not list the MySQL databases')
    for line in out.strip().splitlines():
        p = line.split('\t')
        if len(p) == 2 and p[1].split('.')[0].isdigit():
            state['databases'].append({'name': p[0], 'size': int(p[1].split('.')[0])})
    return state


def _mysql_ready(cfg):
    st = mysql_state(cfg)
    if not st['installed']:
        raise ValueError('MySQL / MariaDB is not installed on the server: install MariaDB from the Apps page first')
    if not st['running']:
        raise ValueError('MariaDB is installed but stopped: start it on the Server page first')
    return st


def mysql_create(cfg, name, user=None, password=None):
    name = _name(name)
    user, password = config.db_login(name, user, password)
    if name in MYSQL_PROTECTED:
        raise ValueError('That name is reserved by MySQL')
    st = _mysql_ready(cfg)
    if any(d['name'] == name for d in st['databases']):
        raise ValueError('Database %s already exists' % name)
    pw = password or secrets.token_hex(16)
    sql = ("CREATE DATABASE `%(n)s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS '%(u)s'@'localhost' IDENTIFIED BY '%(p)s'; "
           "ALTER USER '%(u)s'@'localhost' IDENTIFIED BY '%(p)s'; GRANT ALL PRIVILEGES ON `%(n)s`.* TO '%(u)s'@'localhost'; FLUSH PRIVILEGES;") % {'n': name, 'u': user, 'p': pw}
    rc, out = run(cfg, 'sudo mysql', inp=sql.encode())
    if rc:
        _fail(out, 'could not create the database')
    return {'name': name, 'user': user, 'password': pw, 'url': 'mysql://%s:%s@127.0.0.1:3306/%s' % (user, quote(pw, safe=''), name)}


def mysql_drop(cfg, name, confirm):
    name = _name(name)
    if name in MYSQL_PROTECTED:
        raise ValueError('That database is protected')
    if confirm != name:
        raise ValueError('Type the database name to confirm')
    _mysql_ready(cfg)
    rc, out = run(cfg, 'sudo mysql', inp=("DROP DATABASE `%(n)s`; DROP USER IF EXISTS '%(n)s'@'localhost';" % {'n': name}).encode())
    if rc:
        _fail(out, 'could not drop the database')
    return 'dropped %s' % name


# ----------------------------------------------------------------------- SQL Server
def mssql_containers(cfg):
    """SQL Server containers on the server: [{name, image, status}]."""
    rc, out = run(cfg, "sudo docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null | grep -i mssql; true")
    found = []
    for line in out.strip().splitlines():
        p = line.split('|')
        if len(p) == 3 and CONTAINER_RE.match(p[0]):
            found.append({'name': p[0], 'image': p[1], 'status': p[2], 'running': p[2].startswith('Up')})
    return found


def _sql_script(container, sa_password, sql):
    if not CONTAINER_RE.match(str(container)):
        raise ValueError('Pick the SQL Server container')
    if not SA_RE.match(str(sa_password or '')):
        raise ValueError('Type the SQL Server administrator (sa) password')
    mark = 'SQL_' + secrets.token_hex(6).upper()
    return '''set -euo pipefail
C=%s
SAPW=%s
S=/opt/mssql-tools18/bin/sqlcmd; FLAGS="-C"
sudo docker exec "$C" test -x "$S" || { S=/opt/mssql-tools/bin/sqlcmd; FLAGS=""; }
sudo docker exec "$C" test -x "$S" || { echo "sqlcmd was not found in the container"; exit 1; }
sudo docker exec -i -e SQLCMDPASSWORD="$SAPW" "$C" "$S" -S localhost -U sa $FLAGS -b -i /dev/stdin <<'%s'
%s
%s
echo DONE
''' % (q(container), q(sa_password), mark, sql, mark)


def _run_sql(cfg, container, sa_password, sql):
    rc, out = run(cfg, 'bash -s', inp=_sql_script(container, sa_password, sql).encode(), timeout=120)
    if rc or 'DONE' not in out:
        text = out.replace(sa_password, '***')
        if 'Login failed' in text:
            raise ValueError('SQL Server refused the sa password')
        _fail(text, 'SQL Server did not accept the command')
    return out


def mssql_create(cfg, container, sa_password, name, user=None, password=None):
    name = _name(name)
    user, password = config.db_login(name, user, password)
    pw = password or 'Sh1p-' + secrets.token_hex(12)     # meets SQL Server's complexity rule: upper, lower, digit, symbol
    sql = ("CREATE DATABASE [%(n)s];\nGO\nCREATE LOGIN [%(u)s] WITH PASSWORD = '%(p)s', CHECK_POLICY = OFF;\nGO\nUSE [%(n)s];\nGO\n"
           "CREATE USER [%(u)s] FOR LOGIN [%(u)s];\nGO\nALTER ROLE db_owner ADD MEMBER [%(u)s];\nGO") % {'n': name, 'u': user, 'p': pw}
    _run_sql(cfg, container, sa_password, sql)
    return {'name': name, 'user': user, 'password': pw, 'url': 'Server=127.0.0.1,1433;Database=%s;User Id=%s;Password=%s;TrustServerCertificate=True' % (name, user, pw)}


def mssql_drop(cfg, container, sa_password, name, confirm):
    name = _name(name)
    if name in ('master', 'model', 'msdb', 'tempdb'):
        raise ValueError('That database is protected')
    if confirm != name:
        raise ValueError('Type the database name to confirm')
    _run_sql(cfg, container, sa_password, ("ALTER DATABASE [%(n)s] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;\nGO\nDROP DATABASE [%(n)s];\nGO\n"
                                           "IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = '%(n)s') DROP LOGIN [%(n)s];\nGO") % {'n': name})
    return 'dropped %s' % name
