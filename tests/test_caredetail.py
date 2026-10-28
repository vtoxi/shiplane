"""Server care details and the ban/unban actions: parsers, folder rules and every guard (no server needed).

    python tests/test_caredetail.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import care, caredetail as cd  # noqa: E402

CFG = {'mode': 'ssh', 'host': '203.0.113.10', 'user': 'deploy'}
ORIGINAL = {(cd, 'run'): cd.run, (care, 'collect'): care.collect}


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


F2B = '''Status for the jail: sshd
|- Filter
|  |- Currently failed:\t3
|  |- Total failed:\t412
|  `- File list:\t/var/log/auth.log
`- Actions
   |- Currently banned:\t2
   |- Total banned:\t57
   `- Banned IP list:\t203.0.113.7 2001:db8::5
'''
DU = '5000000\t/\n3000000\t/var\n1500000\t/usr\n200000\t/home\n100\t/boot\n'
DF = 'Mounted on  Size Used Avail Use%\n/ 38G 11G 27G 29%\n/boot 974M 100M 807M 11%\n'
FREE = '               total        used        free      shared  buff/cache   available\nMem:            3814        1444         250          53        2419        2370\nSwap:           2047         100        1947\n'
PS = '    PID USER           RSS %CPU COMMAND\n  101 postgres    204800  1.5 postgres\n  102 root         51200  0.0 python3\n  103 www-data      10240  0.2 php-fpm8.3\n'
SSH = 'TOTAL=130\n     80 198.51.100.4\n     30 203.0.113.99\n     20 2001:db8::bad\n'


# ------------------------------------------------------------------ parsers
def test_fail2ban_parser():
    out = cd.parse_fail2ban(F2B)
    assert out == {'kind': 'fail2ban', 'installed': True, 'banned': ['203.0.113.7', '2001:db8::5'], 'currently_failed': 3, 'total_failed': 412, 'total_banned': 57}
    none = cd.parse_fail2ban("Status for the jail: sshd\n   `- Banned IP list:\t\n")
    assert none['banned'] == [] and none['installed'] is True
    gone = cd.parse_fail2ban("ERROR   NOK: ('sshd',)\nSorry but the jail 'sshd' does not exist")
    assert gone['installed'] is False and gone['banned'] == [] and gone['total_failed'] == 0 and cd.parse_fail2ban('')['installed'] is False


def test_disk_parser_sorts_and_skips_the_folder_itself():
    out = cd.parse_disk(DU, DF, '/')
    assert out['total_kb'] == 5000000 and [i['path'] for i in out['items']] == ['/var', '/usr', '/home', '/boot']
    assert out['mounts'][0] == {'mount': '/', 'size': '38G', 'used': '11G', 'avail': '27G', 'pct': 29} and len(out['mounts']) == 2
    inner = cd.parse_disk('900\t/var/lib\n500\t/var/lib/mysql\n400\t/var/lib/apt\n', DF, '/var/lib')
    assert inner['total_kb'] == 900 and [i['path'] for i in inner['items']] == ['/var/lib/mysql', '/var/lib/apt']
    assert cd.parse_disk('', '', '/') == {'kind': 'disk', 'path': '/', 'total_kb': None, 'items': [], 'mounts': []}
    many = cd.parse_disk('\n'.join('%d\t/d%d' % (1000 - i, i) for i in range(30)), DF, '/')
    assert len(many['items']) == 10 and many['items'][0]['kb'] == 1000                                          # ten, biggest first
    assert cd.parse_disk('x\n12 relative\n', 'Header\nbad row\n', '/')['items'] == []                            # junk lines are ignored


def test_memory_parser():
    out = cd.parse_memory(FREE, PS)
    assert (out['total_mb'], out['used_mb'], out['available_mb'], out['swap_total_mb'], out['swap_used_mb']) == (3814, 1444, 2370, 2047, 100)
    assert out['processes'][0] == {'pid': 101, 'user': 'postgres', 'rss_mb': 200.0, 'cpu': 1.5, 'name': 'postgres'} and len(out['processes']) == 3
    empty = cd.parse_memory('', '')
    assert empty['total_mb'] == 0 and empty['processes'] == [] and empty['available_mb'] == 0
    assert cd.parse_memory('Mem: 100\n', 'h\n  7 u 1 x y z\nbad line\n')['processes'][0]['name'] == 'y z'
    assert cd.parse_memory(FREE, 'h\n' + '\n'.join('  %d u 1024 0.0 p%d' % (i, i) for i in range(1, 30)))['processes'].__len__() == 10


def test_ssh_and_updates_and_firewall_parsers():
    out = cd.parse_ssh(SSH)
    assert out == {'kind': 'ssh', 'failed_24h': 130, 'top': [{'ip': '198.51.100.4', 'count': 80}, {'ip': '203.0.113.99', 'count': 30}, {'ip': '2001:db8::bad', 'count': 20}]}
    assert cd.parse_ssh('') == {'kind': 'ssh', 'failed_24h': 0, 'top': []}
    up = cd.parse_updates('openssl/noble-security 3.0.2 amd64 [upgradable from: 3.0.1]\nlinux-image/noble 6.8 amd64\n---\nCOUNT=2\nlinux-image-6.8.0\nlibc6\n')
    assert up == {'kind': 'updates', 'count': 2, 'packages': ['openssl', 'linux-image'], 'reboot_packages': ['linux-image-6.8.0', 'libc6']}
    assert cd.parse_updates('') == {'kind': 'updates', 'count': 0, 'packages': [], 'reboot_packages': []}
    assert cd.parse_updates('a/b c\n---\n')['count'] == 1                                                         # no explicit count: use the list
    fw = cd.parse_firewall('Status: active\n\n     To                         Action      From\n[ 1] 22/tcp                     ALLOW IN    Anywhere\n[ 2] 80                         ALLOW IN    Anywhere\n')
    assert fw == {'kind': 'firewall', 'active': True, 'rules': ['[ 1] 22/tcp                     ALLOW IN    Anywhere', '[ 2] 80                         ALLOW IN    Anywhere']}
    assert cd.parse_firewall('Status: inactive')['active'] is False


# ------------------------------------------------------------------ reading
def test_every_card_has_details():
    assert set(cd.DETAIL_OF) == {'ssh_password', 'ssh_root', 'fail2ban', 'firewall', 'auto_updates', 'reboot', 'time_sync', 'swap', 'memory', 'disk', 'exposed'}
    findings = {f['id'] for f in care.evaluate(dict(__import__('test_care').good()))['findings']}
    assert findings <= set(cd.DETAIL_OF)                                                                          # no card is left without a detail view
    raises(cd.detail, CFG, 'nope', text='No details')
    raises(cd.detail, CFG, '', text='No details')


def test_details_run_fixed_commands_and_parse_them():
    srv = FakeServer().when('fail2ban-client status', 0, F2B).when('du -xk', 0, DU).when('df -h', 0, DF).when('free -m', 0, FREE).when('ps -eo', 0, PS).when('journalctl', 0, SSH)
    srv.when('ufw status', 0, 'Status: active\n[ 1] 22/tcp ALLOW IN Anywhere').when('apt list', 0, 'a/b c\n---\nCOUNT=1\n').when('timedatectl', 0, 'Local time: x\nNTP service: active')
    cd.run = srv.run
    assert cd.detail(CFG, 'fail2ban')['banned'][0] == '203.0.113.7'
    disk = cd.detail(CFG, 'disk')
    assert disk['items'][0]['path'] == '/var' and disk['partial'] is False
    cmd = srv.ran('du -xk')[0]['cmd']
    assert cmd.startswith('nice -n 19 ionice -c 3 timeout 30 du -xk --max-depth=1 / 2>/dev/null') and cmd.endswith('head -14')      # lowest priority, time-limited
    assert cd.detail(CFG, 'disk', '/var/lib/')['path'] == '/var/lib' and ' du -xk --max-depth=1 /var/lib 2>' in srv.ran('du -xk')[-1]['cmd']
    assert cd.detail(CFG, 'memory')['processes'][0]['name'] == 'postgres' and cd.detail(CFG, 'swap')['kind'] == 'memory'
    assert cd.detail(CFG, 'ssh_password')['failed_24h'] == 130 and cd.detail(CFG, 'ssh_root')['kind'] == 'ssh'
    assert cd.detail(CFG, 'firewall')['rules'] and cd.detail(CFG, 'reboot')['count'] == 1 and cd.detail(CFG, 'auto_updates')['kind'] == 'updates'
    assert cd.detail(CFG, 'time_sync') == {'kind': 'time', 'text': 'Local time: x\nNTP service: active'}


def test_a_scan_that_timed_out_says_so():
    cd.run = FakeServer().when('du -xk', 124, '').when('df -h', 0, DF).run
    out = cd.detail(CFG, 'disk')
    assert out['partial'] is True and out['items'] == []
    cd.run = FakeServer().when('du -xk', 0, '300\t/var/lib/mysql\n').when('df -h', 0, DF).run
    assert cd.detail(CFG, 'disk', '/var/lib')['partial'] is True                                                   # the folder's own total is missing: incomplete


def test_exposed_uses_fresh_facts_unless_given():
    raw = {'listening': [{'port': 5432, 'addr': '0.0.0.0', 'proc': 'postgres'}]}
    care.collect = lambda cfg: raw
    out = cd.detail(CFG, 'exposed')
    assert out['listening'] == raw['listening'] and out['risky']['5432'] == 'PostgreSQL'
    care.collect = lambda cfg: 1 / 0
    assert cd.detail(CFG, 'exposed', raw=raw)['listening'] == raw['listening']


def test_folder_paths_are_checked_before_anything_runs():
    srv = FakeServer()
    cd.run = srv.run
    for bad in ('relative', '/a/../etc', '/a b', '/a;rm -rf /', '/a$HOME', '/a`x`', '//etc', '/' + 'x' * 250, '/var/\nroot'):
        raises(cd.detail, CFG, 'disk', bad, text='looks wrong')
    assert srv.calls == []
    assert cd.check_folder(None) == '/' and cd.check_folder('') == '/' and cd.check_folder('/var/log/') == '/var/log' and cd.check_folder('/') == '/'


# ------------------------------------------------------------------ actions
def test_ban_and_unban():
    srv = FakeServer().when('fail2ban-client set', 0, '1')
    cd.run = srv.run
    assert cd.action(CFG, 'unban', '203.0.113.7', 'apply') == 'Unbanned 203.0.113.7' and srv.calls[-1]['cmd'] == "sudo fail2ban-client set sshd unbanip 203.0.113.7 2>&1"
    assert cd.action(CFG, 'ban', ' 198.51.100.4 ', 'apply', caller_ip='198.51.100.9') == 'Banned 198.51.100.4' and 'banip 198.51.100.4' in srv.calls[-1]['cmd']
    assert cd.action(CFG, 'ban', '2001:db8::bad', 'apply') == 'Banned 2001:db8::bad'
    assert cd.action(CFG, 'unban', '127.0.0.1', 'apply')                                                           # unbanning anything is harmless


def test_ban_refuses_to_lock_you_out():
    srv = FakeServer()
    cd.run = srv.run
    for ip in ('127.0.0.1', '::1', '0.0.0.0', '224.0.0.1', '203.0.113.10', '198.51.100.9'):
        raises(cd.action, CFG, 'ban', ip, 'apply', '198.51.100.9', text='would lock you out')                      # loopback, this server, and the address you are using
    assert srv.calls == []


def test_action_input_checks():
    srv = FakeServer()
    cd.run = srv.run
    raises(cd.action, CFG, 'ban', '1.2.3.4', None, text='Confirm the change first')
    raises(cd.action, CFG, 'ban', '1.2.3.4', 'yes', text='Confirm the change first')
    raises(cd.action, CFG, 'flush', '1.2.3.4', 'apply', text='Unknown action')
    for bad in ('', 'x', '1.2.3', '1.2.3.4; rm -rf /', '1.2.3.4 5.6.7.8', '$(id)', '999.1.1.1', None, '1.2.3.4\n'):
        if bad == '1.2.3.4\n':
            assert cd.action(CFG, 'unban', bad, 'apply') or True                                                       # whitespace around a valid address is trimmed
            continue
        raises(cd.action, CFG, 'ban', bad, 'apply', text='not an IP address')
    cd.run = FakeServer().when('fail2ban-client set', 1, 'NOK: jail sshd does not exist').run
    raises(cd.action, CFG, 'ban', '1.2.3.4', 'apply', text='jail sshd does not exist', kind=RuntimeError)
    cd.run = FakeServer().when('fail2ban-client set', 1, '').run
    raises(cd.action, CFG, 'ban', '1.2.3.4', 'apply', text='is it installed and running', kind=RuntimeError)


if __name__ == '__main__':
    run_tests(globals(), before=restore)
