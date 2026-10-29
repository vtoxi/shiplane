"""ufw manager: parsing, validation, previews and the lock-out guard (fake server).

    python tests/test_firewall.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import firewall  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy', 'port': 2222}
UFW = '''Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
2222/tcp                   ALLOW IN    Anywhere
80/tcp                     ALLOW IN    10.0.0.0/8
443                        ALLOW IN    Anywhere
2222/tcp (v6)              ALLOW IN    Anywhere (v6)
'''


def use(ufw=UFW, sshd='port 2222'):
    fake = FakeServer([('sshd -T', (0, '2222\n')), ('ufw status', (0, ufw))])
    firewall.run = fake.run
    return fake


def raises(fn, text):
    try:
        fn()
    except (ValueError, RuntimeError) as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected ' + text)


def test_parse():
    info = firewall.parse(UFW)
    assert info['active'] and info['default_in'] == 'deny'
    assert [(r['to'], r['action'], r['from']) for r in info['rules']] == [('2222/tcp', 'allow', 'Anywhere'), ('80/tcp', 'allow', '10.0.0.0/8'), ('443', 'allow', 'Anywhere')]
    assert firewall.parse('Status: inactive\n')['active'] is False


def test_status_ssh_ports_and_missing_ufw():
    use()
    info = firewall.status(CFG)
    assert info['installed'] and info['ssh_ports'] == [2222]
    firewall.run = FakeServer([('ufw status', (127, 'sudo: ufw: command not found'))]).run
    assert firewall.status(CFG)['installed'] is False


def test_validation_and_previews_never_touch_the_server():
    fake = use()
    assert firewall.add(CFG, 'allow', '8080', 'tcp', preview=True) == {'commands': ['sudo ufw allow 8080/tcp']}
    assert firewall.add(CFG, 'allow', '6000:6100', 'udp', '10.1.2.3', preview=True)['commands'] == ['sudo ufw allow from 10.1.2.3/32 to any port 6000:6100 proto udp']
    assert firewall.add(CFG, 'limit', '22', 'any', preview=True)['commands'] == ['sudo ufw limit 22']
    for bad in (('allow', '0'), ('allow', '70000'), ('allow', '9:3'), ('allow', '80; rm -rf /'), ('allow', 'x'), ('open', '80')):
        raises(lambda: firewall.add(CFG, *bad, preview=True), '')
    raises(lambda: firewall.add(CFG, 'allow', '80', 'icmp', preview=True), 'Protocol')
    raises(lambda: firewall.add(CFG, 'allow', '80', 'tcp', 'evil; ls', preview=True), 'From must be')
    assert not fake.calls


def test_add_runs_the_command_and_reports_errors():
    fake = use()
    assert firewall.add(CFG, 'allow', '8080', 'tcp')['commands'] == ['sudo ufw allow 8080/tcp'] and fake.ran('sudo ufw allow 8080/tcp')
    firewall.run = FakeServer([('sshd -T', (0, '2222')), ('ufw status', (0, UFW)), ('ufw allow', (1, 'ERROR: bad rule'))]).run
    raises(lambda: firewall.add(CFG, 'allow', '8080', 'tcp'), 'bad rule')
    firewall.run = FakeServer([('ufw status', (127, 'ufw: command not found'))]).run
    raises(lambda: firewall.add(CFG, 'allow', '8080', 'tcp'), 'not installed')


def test_cannot_block_ssh():
    use()
    raises(lambda: firewall.add(CFG, 'deny', '2222', 'tcp'), 'block SSH')
    raises(lambda: firewall.add(CFG, 'deny', '2000:3000', 'tcp'), 'block SSH')
    assert firewall.add(CFG, 'deny', '2222', 'tcp', '203.0.113.9')['commands']            # blocking one address is fine
    assert firewall.add(CFG, 'deny', '23', 'tcp')['commands']


def test_cannot_remove_the_last_ssh_allow_rule():
    fake = use()
    raises(lambda: firewall.delete(CFG, 'allow', '2222', 'tcp'), 'lock you out')
    assert not fake.ran('delete')
    assert firewall.delete(CFG, 'allow', '80', 'tcp', '10.0.0.0/8')['commands'] == ['sudo ufw --force delete allow from 10.0.0.0/8 to any port 80 proto tcp']
    two = UFW.replace('443                        ALLOW IN    Anywhere', '2000:3000/tcp                ALLOW IN    Anywhere')   # another rule still opens 2222
    use(two)
    assert firewall.delete(CFG, 'allow', '2222', 'tcp')['commands']


def test_power_needs_confirmation_and_opens_ssh_first():
    use()
    raises(lambda: firewall.power(CFG, True, 'yes'), 'Type "firewall"')
    cmds = firewall.power(CFG, True, 'firewall', preview=True)['commands']
    assert cmds == ['sudo ufw allow 2222/tcp', 'sudo ufw --force enable']
    fake = use()
    firewall.power(CFG, True, 'firewall')
    assert fake.calls[-1]['cmd'].startswith('sudo ufw allow 2222/tcp && sudo ufw --force enable')
    assert firewall.power(CFG, False, 'firewall', preview=True)['commands'] == ['sudo ufw disable']
    firewall.run = FakeServer([('ufw', (1, 'nope'))]).run
    raises(lambda: firewall.power(CFG, False, 'firewall'), 'nope')


def test_local_mode_uses_sshd_ports_only():
    firewall.run = FakeServer([('sshd -T', (0, '2200\n'))]).run
    assert firewall.ssh_ports({'mode': 'local'}) == [2200]
    firewall.run = FakeServer([('sshd -T', (1, ''))]).run
    assert firewall.ssh_ports({'mode': 'local'}) == [22]


if __name__ == '__main__':
    run_tests(dict(globals()))
