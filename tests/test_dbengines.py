"""MySQL / MariaDB and SQL Server databases: validation, the SQL that is sent, and that secrets stay out of command lines and errors (fake server).

    python tests/test_dbengines.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import dbengines as d  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
CALLS = []
ANSWERS = []
SA = 'S3cretpass99'


def fake(cfg, cmd, inp=None, timeout=60):
    CALLS.append((cmd, (inp or b'').decode()))
    return ANSWERS.pop(0) if ANSWERS else (0, '')


def before():
    d.run = fake
    CALLS.clear()
    ANSWERS.clear()


def raises(fn, *a, text='', kind=ValueError):
    try:
        fn(*a)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


UP = (0, 'INSTALLED\nRUNNING\n')


def test_state_reports_missing_stopped_and_running():
    ANSWERS.append((0, ''))
    assert d.mysql_state(CFG) == {'installed': False, 'running': False, 'databases': []} and len(CALLS) == 1
    ANSWERS.append((0, 'INSTALLED\n'))
    assert d.mysql_state(CFG)['running'] is False and len(CALLS) == 2
    ANSWERS.extend([UP, (0, 'shop\t4096\nblog\t0\nbroken\n')])
    assert d.mysql_state(CFG)['databases'] == [{'name': 'shop', 'size': 4096}, {'name': 'blog', 'size': 0}]
    ANSWERS.extend([UP, (1, 'ERROR 1045 denied')])
    raises(d.mysql_state, CFG, text='denied', kind=RuntimeError)
    ANSWERS.extend([UP, (1, '')])
    raises(d.mysql_state, CFG, text='could not list', kind=RuntimeError)


def test_mysql_create_validates_then_sends_one_script():
    raises(d.mysql_create, CFG, 'Bad Name', text='Database name')
    raises(d.mysql_create, CFG, 'mysql', text='reserved')
    ANSWERS.append((0, ''))
    raises(d.mysql_create, CFG, 'shop', text='not installed')
    ANSWERS.append((0, 'INSTALLED\n'))
    raises(d.mysql_create, CFG, 'shop', text='stopped')
    ANSWERS.extend([UP, (0, 'shop\t1\n')])
    raises(d.mysql_create, CFG, 'shop', text='already exists')
    CALLS.clear()
    ANSWERS.extend([UP, (0, ''), (0, '')])
    out = d.mysql_create(CFG, 'reports')
    cmd, sql = CALLS[-1]
    assert cmd == 'sudo mysql' and out['password'] not in cmd and out['password'] in sql
    assert 'CREATE DATABASE `reports`' in sql and "GRANT ALL PRIVILEGES ON `reports`.* TO 'reports'@'localhost'" in sql and len(out['password']) == 32
    assert out['url'] == 'mysql://reports:%s@127.0.0.1:3306/reports' % out['password']
    ANSWERS.extend([UP, (0, ''), (1, 'ERROR 1007')])
    raises(d.mysql_create, CFG, 'other', text='ERROR 1007', kind=RuntimeError)
    ANSWERS.extend([UP, (0, ''), (1, '')])
    raises(d.mysql_create, CFG, 'other', text='could not create', kind=RuntimeError)
    raises(d.mysql_create, CFG, 'other', 'Bad User', text='User name')
    raises(d.mysql_create, CFG, 'other', 'bob', "it's'bad", text='Password')
    ANSWERS.extend([UP, (0, ''), (0, '')])
    out = d.mysql_create(CFG, 'blog', 'bob', 'p@ss#word1')
    assert "TO 'bob'@'localhost'" in CALLS[-1][1] and out['user'] == 'bob' and out['password'] == 'p@ss#word1'
    assert out['url'] == 'mysql://bob:p%40ss%23word1@127.0.0.1:3306/blog'


def test_mysql_drop_is_guarded():
    raises(d.mysql_drop, CFG, 'sys', 'sys', text='protected')
    raises(d.mysql_drop, CFG, 'shop', 'nope', text='Type the database name')
    ANSWERS.extend([UP, (0, 'shop\t1\n'), (0, '')])
    assert d.mysql_drop(CFG, 'shop', 'shop') == 'dropped shop'
    assert 'DROP DATABASE `shop`' in CALLS[-1][1] and "DROP USER IF EXISTS 'shop'@'localhost'" in CALLS[-1][1]
    ANSWERS.extend([UP, (0, ''), (1, 'ERROR 1008')])
    raises(d.mysql_drop, CFG, 'shop', 'shop', text='ERROR 1008', kind=RuntimeError)
    ANSWERS.extend([UP, (0, ''), (1, '')])
    raises(d.mysql_drop, CFG, 'shop', 'shop', text='could not drop', kind=RuntimeError)


def test_sql_server_containers_are_listed():
    ANSWERS.append((0, 'mssql|mcr.microsoft.com/mssql/server:2022|Up 3 minutes\nodd line\nbad name!|x|Up\nold|mcr.microsoft.com/mssql/server|Exited (0) 2 days ago\n'))
    assert d.mssql_containers(CFG) == [{'name': 'mssql', 'image': 'mcr.microsoft.com/mssql/server:2022', 'status': 'Up 3 minutes', 'running': True},
                                       {'name': 'old', 'image': 'mcr.microsoft.com/mssql/server', 'status': 'Exited (0) 2 days ago', 'running': False}]


def test_sql_server_create_keeps_the_sa_password_out_of_the_command_line():
    raises(d.mssql_create, CFG, 'mssql', SA, 'Bad Name', text='Database name')
    raises(d.mssql_create, CFG, 'bad container!', SA, 'shop', text='container')
    raises(d.mssql_create, CFG, 'mssql', 'short', 'shop', text='sa) password')
    assert not CALLS
    ANSWERS.append((0, 'DONE\n'))
    out = d.mssql_create(CFG, 'mssql', "S3cret'pass$word", 'shop')
    cmd, script = CALLS[0]
    assert cmd == 'bash -s' and 'S3cret' not in cmd and "'S3cret'\"'\"'pass$word'" in script
    assert 'CREATE DATABASE [shop]' in script and 'CREATE LOGIN [shop] WITH PASSWORD' in script and 'ALTER ROLE db_owner ADD MEMBER [shop]' in script and out['password'] in script
    assert out['password'].startswith('Sh1p-') and out['url'].startswith('Server=127.0.0.1,1433;Database=shop;User Id=shop;Password=Sh1p-')
    assert subprocess.run(['bash', '-n'], input=script.encode(), capture_output=True).returncode == 0
    assert '-e SQLCMDPASSWORD' in script and 'sqlcmd' in script


def test_sql_server_errors_are_clear_and_never_echo_the_password():
    ANSWERS.append((1, "Login failed for user 'sa'. " + SA))
    raises(d.mssql_create, CFG, 'mssql', SA, 'shop', text='refused the sa password')
    ANSWERS.append((1, 'Msg 1801, Database already exists ' + SA))
    try:
        d.mssql_create(CFG, 'mssql', SA, 'shop')
    except RuntimeError as e:
        assert SA not in str(e) and 'already exists' in str(e)
    else:
        raise AssertionError('expected an error')
    ANSWERS.append((1, ''))
    raises(d.mssql_create, CFG, 'mssql', SA, 'shop', text='did not accept', kind=RuntimeError)
    ANSWERS.append((0, 'finished without the marker'))
    raises(d.mssql_create, CFG, 'mssql', SA, 'shop', text='without the marker', kind=RuntimeError)


def test_sql_server_drop_is_guarded():
    raises(d.mssql_drop, CFG, 'mssql', SA, 'master', 'master', text='protected')
    raises(d.mssql_drop, CFG, 'mssql', SA, 'shop', 'nope', text='Type the database name')
    ANSWERS.append((0, 'DONE\n'))
    assert d.mssql_drop(CFG, 'mssql', SA, 'shop', 'shop') == 'dropped shop'
    assert 'DROP DATABASE [shop]' in CALLS[-1][1] and 'DROP LOGIN [shop]' in CALLS[-1][1]


if __name__ == '__main__':
    run_tests(globals(), before=before)
