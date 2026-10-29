"""Care fixes: preview of the exact commands, a marker per applied fix, and undo only of what Shiplane applied (fake server).

    python tests/test_care_undo.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import care, config, deploy  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy', 'port': 22, 'auth': 'key'}
RAW = {'os': 'Ubuntu 24.04', 'os_id': 'ubuntu', 'ssh': {'port': [22], 'passwordauthentication': 'yes', 'permitrootlogin': 'yes'}, 'key_users': ['deploy'], 'root_keys': False,
       'ufw': {'installed': True, 'active': False, 'default_incoming': 'allow', 'allows': []}, 'fail2ban': {'active': False, 'banned': 0}, 'updates': {'installed': False, 'enabled': False},
       'time_synced': True, 'swap_mb': 0, 'ram_mb': 1024, 'disk_free': 50e9, 'disk_total': 100e9, 'listening': [{'port': 22}, {'port': 80}], 'reboot_required': False, 'mem_avail_mb': 500}


def raises(fn, text):
    try:
        fn()
    except (ValueError, RuntimeError) as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected ' + text)


def test_preview_shows_the_commands_and_nothing_runs():
    care.collect = lambda cfg: RAW
    pv = care.preview(CFG, 'fail2ban')
    assert pv['label'] == 'Install fail2ban' and any('apt-get install' in c for c in pv['commands']) and not any(c.startswith(('echo', 'set -e')) for c in pv['commands'])
    assert all(care.MARK_DIR not in c for c in pv['commands'])
    raises(lambda: care.preview(CFG, 'nope'), 'Unknown fix')


def test_every_fix_has_an_undo_script_and_all_scripts_are_valid_bash():
    for fix, (file, _) in care.FIXES.items():
        assert os.path.isfile(os.path.join(config.REMOTE, 'care', 'undo', file)), fix
        text = care.undo_script(fix)
        assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0, fix
        assert text.rstrip().endswith('%s/%s' % (care.MARK_DIR, fix)) and 'sudo rm -f' in text.splitlines()[-1]
        fixed = care.fix_script(fix, dict(RAW, swap_mb=0), CFG) if fix != 'ssh_keys_only' else care.fix_script(fix, RAW, CFG)
        assert fixed.rstrip().endswith('%s/%s' % (care.MARK_DIR, fix)) and subprocess.run(['bash', '-n'], input=fixed.encode(), capture_output=True).returncode == 0, fix
    raises(lambda: care.undo_script('nope'), 'Unknown fix')


def test_undo_is_refused_for_fixes_shiplane_did_not_apply():
    fake = FakeServer([('ls -1', (0, 'swap\nfail2ban\nbogus\n'))])
    care.run = fake.run
    assert care.applied(CFG) == ['swap', 'fail2ban']                                   # unknown names are ignored
    raises(lambda: care.start_undo(CFG, 'swap', 'yes'), 'Confirm the undo')
    raises(lambda: care.start_undo(CFG, 'firewall', 'undo'), 'did not apply this fix')
    raises(lambda: care.start_undo(CFG, 'nope', 'undo'), 'Unknown fix')


def test_undo_runs_the_script_for_an_applied_fix_and_blocks_a_second_one():
    care.run = FakeServer([('ls -1', (0, 'swap\n'))]).run
    sent = {}
    real = deploy.start_script_job
    deploy.start_script_job = lambda cfg, text: sent.setdefault('text', text) and 'job1'
    deploy.JOBS['job1'] = deploy.Job()
    try:
        assert care.start_undo(CFG, 'swap', 'undo') == 'job1' and 'swapoff /swapfile' in sent['text'] and getattr(deploy.JOBS['job1'], 'care', False)
        raises(lambda: care.start_undo(CFG, 'swap', 'undo'), 'still running')
    finally:
        deploy.start_script_job = real
        deploy.JOBS.pop('job1', None)


def test_report_lists_applied_fixes():
    care.collect = lambda cfg: RAW
    care.run = FakeServer([('ls -1', (0, 'fail2ban\n'))]).run
    out = care.report(CFG)
    assert out['applied'] == [{'id': 'fail2ban', 'label': 'Install fail2ban'}]
    care.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('ssh down'))
    assert care.report(CFG)['applied'] == []                                           # the checklist still works when the marker list cannot be read


if __name__ == '__main__':
    run_tests(dict(globals()))
