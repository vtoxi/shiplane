"""The mail server's logic without any mail software: validation, the rendered Postfix / Dovecot / Rspamd / sieve files, the model rules, and the Python side.
(tests/test_mail_integration.py checks the same against the real Postfix, Dovecot and Rspamd when you ask for it.)

    python tests/test_mail.py
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import config, deploy, firewall, mail  # noqa: E402

spec = importlib.util.spec_from_file_location('mailctl', os.path.join(config.REMOTE, 'mail', 'mailctl.py'))
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)                                    # the remote script is importable: only main() needs the request
CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}


def fail(fn, text=''):
    try:
        fn()
    except (mc.Fail, ValueError) as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected an error containing %r' % text)


def conf():
    c = mc.new_conf()
    c['hostname'] = 'mail.example.com'
    mc.add_domain(c, 'example.com')
    mc.add_account(c, 'alice@example.com', '{SHA512-CRYPT}$6$salt$hash', 500, 'Alice')
    mc.add_account(c, 'bob@example.com', '{SHA512-CRYPT}$6$salt$hash2', 0)
    return c


def test_validation():
    assert mc.domain_ok(' Example.COM. ') == 'example.com' and mc.email_ok(' Alice+Tag@Example.com ') == 'alice+tag@example.com'
    for bad in ('', 'nodot', 'a b.com', '-a.com', 'a..com', 'a.c', 'x' * 300 + '.com', 'a.com/../x', "a.com';id"):
        fail(lambda: mc.domain_ok(bad))
    for bad in ('a\n@example.com', 'a@example.com\nBcc: x@y.com', 'ok@example.com\n.evil', '', 'noat', '@example.com', 'a@', 'a b@example.com', '.a@example.com', 'a.@example.com', 'a..b@example.com', 'a@b', 'a;b@example.com', 'a\n@example.com', ('x' * 70) + '@example.com'):
        fail(lambda: mc.email_ok(bad))
    for bad in ('short', 'x' * 200, 'aaaaaaaaaaaa', 'line\nbreak-pass9', 'nul\x00-passwordx', None, 5):
        fail(lambda: mc.password_ok(bad))
    assert mc.password_ok('correct-horse-9') == 'correct-horse-9'
    assert mc.quota_ok('250') == 250 and mc.quota_ok(0) == 0
    for bad in (-1, 'x', 99999999, None):
        fail(lambda: mc.quota_ok(bad))


def test_model_rules_and_refusals_change_nothing():
    c = conf()
    before = json.dumps(c, sort_keys=True)
    fail(lambda: mc.add_account(c, 'x@other.com', 'h'), 'Add the domain')
    fail(lambda: mc.add_account(c, 'alice@example.com', 'h'), 'already exists')
    fail(lambda: mc.add_domain(c, 'example.com'), 'already')
    fail(lambda: mc.add_domain(c, 'new.com', 'bad selector!'), 'DKIM selector')
    fail(lambda: mc.remove_domain(c, 'example.com'), 'Delete its 2 mailboxes first')
    fail(lambda: mc.remove_domain(c, 'nothere.com'), 'No such domain')
    fail(lambda: mc.add_alias(c, 'alice@example.com', ['bob@example.com']), 'mailbox with that address exists')
    fail(lambda: mc.add_alias(c, 'info@example.com', ['info@example.com']), 'forward to itself')
    fail(lambda: mc.add_alias(c, 'info@example.com', []), '1 to 20')
    fail(lambda: mc.add_alias(c, 'info@example.com', ['nonsense']), 'valid email')
    fail(lambda: mc.add_alias(c, 'info@unknown.com', ['bob@example.com']), 'Add the domain')
    assert json.dumps(c, sort_keys=True) == before
    mc.add_alias(c, 'info@example.com', ['alice@example.com', 'ALICE@example.com', 'bob@example.com'])
    assert c['aliases']['info@example.com'] == ['alice@example.com', 'bob@example.com']                                    # normalised, no duplicates
    fail(lambda: mc.add_account(c, 'info@example.com', 'h'), 'alias with that address exists')
    mc.add_alias(c, '@example.com', ['bob@example.com'])
    del c['accounts']['bob@example.com']
    del c['accounts']['alice@example.com']
    mc.remove_domain(c, 'example.com')
    assert c['domains'] == {} and c['aliases'] == {}                                                                        # its aliases go with it


def test_postfix_maps():
    c = conf()
    mc.add_alias(c, 'info@example.com', ['alice@example.com', 'bob@example.com'])
    mc.add_alias(c, '@example.com', ['bob@example.com'])
    c['accounts']['bob@example.com']['disabled'] = True
    assert mc.render_vdomains(c) == 'example.com OK\n'
    assert mc.render_vmailbox(c) == 'alice@example.com example.com/alice/\n'                                               # a disabled mailbox does not exist for delivery
    va = mc.render_valias(c).splitlines()
    assert va[0] == 'alice@example.com alice@example.com' and '@example.com bob@example.com' in va and 'info@example.com alice@example.com, bob@example.com' in va
    assert 'bob@example.com bob@example.com' not in va                                                                      # a disabled mailbox is not swallowed into a catch-all ...
    assert mc.render_disabled(c) == 'bob@example.com REJECT This mailbox is disabled\n'                                    # ... it is refused
    sl = mc.render_sender_login(c).splitlines()
    assert 'alice@example.com alice@example.com' in sl and 'info@example.com alice@example.com' in sl
    assert not any(l.startswith('@') for l in sl) and not any(l.startswith('bob@') for l in sl)                            # nobody may send as a catch-all or as a disabled user


def test_dovecot_files():
    c = conf()
    c['accounts']['bob@example.com']['disabled'] = True
    users = mc.render_dovecot_users(c)
    assert users == 'alice@example.com:{SHA512-CRYPT}$6$salt$hash:5000:5000::/var/vmail/example.com/alice::userdb_quota_rule=*:storage=500M\n'
    c['accounts']['bob@example.com']['disabled'] = False
    assert 'userdb_quota_rule=*:storage=0' in mc.render_dovecot_users(c)                                                   # quota 0 = unlimited
    conf_text = mc.render_dovecot(c, ipv6=True)
    for must in ('ssl = required', 'disable_plaintext_auth = yes', 'ssl_min_protocol = TLSv1.2', 'mail_location = maildir:~/Maildir', 'inet_listener imaps', 'port = 993',
                 '/var/spool/postfix/private/dovecot-lmtp', '/var/spool/postfix/private/auth', 'passwd-file', 'quota = maildir:User quota', 'listen = *, ::', 'sieve_before'):
        assert must in conf_text, must
    assert 'listen = *\n' in mc.render_dovecot(c, ipv6=False) and '::' not in mc.render_dovecot(c, ipv6=False).split('listen =')[1].split('\n')[0]   # hosts without IPv6 still start
    for line in conf_text.splitlines():                                                                                    # Dovecot's parser wants every block on its own lines (the real parser rejected inline blocks)
        assert not (line.count('{') and line.count('}')), line


def test_postfix_settings():
    c = conf()
    s = dict(mc.postfix_settings(c))
    assert s['myhostname'] == 'mail.example.com' and s['mynetworks'] == '127.0.0.0/8 [::ffff:127.0.0.0]/104 [::1]/128'      # only this machine may relay: never the whole subnet
    assert s['virtual_transport'] == 'lmtp:unix:private/dovecot-lmtp' and s['smtpd_sasl_type'] == 'dovecot' and s['smtpd_tls_auth_only'] == 'yes'
    assert s['smtpd_tls_protocols'] == '>=TLSv1.2' and s['disable_vrfy_command'] == 'yes' and s['message_size_limit'] == str(50 * 1024 * 1024)
    assert s['smtpd_milters'] == 'inet:127.0.0.1:11332' and s['relayhost'] == '' and s['smtp_sasl_auth_enable'] == 'no'
    assert s['smtpd_recipient_restrictions'].split(',')[0].startswith('check_recipient_access hash:') and 'reject_unauth_destination' in s['smtpd_recipient_restrictions']
    c['options'].update(rspamd=False, max_message_mb=25)
    s = dict(mc.postfix_settings(c))
    assert s['smtpd_milters'] == '' and s['message_size_limit'] == str(25 * 1024 * 1024)
    c['relay'] = {'host': 'smtp.relay.example', 'port': 2525, 'user': 'u', 'password': 'p'}
    s = dict(mc.postfix_settings(c))
    assert s['relayhost'] == '[smtp.relay.example]:2525' and s['smtp_sasl_auth_enable'] == 'yes' and s['smtp_tls_security_level'] == 'encrypt'
    assert mc.render_sasl_passwd(c) == '[smtp.relay.example]:2525 u:p\n'
    c['relay'] = {'host': 'open.relay.example', 'port': 25}
    assert dict(mc.postfix_settings(c))['smtp_sasl_auth_enable'] == 'no' and mc.render_sasl_passwd(c) == ''
    submission = dict(mc.MASTER[0][2])
    assert submission['smtpd_sasl_auth_enable'] == 'yes' and submission['smtpd_tls_security_level'] == 'encrypt' and submission['smtpd_sender_restrictions'] == 'reject_sender_login_mismatch'
    assert dict(mc.MASTER[1][2])['smtpd_tls_wrappermode'] == 'yes'


def test_rspamd_levels_and_dkim_maps():
    c = conf()
    mc.add_domain(c, 'second.org', 'sel2')
    r = mc.render_rspamd(c)
    assert 'reject = 15;' in r['actions.conf'] and 'add_header = 6;' in r['actions.conf']
    assert r['../shiplane_dkim_selectors.map'] == 'example.com mail\nsecond.org sel2\n' and 'milter = yes' in r['worker-proxy.inc'] and '11332' in r['worker-proxy.inc']
    assert '$domain.$selector.key' in r['dkim_signing.conf'] and 'sign_authenticated = true' in r['dkim_signing.conf']
    for level, (add, rej) in (('relaxed', (8, 20)), ('strict', (4, 10))):
        c['options']['spam'] = level
        assert 'reject = %d;' % rej in mc.render_rspamd(c)['actions.conf'] and 'add_header = %d;' % add in mc.render_rspamd(c)['actions.conf']
    c['options']['spam'] = 'off'
    assert 'reject = 1000;' in mc.render_rspamd(c)['actions.conf']


def test_autoresponder_script_is_escaped():
    assert mc.render_vacation('a@example.com', None) is None and mc.render_vacation('a@example.com', {'enabled': False}) is None
    s = mc.render_vacation('a@example.com', {'enabled': True, 'subject': 'Out "of" office', 'body': 'Back \\ soon\r\nBye', 'days': 99})
    assert s.startswith('require ["vacation"];') and ':days 30' in s and 'Out \\"of\\" office' in s and '\r' not in s and ':addresses ["a@example.com"]' in s
    assert ':days 7 ' in mc.render_vacation('a@example.com', {'enabled': True, 'days': 0}) and ':days 1 ' in mc.render_vacation('a@example.com', {'enabled': True, 'days': -5})
    assert 'X-Spam' in mc.BEFORE_SIEVE and 'fileinto :create "Junk"' in mc.BEFORE_SIEVE


def test_python_side_validation_and_requests():
    for bad in ('', 'nodot', 'a b.com', "a.com'; id"):
        fail(lambda: mail._clean_domain(bad), 'valid domain')
    fail(lambda: mail._clean_email('nope'), 'valid email')
    fake = FakeServer([('python3', (0, json.dumps({'ok': True, 'notes': []})))])
    mail.run = fake.run
    assert mail.add_domain(CFG, 'Example.COM') == {'ok': True, 'notes': []}
    assert 'example.com' in fake.calls[0]['inp'].decode() and '"action": "domain_add"' in fake.calls[0]['inp'].decode()
    mail.run = FakeServer([('python3', (3, json.dumps({'error': 'That mailbox already exists'})))]).run
    fail(lambda: mail.add_account(CFG, 'alice@example.com', 'long-enough-pw-9'), 'already exists')
    mail.run = lambda *a, **k: (255, 'ssh: connect to host x port 22: Connection refused')
    try:
        mail.status(CFG)
    except RuntimeError as e:
        assert 'Connection refused' in str(e)
    else:
        raise AssertionError
    fail(lambda: mail.remove_domain(CFG, 'example.com', 'nope'), 'Type the domain')
    fail(lambda: mail.account_op(CFG, 'a@example.com', 'explode'), 'Unknown mailbox action')
    assert mail.add_alias.__name__ and mail._clean_email('A@B.CO') == 'a@b.co'


def test_dns_records_and_install_job():
    recs = mail.dns_records('example.com', 'mail.example.com', '203.0.113.5', 'mail', 'PUBKEY')
    by = {(r['type'], r['name']): r for r in recs}
    assert by[('A', 'mail.example.com')]['value'] == '203.0.113.5' and by[('MX', 'example.com')]['value'] == 'mail.example.com' and by[('MX', 'example.com')]['priority'] == 10
    assert by[('TXT', 'example.com')]['value'] == 'v=spf1 mx ~all' and by[('TXT', 'mail._domainkey.example.com')]['value'] == 'v=DKIM1; k=rsa; p=PUBKEY'
    assert by[('TXT', '_dmarc.example.com')]['value'].startswith('v=DMARC1; p=none') and by[('CNAME', 'autoconfig.example.com')]['value'] == 'mail.example.com'
    other = mail.dns_records('example.org', 'mail.example.com', '203.0.113.5', 'mail', 'K')                                  # the mail host is under another domain: no A record to add here
    assert not any(r['type'] == 'A' for r in other)
    started = {}
    real = deploy.start_script_job
    deploy.start_script_job = lambda cfg, text: started.setdefault('text', text) and 'job-1'
    try:
        assert mail.install(CFG, 'Mail.Example.com', {'spam': 'strict', 'evil': 1}) == 'job-1'
        text = started['text']
        assert "'mail.example.com'" in text
        assert '"action": "init"' in text and '"hostname": "mail.example.com"' in text and 'evil' not in text and '"spam": "strict"' in text
        fail(lambda: mail.install(CFG, 'bad host', {}), 'valid domain')
    finally:
        deploy.start_script_job = real


def test_open_ports_never_touches_ssh_and_respects_the_firewall():
    fail(lambda: mail.open_ports(CFG, False, 'yes'), 'Type "mail"')
    real = firewall.status
    firewall.status = lambda cfg: {'installed': True, 'active': False, 'rules': [], 'default_in': 'allow', 'ssh_ports': [22]}
    try:
        out = mail.open_ports(CFG, True)
        assert out['firewall'] == 'off' and out['commands'] == []
        firewall.status = lambda cfg: {'installed': True, 'active': True, 'rules': [], 'default_in': 'deny', 'ssh_ports': [22]}
        out = mail.open_ports(CFG, True)
        assert out['commands'] == ['sudo ufw allow %d/tcp' % p for p in mail.PORTS] and not any(' 22' in c for c in out['commands'])
    finally:
        firewall.status = real


def test_scripts_are_valid():
    import subprocess
    assert subprocess.run(['bash', '-n'], input=open(os.path.join(config.REMOTE, 'mail', 'install.sh'), 'rb').read(), capture_output=True).returncode == 0
    compile(open(os.path.join(config.REMOTE, 'mail', 'mailctl.py')).read().replace('__REQ__', '"{}"'), 'mailctl', 'exec')


if __name__ == '__main__':
    run_tests(dict(globals()))
