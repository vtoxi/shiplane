"""Server care: the checklist, the score and every safety guard of the fixes (no server needed).

    python tests/test_care.py
"""
import importlib.util
import json
import os
import subprocess
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import care, config, deploy, perf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('care_check', os.path.join(ROOT, 'coderunner', 'remote', 'care_check.py'))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

KEY_CFG = {'mode': 'ssh', 'auth': 'key', 'host': 'srv.example.com', 'port': 22, 'user': 'deploy'}
PW_CFG = dict(KEY_CFG, auth='password')
LOCAL_CFG = {'mode': 'local'}
ORIGINAL = {(care, 'run'): care.run, (care.deploy, 'start_script_job'): deploy.start_script_job}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    deploy.JOBS.clear()


def good():
    """Facts of a well-kept server."""
    return {'os_id': 'ubuntu', 'os': 'Ubuntu 24.04 LTS', 'ssh': {'port': [22], 'passwordauthentication': 'no', 'permitrootlogin': 'prohibit-password', 'pubkeyauthentication': 'yes'},
            'key_users': ['deploy'], 'root_keys': False, 'ufw': {'installed': True, 'active': True, 'default_incoming': 'deny (incoming)', 'allows': [22, 80, 443]},
            'fail2ban': {'installed': True, 'active': True, 'banned': 3}, 'updates': {'installed': True, 'enabled': True}, 'time_synced': True, 'swap_mb': 2048, 'ram_mb': 2048, 'mem_avail_mb': 1500,
            'disk_total': 40 * 10 ** 9, 'disk_free': 30 * 10 ** 9, 'reboot_required': False, 'listening': [{'port': 22, 'addr': '0.0.0.0', 'proc': 'sshd'}, {'port': 80, 'addr': '*', 'proc': 'caddy'}]}


def bad():
    """Facts of a neglected server."""
    r = good()
    r.update(ssh={'port': [22], 'passwordauthentication': 'yes', 'permitrootlogin': 'yes'}, ufw={'installed': False, 'active': False, 'default_incoming': '', 'allows': []},
             fail2ban={'installed': False, 'active': False, 'banned': 0}, updates={'installed': False, 'enabled': False}, time_synced=False, swap_mb=0, ram_mb=1024, mem_avail_mb=40,
             disk_free=2 * 10 ** 9, reboot_required=True, listening=[{'port': 22, 'addr': '0.0.0.0', 'proc': 'sshd'}, {'port': 5432, 'addr': '0.0.0.0', 'proc': 'postgres'}])
    return r


def status_of(raw):
    return {f['id']: f['status'] for f in care.evaluate(raw)['findings']}


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


# --------------------------------------------------------- the remote script's parsers
def test_sshd_settings():
    out = cc.sshd_settings('port 22\nport 2222\nport x\npasswordauthentication no\npermitrootlogin prohibit-password\nunrelated yes\nkbdinteractiveauthentication no\n')
    assert out == {'port': [22, 2222], 'passwordauthentication': 'no', 'permitrootlogin': 'prohibit-password', 'kbdinteractiveauthentication': 'no'}
    assert cc.sshd_settings('') == {'port': []}


def test_ufw_state():
    text = 'Status: active\nLogging: on (low)\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n\nTo  Action  From\n22/tcp ALLOW IN Anywhere\n80 ALLOW IN Anywhere\n443/tcp (v6) ALLOW IN Anywhere (v6)\n'
    out = cc.ufw_state(text)
    assert out['installed'] and out['active'] and out['default_incoming'].startswith('deny') and out['allows'] == [22, 80]
    assert cc.ufw_state('Status: inactive\n') == {'installed': True, 'active': False, 'default_incoming': '', 'allows': []}
    assert cc.ufw_state('') == {'installed': False, 'active': False, 'default_incoming': '', 'allows': []}


def test_listeners_ignore_loopback_and_duplicates():
    text = ('LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
            'LISTEN 0 128 [::]:22 [::]:* users:(("sshd",pid=1,fd=4))\n'
            'LISTEN 0 4096 127.0.0.1:8765 0.0.0.0:* users:(("python3",pid=2,fd=5))\n'
            'LISTEN 0 4096 [::1]:5432 [::]:* users:(("postgres",pid=3,fd=6))\n'
            'LISTEN 0 511 *:80 *:* users:(("caddy",pid=4,fd=7))\n'
            'LISTEN 0 10 0.0.0.0:6379 0.0.0.0:*\n'
            'short line\n')
    assert cc.listeners(text) == [{'port': 22, 'addr': '0.0.0.0', 'proc': 'sshd'}, {'port': 80, 'addr': '*', 'proc': 'caddy'}, {'port': 6379, 'addr': '0.0.0.0', 'proc': ''}]
    assert cc.listeners('') == []


def test_key_users_need_sudo_and_a_real_key():
    files = {'/home/ann/.ssh/authorized_keys': '# only a comment\n\n', '/home/bob/.ssh/authorized_keys': 'ssh-ed25519 AAAA bob\n', '/home/eve/.ssh/authorized_keys': 'ssh-ed25519 AAAA eve\n'}
    real = (cc.glob.glob, cc.rd, cc.grp)
    Fake = namedtuple('G', 'gr_mem')

    class FakeGrp:
        @staticmethod
        def getgrnam(name):
            if name == 'sudo':
                return Fake(['ann', 'bob'])
            raise KeyError(name)
    cc.glob.glob, cc.rd, cc.grp = (lambda p: sorted(files)), (lambda p: files.get(p, '')), FakeGrp
    try:
        assert cc.key_users() == ['bob']                                                          # ann has no key, eve has no sudo
        cc.grp = None
        assert cc.key_users() == []                                                                # not Linux: nobody
    finally:
        cc.glob.glob, cc.rd, cc.grp = real


def test_main_collects_everything_with_fakes():
    answers = {'sshd -T': 'port 22\npasswordauthentication no\npermitrootlogin no', 'ufw status verbose': 'Status: active\nDefault: deny (incoming), allow (outgoing)\n22/tcp ALLOW IN Anywhere',
               'is-active fail2ban': 'active', 'fail2ban-client status sshd': '|- Currently banned:\t4', 'command -v fail2ban-client': '/usr/bin/fail2ban-client',
               'dpkg -s unattended-upgrades': '1', 'NTPSynchronized': 'yes', 'ss -H': 'LISTEN 0 1 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))'}
    files = {'/proc/meminfo': 'MemTotal: 2097152 kB\n', '/proc/swaps': 'Filename Type Size Used Priority\n/swapfile file 1048576 0 -2\n', '/etc/os-release': 'ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04"\n',
             '/etc/apt/apt.conf.d/20auto-upgrades': 'APT::Periodic::Unattended-Upgrade "1";\n', '/root/.ssh/authorized_keys': 'ssh-ed25519 AAAA root\n'}
    real = (cc.sh, cc.rd, getattr(cc.os, 'statvfs', None), cc.os.path.exists, cc.key_users)
    cc.sh = lambda c: next((v for k, v in answers.items() if k in c), '')
    cc.rd = lambda p: files.get(p, '')
    cc.os.statvfs = lambda p: namedtuple('V', 'f_blocks f_frsize f_bavail')(1000, 1000, 400)
    cc.os.path.exists = lambda p: p == '/var/run/reboot-required'
    cc.key_users = lambda: ['deploy']
    try:
        out = cc.main()
    finally:
        cc.sh, cc.rd, statvfs, cc.os.path.exists, cc.key_users = real
        if statvfs is None:
            del cc.os.statvfs                                                                           # Windows has none; leave no trace
        else:
            cc.os.statvfs = statvfs
    assert out['os_id'] == 'ubuntu' and out['ssh']['port'] == [22] and out['key_users'] == ['deploy'] and out['root_keys'] is True and out['ufw']['active'] and out['ufw']['allows'] == [22]
    assert out['fail2ban'] == {'installed': True, 'active': True, 'banned': 4} and out['updates'] == {'installed': True, 'enabled': True} and out['time_synced'] is True
    assert out['swap_mb'] == 1024 and out['ram_mb'] == 2048 and out['reboot_required'] is True and out['disk_total'] == 10 ** 6 and out['disk_free'] == 4 * 10 ** 5
    assert out['listening'][0]['proc'] == 'sshd' and json.dumps(out)


# ------------------------------------------------------------------ scoring
def test_a_well_kept_server_scores_100():
    out = care.evaluate(good())
    assert out['score'] == 100 and out['label'] == 'Well protected' and out['supported'] is True and out['os'] == 'Ubuntu 24.04 LTS'
    assert all(f['status'] == care.OK and f['fix'] is None for f in out['findings'])
    assert [f['id'] for f in out['findings']] == ['ssh_password', 'ssh_root', 'firewall', 'fail2ban', 'auto_updates', 'time_sync', 'swap', 'memory', 'disk', 'reboot', 'exposed']


def test_a_neglected_server_scores_low_and_offers_every_fix():
    out = care.evaluate(bad())
    assert out['score'] < 40 and out['label'] == 'At risk'
    s = {f['id']: f for f in out['findings']}
    assert {k: v['status'] for k, v in s.items()} == {'ssh_password': 'bad', 'ssh_root': 'bad', 'firewall': 'bad', 'fail2ban': 'warn', 'auto_updates': 'bad', 'time_sync': 'warn',
                                                      'swap': 'warn', 'memory': 'bad', 'disk': 'bad', 'reboot': 'warn', 'exposed': 'bad'}
    assert {k: v['fix'] for k, v in s.items() if v['fix']} == {'ssh_password': 'ssh_keys_only', 'ssh_root': 'ssh_no_root', 'firewall': 'firewall', 'fail2ban': 'fail2ban',
                                                              'auto_updates': 'auto_updates', 'time_sync': 'time_sync', 'swap': 'swap', 'exposed': 'firewall'}
    assert 'PostgreSQL (5432)' in s['exposed']['detail']


def test_score_weights_and_labels():
    r = good()
    r['ssh']['passwordauthentication'] = 'yes'
    assert care.evaluate(r)['score'] == 80 and care.evaluate(r)['label'] == 'Good, with some gaps'                  # one bad item costs its full weight
    r['fail2ban'] = {'installed': True, 'active': False, 'banned': 0}
    assert care.evaluate(r)['score'] == 75                                                                       # a warning costs half its weight
    r['ufw']['active'] = False
    assert care.evaluate(r)['score'] == 55 and care.evaluate(r)['label'] == 'Needs attention'
    worst = bad()
    worst['disk_free'] = 0
    assert 0 <= care.evaluate(worst)['score'] < 40


def test_disk_thresholds():
    for free_gb, expected in ((9, 'ok'), (7.9, 'warn'), (7, 'warn'), (3.9, 'bad'), (0, 'bad')):                 # 40 GB disk: warn from 80% used, bad from 90%
        r = good()
        r['disk_free'] = int(free_gb * 10 ** 9)
        assert status_of(r)['disk'] == expected, free_gb
    r = good()
    r['disk_total'] = 0
    assert status_of(r)['disk'] in ('ok', 'bad')                                                                  # no division by zero


def test_memory_thresholds():
    for avail, expected in ((1500, 'ok'), (400, 'ok'), (308, 'ok'), (307, 'warn'), (200, 'warn'), (103, 'warn'), (102, 'bad'), (0, 'bad')):                  # 2048 MB: warn from 85% used, bad from 95%
        r = good()
        r['mem_avail_mb'] = avail
        assert status_of(r)['memory'] == expected, avail
    r = good()
    del r['mem_avail_mb']
    assert 'memory' not in status_of(r)                                                                          # facts from an older script: no guess
    r['mem_avail_mb'], r['ram_mb'] = 0, 0
    assert 'memory' not in status_of(r)                                                                          # no division by zero
    memory = [f for f in care.evaluate(dict(good(), mem_avail_mb=10))['findings'] if f['id'] == 'memory'][0]
    assert memory['status'] == 'bad' and memory['fix'] is None and '2038 MB of 2048 MB' in memory['detail']      # nothing to "fix": the details view shows what to do


def test_firewall_variants():
    for ufw, expected in (({'installed': True, 'active': True, 'default_incoming': 'allow', 'allows': []}, 'warn'), ({'installed': True, 'active': False, 'default_incoming': '', 'allows': []}, 'bad'),
                          ({'installed': False, 'active': False, 'default_incoming': '', 'allows': []}, 'bad'), ({'installed': True, 'active': True, 'default_incoming': 'deny', 'allows': []}, 'ok')):
        r = good()
        r['ufw'] = ufw
        assert status_of(r)['firewall'] == expected, ufw
    r = good()
    assert care.evaluate(r)['findings'][2]['detail'] == 'On: only ports 22, 80, 443 open to the outside'
    r['ufw']['allows'] = [22]
    assert care.evaluate(r)['findings'][2]['detail'] == 'On: only port 22 open to the outside'
    r['ufw']['allows'] = []
    assert care.evaluate(r)['findings'][2]['detail'] == 'On: nothing is open to the outside'


def test_ssh_variants_and_swap_variants():
    for root, expected in (('no', 'ok'), ('prohibit-password', 'ok'), ('without-password', 'ok'), ('forced-commands-only', 'ok'), ('yes', 'bad'), ('', 'bad')):
        r = good()
        r['ssh']['permitrootlogin'] = root
        assert status_of(r)['ssh_root'] == expected, root
    r = good()
    r['ssh'] = {'port': [22]}                                                                                     # sshd -T unreadable: assume the worst, never claim it is safe
    assert status_of(r)['ssh_password'] == 'bad' and status_of(r)['ssh_root'] == 'bad'
    for ram, swap, expected in ((512, 0, 'warn'), (4096, 0, 'warn'), (8192, 0, 'ok'), (512, 512, 'ok'), (0, 0, 'ok')):
        r = good()
        r['ram_mb'], r['swap_mb'] = ram, swap
        assert status_of(r)['swap'] == expected, (ram, swap)


def test_exposed_services_depend_on_the_firewall():
    r = good()
    r['listening'].append({'port': 6379, 'addr': '0.0.0.0', 'proc': 'redis'})
    assert status_of(r)['exposed'] == 'ok'                                                                       # redis listens, but the firewall blocks it
    r['ufw']['allows'].append(6379)
    assert status_of(r)['exposed'] == 'bad'                                                                      # ...unless it was allowed through
    r['ufw']['active'] = False
    s = {f['id']: f for f in care.evaluate(r)['findings']}
    assert s['exposed']['status'] == 'bad' and s['exposed']['fix'] == 'firewall' and 'Redis (6379)' in s['exposed']['detail']
    r['ufw']['active'] = True
    r['ufw']['allows'] = [22, 6379]
    assert {f['id']: f for f in care.evaluate(r)['findings']}['exposed']['fix'] is None                           # the firewall is already on: advice, not a fix


# ------------------------------------------------------------------ fix guards
def test_ssh_keys_only_guards():
    raw = good()
    assert 'PasswordAuthentication no' in care.fix_script('ssh_keys_only', raw, KEY_CFG) and care.fix_script('ssh_keys_only', raw, LOCAL_CFG)
    raises(care.fix_script, 'ssh_keys_only', raw, PW_CFG, text='logs in to this server with a password')
    nokeys = dict(good(), key_users=[], root_keys=False)
    raises(care.fix_script, 'ssh_keys_only', nokeys, KEY_CFG, text='would lock everyone out')
    assert care.fix_script('ssh_keys_only', dict(nokeys, root_keys=True), KEY_CFG)                                # root with a key is enough to keep a way in
    assert care.fix_script('ssh_keys_only', dict(nokeys, key_users=['ann']), KEY_CFG)
    nokeys.pop('root_keys')
    raises(care.fix_script, 'ssh_keys_only', nokeys, KEY_CFG, text='would lock everyone out')


def test_ssh_no_root_guard():
    assert 'prohibit-password' in care.fix_script('ssh_no_root', good(), KEY_CFG)
    assert care.fix_script('ssh_no_root', good(), dict(PW_CFG, user='deploy'))                                    # a password user other than root is not affected
    raises(care.fix_script, 'ssh_no_root', good(), dict(PW_CFG, user='root'), text='logs in as root with a password')
    assert care.fix_script('ssh_no_root', good(), dict(KEY_CFG, user='root'))                                     # root with a key keeps working


def test_firewall_always_keeps_ssh_open():
    raw = good()
    raw['ssh']['port'] = [2222]
    text = care.fix_script('firewall', raw, dict(KEY_CFG, port=22))
    assert 'for p in 22 2222; do' in text and 'for p in 80; do' in text                                           # sshd's port AND the port Shiplane uses; web only if it was public
    assert 'for p in ; do' in care.fix_script('firewall', dict(good(), listening=[]), KEY_CFG)                    # no web server: no web ports (and the loop stays valid bash)
    assert 'for p in 80 443; do' in care.fix_script('firewall', dict(good(), listening=[{'port': 443, 'addr': '*', 'proc': 'c'}, {'port': 80, 'addr': '*', 'proc': 'c'}]), KEY_CFG)
    raw['ssh']['port'] = []
    assert 'for p in 22; do' in care.fix_script('firewall', raw, KEY_CFG)                                         # sshd unreadable, but the app's own port is known
    raises(care.fix_script, 'firewall', raw, LOCAL_CFG, text='Could not work out the SSH port')
    raw['ssh']['port'] = [70000]
    raises(care.fix_script, 'firewall', raw, LOCAL_CFG, text='Could not work out the SSH port')
    assert 'ports = 22,2222' not in care.fix_script('fail2ban', dict(good(), ssh={'port': [22, 2222]}), LOCAL_CFG) and 'port = 22,2222' in care.fix_script('fail2ban', dict(good(), ssh={'port': [22, 2222]}), LOCAL_CFG)


def test_swap_sizes_and_guards():
    for ram, size in ((512, 1024), (1024, 1024), (1025, 2048), (4096, 2048)):
        assert '__SIZE' not in care.fix_script('swap', dict(good(), ram_mb=ram, swap_mb=0), LOCAL_CFG) and care.swap_size({'ram_mb': ram}) == size
        assert 'fallocate -l %dM' % size in care.fix_script('swap', dict(good(), ram_mb=ram, swap_mb=0), LOCAL_CFG)
    assert care.swap_size({'ram_mb': 8192}) == 0 and care.swap_size({'ram_mb': 0}) == 0 and care.swap_size({}) == 0
    raises(care.fix_script, 'swap', dict(good(), ram_mb=8192, swap_mb=0), LOCAL_CFG, text='does not need')
    raises(care.fix_script, 'swap', dict(good(), ram_mb=1024, swap_mb=512), LOCAL_CFG, text='does not need')
    raises(care.fix_script, 'swap', dict(good(), ram_mb=1024, swap_mb=0, disk_free=3 * 10 ** 9), LOCAL_CFG, text='Not enough free disk')


def test_other_fixes_and_general_refusals():
    for fix in ('auto_updates', 'time_sync', 'fail2ban'):
        assert care.fix_script(fix, good(), LOCAL_CFG).startswith('set -euo pipefail\n')
    raises(care.fix_script, 'rm -rf /', good(), LOCAL_CFG, text='Unknown fix')
    raises(care.fix_script, '', good(), LOCAL_CFG, text='Unknown fix')
    centos = dict(good(), os_id='centos', os='CentOS 9')
    for fix in care.FIXES:
        raises(care.fix_script, fix, centos, LOCAL_CFG, text='Ubuntu and Debian')
    assert care.evaluate(centos)['supported'] is False


def test_every_fix_script_is_valid_bash_and_fully_filled():
    raw = dict(good(), ram_mb=1024, swap_mb=0)
    for fix in care.FIXES:
        text = care.fix_script(fix, raw, KEY_CFG)
        assert not care.re.search(r'__[A-Z_]+__', text), fix
        p = subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True)
        assert p.returncode == 0, (fix, p.stderr.decode())
    ssh = care.fix_script('ssh_keys_only', raw, KEY_CFG)
    assert ssh.index('sshd -t') < ssh.index('systemctl reload') and 'rm -f "$CONF"' in ssh                          # validate first, undo on failure, then reload
    fw = care.fix_script('firewall', raw, KEY_CFG)
    assert fw.index('allow "$p/tcp"') < fw.index('--force enable')                                                  # SSH is allowed BEFORE the firewall is switched on


# ------------------------------------------------------------ talking to the server
def test_collect_and_report():
    raw = bad()
    care.run = FakeServer().when('python3', 0, 'noise\n' + json.dumps(raw)).run
    assert care.collect(KEY_CFG)['os_id'] == 'ubuntu'
    out = care.report(KEY_CFG)
    s = {f['id']: f for f in out['findings']}
    assert s['ssh_password']['fix'] == {'id': 'ssh_keys_only', 'label': 'Turn off password login', 'blocked': ''}
    assert 'Not enough free disk' in s['swap']['fix']['blocked'] and s['disk']['fix'] is None                      # this neglected server has only 2 GB free: a swap file is refused, and the page can say why
    assert s['firewall']['fix']['blocked'] == ''
    out = care.report(PW_CFG)
    assert 'logs in to this server with a password' in {f['id']: f for f in out['findings']}['ssh_password']['fix']['blocked']        # shown, so the button can say why it is disabled
    care.run = FakeServer().when('python3', 255, 'ssh: connect to host srv.example.com port 22: Connection timed out').run
    raises(care.collect, KEY_CFG, text='Could not reach', kind=RuntimeError)
    care.run = FakeServer().when('python3', 0, 'no json').run
    raises(care.collect, KEY_CFG, text='no json', kind=RuntimeError)
    care.run = FakeServer().when('python3', 1, '').run
    raises(care.collect, KEY_CFG, text='could not read the server', kind=RuntimeError)


def test_start_fix_rereads_the_server_and_needs_confirmation():
    care.run = FakeServer().when('python3', 0, json.dumps(good())).run
    started = []
    deploy.start_script_job = lambda cfg, text: started.append(text) or deploy.JOBS.setdefault('job-1', deploy.Job()) and 'job-1'
    raises(care.start_fix, KEY_CFG, 'time_sync', None, text='Confirm the change first')
    raises(care.start_fix, KEY_CFG, 'time_sync', 'yes', text='Confirm the change first')
    raises(care.start_fix, KEY_CFG, 'nonsense', 'apply', text='Unknown fix')
    assert not started
    perf.cached('x', lambda: 1, ttl=60)
    assert care.start_fix(KEY_CFG, 'time_sync', 'apply') == 'job-1' and started[0].startswith('set -euo pipefail') and deploy.JOBS['job-1'].care is True
    assert not perf._cache                                                                                         # what the page shows is re-read afterwards
    raises(care.start_fix, KEY_CFG, 'auto_updates', 'apply', text='Another fix is still running')                   # one at a time
    deploy.JOBS['job-1'].done = True
    assert care.start_fix(KEY_CFG, 'auto_updates', 'apply') == 'job-1'
    care.run = FakeServer().when('python3', 0, json.dumps(dict(good(), key_users=[], root_keys=False))).run
    deploy.JOBS['job-1'].done = True
    raises(care.start_fix, KEY_CFG, 'ssh_keys_only', 'apply', text='would lock everyone out')                       # judged on FRESH facts, whatever the page showed
    assert len(started) == 2


if __name__ == '__main__':
    run_tests(globals(), before=restore)
