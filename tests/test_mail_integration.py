"""The mail server against the REAL Postfix and Dovecot: configure it with mailctl.py, then send and read mail over SMTP, submission and IMAP.

It rewrites /etc/postfix and /etc/dovecot, so it only runs when you ask for it, as root, on a throwaway machine or container that has the packages
(postfix dovecot-core dovecot-imapd dovecot-pop3d dovecot-lmtpd dovecot-sieve openssl). Otherwise it is skipped.

    SHIPLANE_MAIL_INTEGRATION=1 python tests/test_mail_integration.py
"""
import imaplib
import os
import shutil
import smtplib
import ssl
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if os.environ.get('SHIPLANE_MAIL_INTEGRATION') != '1' or os.geteuid() != 0 or not (shutil.which('postconf') and shutil.which('doveconf')):
    print('skipped: set SHIPLANE_MAIL_INTEGRATION=1 and run as root on a machine with postfix and dovecot installed (it rewrites their configuration)')
    sys.exit(0)

from fakes import run_tests  # noqa: E402
from coderunner import mail  # noqa: E402

REAL_RUN = subprocess.run
CFG = {'mode': 'local'}
HOST, DOMAIN = 'mail.test.example', 'test.example'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def local_run(cfg, cmd, inp=None, timeout=60):
    r = REAL_RUN([sys.executable, '-'], input=inp, capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).decode()


mail.run = local_run


def raises(fn, text):
    try:
        fn()
    except ValueError as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected ' + text)


def sh(*cmd, **kw):
    return REAL_RUN(list(cmd), capture_output=True, text=True, **kw)


def listening(port):
    import socket
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(('127.0.0.1', port)) == 0


def start_services():
    sh('postconf', '-e', 'maillog_file=/var/log/postfix-test.log')
    if not listening(993):
        subprocess.Popen(['dovecot'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    if not listening(25):
        sh('postfix', 'start')
    else:
        sh('postfix', 'reload')
    for _ in range(40):
        if listening(25) and listening(993) and listening(587):
            return
        time.sleep(0.25)
    raise AssertionError('the mail services did not start')


def reload_all():
    sh('postfix', 'reload')
    sh('doveadm', 'reload')
    time.sleep(0.8)


def imap(user, password):
    c = imaplib.IMAP4_SSL('127.0.0.1', 993, ssl_context=CTX)
    c.login(user, password)
    return c


def inbox_subjects(user, password, folder='INBOX', wait=6, want=None):
    """Subjects in the mailbox. With `want`, keeps looking (up to `wait` seconds) until that subject has arrived."""
    end = time.time() + wait
    subjects = []
    while time.time() < end:
        c = imap(user, password)
        typ, _ = c.select(folder, readonly=True)
        subjects = []
        if typ == 'OK':
            typ, data = c.search(None, 'ALL')
            for num in data[0].split():
                _, msg = c.fetch(num, '(BODY.PEEK[HEADER.FIELDS (SUBJECT)])')
                subjects.append(msg[0][1].decode().strip().replace('Subject: ', ''))
        c.logout()
        if (want in subjects) if want else subjects:
            break
        time.sleep(0.4)
    return subjects


def send_in(to, subject, sender='someone@elsewhere.example'):
    """Mail arriving from the internet on port 25."""
    with smtplib.SMTP('127.0.0.1', 25, timeout=15) as s:
        s.ehlo('client.example')
        return s.sendmail(sender, [to], 'From: %s\r\nTo: %s\r\nSubject: %s\r\n\r\nbody of %s\r\n' % (sender, to, subject, subject))


def submit(user, password, sender, to, subject):
    """Mail sent by a user through the submission port (587, STARTTLS, login required)."""
    with smtplib.SMTP('127.0.0.1', 587, timeout=15) as s:
        s.ehlo('client.example')
        s.starttls(context=CTX)
        s.ehlo('client.example')
        s.login(user, password)
        return s.sendmail(sender, [to], 'From: %s\r\nTo: %s\r\nSubject: %s\r\n\r\nbody\r\n' % (sender, to, subject))


def test_01_configure_and_start():
    for f in ('/etc/shiplane-mail/mail.json',):
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree('/var/vmail', ignore_errors=True)
    os.makedirs('/var/vmail', exist_ok=True)
    sh('chown', 'vmail:vmail', '/var/vmail')
    out = mail.call(CFG, 'init', hostname=HOST)
    assert out['ok']
    start_services()
    d = mail.add_domain(CFG, DOMAIN)
    assert d['domain'] == DOMAIN and len(d['dkim_public']) > 300                                                         # a DKIM key was made
    mail.add_account(CFG, 'alice@' + DOMAIN, 'correct-horse-9', 100, 'Alice')
    mail.add_account(CFG, 'bob@' + DOMAIN, 'battery-staple-9', 100)
    reload_all()
    st = mail.status(CFG)
    assert st['configured'] and st['services']['postfix']['ports']['25'] and st['services']['dovecot']['ports']['993'] and st['domains'] == 1 and st['accounts'] == 2


def test_02_incoming_mail_reaches_the_mailbox_and_imap_login_is_enforced():
    send_in('alice@' + DOMAIN, 'hello alice')
    assert inbox_subjects('alice@' + DOMAIN, 'correct-horse-9') == ['hello alice']                                     # SMTP -> Postfix -> LMTP -> Dovecot -> IMAP
    try:
        imap('alice@' + DOMAIN, 'wrong-password')
    except imaplib.IMAP4.error:
        pass
    else:
        raise AssertionError('a wrong password was accepted')
    assert os.path.isdir('/var/vmail/%s/alice/Maildir' % DOMAIN) and os.stat('/var/vmail/%s/alice/Maildir' % DOMAIN).st_uid == 5000   # owned by the mail user


def outside_address():
    """This machine's own non-loopback address: connecting to it makes the source address a stranger's, not 127.0.0.1."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(('192.0.2.1', 9))
        return s.getsockname()[0]


def test_03_unknown_users_and_open_relaying_are_refused():
    for rcpt in ('nobody@' + DOMAIN,):
        try:
            send_in(rcpt, 'x')
        except smtplib.SMTPRecipientsRefused as e:
            assert '550' in str(e) or '5.1.1' in str(e), str(e)
        else:
            raise AssertionError('mail for an unknown user was accepted')
    ip = outside_address()
    if ip.startswith('127.'):
        print('(no non-loopback address here: the open-relay check is skipped)')
        return
    try:                                                                                                                # a stranger (source address is not loopback), not logged in, not our domain
        with smtplib.SMTP(ip, 25, timeout=15) as s:
            s.ehlo('client.example')
            s.sendmail('x@elsewhere.example', ['victim@gmail.example'], 'Subject: relay me\r\n\r\nx')
    except smtplib.SMTPRecipientsRefused as e:
        assert any(code in str(e) for code in ('554', '5.7.1', '450', '454')), str(e)
    else:
        raise AssertionError('this server relays for strangers (open relay)')


def test_04_submission_needs_tls_and_login_and_the_right_sender():
    s = smtplib.SMTP('127.0.0.1', 587, timeout=10)
    s.ehlo('c')
    assert 'auth' not in s.esmtp_features                                                                              # no login offered before STARTTLS
    s.quit()
    submit('alice@' + DOMAIN, 'correct-horse-9', 'alice@' + DOMAIN, 'bob@' + DOMAIN, 'from alice to bob')
    assert inbox_subjects('bob@' + DOMAIN, 'battery-staple-9') == ['from alice to bob']
    try:
        submit('alice@' + DOMAIN, 'correct-horse-9', 'bob@' + DOMAIN, 'bob@' + DOMAIN, 'spoofed')                       # alice pretends to be bob
    except (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused) as e:                                               # Postfix applies the sender check at RCPT
        assert '553' in str(e) or '5.7.1' in str(e), str(e)
    else:
        raise AssertionError('a user could send as somebody else')
    try:
        submit('alice@' + DOMAIN, 'wrong-password-9', 'alice@' + DOMAIN, 'bob@' + DOMAIN, 'x')
    except smtplib.SMTPAuthenticationError:
        pass
    else:
        raise AssertionError('submission accepted a wrong password')
    with smtplib.SMTP_SSL('127.0.0.1', 465, context=CTX, timeout=10) as s:                                              # implicit TLS port works too
        s.login('alice@' + DOMAIN, 'correct-horse-9')


def test_05_aliases_forwarders_and_catch_all():
    mail.add_alias(CFG, 'info@' + DOMAIN, ['alice@' + DOMAIN, 'bob@' + DOMAIN])
    reload_all()
    send_in('info@' + DOMAIN, 'to the info alias')
    assert 'to the info alias' in inbox_subjects('alice@' + DOMAIN, 'correct-horse-9', wait=8, want='to the info alias')
    assert 'to the info alias' in inbox_subjects('bob@' + DOMAIN, 'battery-staple-9', wait=8, want='to the info alias')
    mail.add_alias(CFG, '@' + DOMAIN, ['bob@' + DOMAIN])                                                               # catch-all
    reload_all()
    send_in('anything-at-all@' + DOMAIN, 'caught by the catch-all')
    assert 'caught by the catch-all' in inbox_subjects('bob@' + DOMAIN, 'battery-staple-9', wait=8, want='caught by the catch-all')
    send_in('alice@' + DOMAIN, 'real mailbox wins over catch-all')                                                     # a real mailbox is not swallowed by it
    assert 'real mailbox wins over catch-all' in inbox_subjects('alice@' + DOMAIN, 'correct-horse-9', wait=8, want='real mailbox wins over catch-all')
    mail.remove_alias(CFG, '@' + DOMAIN)
    reload_all()
    try:
        send_in('anything-at-all@' + DOMAIN, 'now refused')
    except smtplib.SMTPRecipientsRefused:
        pass
    else:
        raise AssertionError('catch-all still active after removal')
    # a user may send as an alias that points at them
    submit('alice@' + DOMAIN, 'correct-horse-9', 'info@' + DOMAIN, 'bob@' + DOMAIN, 'sent as the alias')
    assert 'sent as the alias' in inbox_subjects('bob@' + DOMAIN, 'battery-staple-9', wait=8, want='sent as the alias')


def test_06_password_change_disable_enable_and_delete():
    mail.account_op(CFG, 'alice@' + DOMAIN, 'password', password='a-brand-new-pass-9')
    try:
        imap('alice@' + DOMAIN, 'correct-horse-9')
    except imaplib.IMAP4.error:
        pass
    else:
        raise AssertionError('the old password still works')
    imap('alice@' + DOMAIN, 'a-brand-new-pass-9').logout()
    mail.account_op(CFG, 'alice@' + DOMAIN, 'disable')
    reload_all()
    try:
        imap('alice@' + DOMAIN, 'a-brand-new-pass-9')
    except imaplib.IMAP4.error:
        pass
    else:
        raise AssertionError('a disabled mailbox could log in')
    try:
        send_in('alice@' + DOMAIN, 'to a disabled mailbox')
    except smtplib.SMTPRecipientsRefused:
        pass
    else:
        raise AssertionError('mail was accepted for a disabled mailbox')
    mail.account_op(CFG, 'alice@' + DOMAIN, 'enable')
    reload_all()
    imap('alice@' + DOMAIN, 'a-brand-new-pass-9').logout()
    mail.add_account(CFG, 'temp@' + DOMAIN, 'temporary-pass-9', 10)
    mail.account_op(CFG, 'temp@' + DOMAIN, 'delete', delete_mail=True)
    assert not os.path.exists('/var/vmail/%s/temp' % DOMAIN)


def test_07_autoresponder_is_a_valid_sieve_script_and_delivery_still_works():
    mail.account_op(CFG, 'bob@' + DOMAIN, 'autoreply', autoreply={'enabled': True, 'subject': 'Away until Monday', 'body': 'I am away; "quoted" and \\backslash fine.', 'days': 2})
    script = '/var/vmail/%s/bob/.dovecot.sieve' % DOMAIN
    assert os.path.exists(script) and 'vacation' in open(script).read()
    assert sh('sievec', '-d', '-', script).returncode == 0 or sh('sievec', script).returncode == 0                      # Pigeonhole accepts the script
    send_in('bob@' + DOMAIN, 'while bob is away')
    assert 'while bob is away' in inbox_subjects('bob@' + DOMAIN, 'battery-staple-9', wait=8, want='while bob is away')                           # delivered, and a reply is queued
    mail.account_op(CFG, 'bob@' + DOMAIN, 'autoreply', autoreply={'enabled': False})
    assert not os.path.exists(script)


def test_08_a_refused_change_leaves_everything_as_it_was():
    before = mail.view(CFG)
    raises(lambda: mail.add_account(CFG, 'x@nodomain.example', 'long-enough-pw-9'), 'Add the domain')
    raises(lambda: mail.add_account(CFG, 'alice@' + DOMAIN, 'long-enough-pw-9'), 'already exists')
    raises(lambda: mail.add_account(CFG, 'weak@' + DOMAIN, 'short'), '10 to 128')
    raises(lambda: mail.remove_domain(CFG, DOMAIN, DOMAIN), 'Delete its')
    raises(lambda: mail.remove_domain(CFG, DOMAIN, 'nope'), 'Type the domain')
    assert mail.view(CFG) == before
    assert sh('doveconf', '-n').returncode == 0 and sh('postfix', 'check').returncode == 0


def test_09_the_status_the_queue_and_the_logs():
    st = mail.status(CFG)
    assert st['cert']['self_signed'] is True and st['hostname'] == HOST
    assert mail.queue(CFG, 'list')['items'] is not None
    assert mail.logs(CFG, 50)['lines'] is not None
    assert mail.port25(CFG)['ok'] in (True, False)


def rspamd_up():
    return listening(11332)


def test_10_outgoing_mail_is_signed_with_dkim():
    if not rspamd_up():
        print('(rspamd is not running here: the DKIM check is skipped)')
        return
    sh('postfix', 'reload')
    time.sleep(1)
    submit('alice@' + DOMAIN, 'a-brand-new-pass-9', 'alice@' + DOMAIN, 'friend@unreachable-example.invalid', 'please sign me')
    time.sleep(2)
    q = sh('postqueue', '-j').stdout.splitlines()
    ids = [__import__('json').loads(l)['queue_id'] for l in q if 'unreachable-example.invalid' in l]
    assert ids, 'the outgoing message is not in the queue'
    raw = sh('postcat', '-q', ids[0]).stdout
    assert 'DKIM-Signature:' in raw and 'd=test.example' in raw and 's=mail' in raw, raw[:600]                          # signed for the sending domain with its selector


def test_11_mail_rspamd_flags_as_spam_goes_to_junk():
    if not rspamd_up():
        print('(rspamd is not running here: the spam check is skipped)')
        return
    path = '/etc/rspamd/local.d/actions.conf'
    original = open(path).read()
    try:
        open(path, 'w').write('reject = 1000;\nadd_header = -1000;\ngreylist = 1000;\n')                                # every message now counts as "add the spam header"
        sh('pkill', '-HUP', '-x', 'rspamd')
        time.sleep(5)
        send_in('bob@' + DOMAIN, 'flagged as spam')
        junk = inbox_subjects('bob@' + DOMAIN, 'battery-staple-9', folder='Junk', wait=10, want='flagged as spam')
        assert 'flagged as spam' in junk, junk
        assert 'flagged as spam' not in inbox_subjects('bob@' + DOMAIN, 'battery-staple-9', wait=1)
    finally:
        open(path, 'w').write(original)
        sh('pkill', '-HUP', '-x', 'rspamd')
    mail.set_options(CFG, {'spam': 'strict'})                                                                           # a level change rewrites the thresholds
    assert 'reject = 10;' in open(path).read() and 'add_header = 4;' in open(path).read()
    mail.set_options(CFG, {'spam': 'off'})
    assert 'reject = 1000;' in open(path).read()
    mail.set_options(CFG, {'spam': 'standard'})


if __name__ == '__main__':
    run_tests(dict(globals()))
