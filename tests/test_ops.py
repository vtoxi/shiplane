"""Unit checks for alerts, backup schedule/retention and resource alerts. No network beyond localhost, no SSH."""
import datetime
import http.server
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
from coderunner import alerts, backups, dbops, monitor, settings  # noqa: E402

fails = 0


def check(name, ok, extra=''):
    global fails
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok or not extra else ' | ' + str(extra)[:300]))
    fails += 0 if ok else 1


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except ValueError:
        return True
    return False


# ---- alerts against a fake Telegram + webhook
got = []
sigs = []


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        got.append((self.path, json.loads(raw)))
        sigs.append((self.path, self.headers.get('X-Shiplane-Signature'), raw))
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'{}')


srv = http.server.HTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d' % srv.server_port
TOKEN = '123456789:' + 'A' * 35
alerts.TELEGRAM = base

check('nothing configured: notify does nothing and test() says so', alerts.notify('x') is None and raises(alerts.test))
check('bad bot token refused', raises(alerts.save, telegram_token='nope'))
check('bad chat id refused', raises(alerts.save, telegram_chat='not a chat'))
check('webhook must be https', raises(alerts.save, webhook_url='http://example.com/h'))
alerts.save(telegram_token=TOKEN, telegram_chat='987654321')
check('telegram saved', alerts.public()['telegram'] == {'configured': True, 'chat': '987654321'})
check('the token is never in what the browser gets', TOKEN not in json.dumps(alerts.public()))
check('settings file is owner-only', os.name == 'nt' or oct(os.stat(settings._file()).st_mode)[-3:] == '600')
alerts.save(webhook_url='https://hooks.example.com/abc')
check('webhook host shown, not the secret path', alerts.public()['webhook'] == {'configured': True, 'host': 'hooks.example.com', 'signed': False})
check('slack address checked', raises(alerts.save, slack_url='https://example.com/services/x'))
check('discord address checked', raises(alerts.save, discord_url='https://discord.com/api/other/1'))
check('short signing secret refused', raises(alerts.save, webhook_secret='short'))
alerts.save(slack_url='https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXX', discord_url='https://discord.com/api/webhooks/123456/' + 'a' * 30, webhook_secret='s' * 20)
pub = alerts.public()
check('slack, discord and signing saved; secrets not shown', pub['slack']['configured'] and pub['discord']['configured'] and pub['webhook']['signed'] and 'XXXXXXXXXXXX' not in json.dumps(pub) and 's' * 20 not in json.dumps(pub))
c = settings.get('alerts')
c['webhook']['url'] = base + '/hook'   # delivery check against the local fake (the save() rule is for the UI)
c['slack']['url'], c['discord']['url'] = base + '/slack', base + '/discord'
settings.put('alerts', c)
res = alerts.test()
check('test alert reaches both channels', sorted(r['channel'] for r in res) == ['discord', 'slack', 'telegram', 'webhook'] and all(r['ok'] for r in res), res)
tg = [b for p, b in got if p == '/bot%s/sendMessage' % TOKEN]
hk = [b for p, b in got if p == '/hook']
check('telegram payload', tg and tg[0]['chat_id'] == '987654321' and 'Test alert' in tg[0]['text'])
check('webhook payload works for Slack (text) and Discord (content)', hk and hk[0]['text'] == hk[0]['content'])
check('webhook payload is structured', hk[0]['event'] == 'alert' and hk[0]['time'].endswith('Z') and 'Test alert' in hk[0]['title'])
import hashlib, hmac  # noqa: E402
sig = [(h, raw) for p, h, raw in sigs if p == '/hook'][0]
check('webhook is signed with HMAC-SHA256 over the body', sig[0] == 'sha256=' + hmac.new(b's' * 20, sig[1], hashlib.sha256).hexdigest())
check('slack and discord are not signed', all(h is None for p, h, _ in sigs if p != '/hook'))
check('slack payload', [b for p, b in got if p == '/slack'][0] == {'text': hk[0]['text']})
check('discord payload', [b for p, b in got if p == '/discord'][0]['content'] == hk[0]['text'])
alerts.save(webhook_url='')
check('removing the webhook drops its secret', not settings.get('alerts')['webhook'].get('secret'))
c = settings.get('alerts'); c['webhook']['url'] = base + '/hook'; settings.put('alerts', c)
alerts.save(telegram_token='', telegram_chat='')
check('removing telegram keeps the webhook', not alerts.public()['telegram']['configured'] and alerts.public()['webhook']['configured'])
srv.shutdown()
alerts.save(telegram_token=TOKEN, telegram_chat='987654321')
alerts.TELEGRAM = 'http://127.0.0.1:1'   # nothing listens
errs = [r for r in alerts.deliver('x') if r[1]]
check('a failing channel reports an error without leaking the token', errs and all(TOKEN not in e for _, e in errs), errs)

# ---- backup schedule
check('schedule validation', raises(backups.save, True, 24, 7) and raises(backups.save, True, 3, 0) and raises(backups.save, True, 3, 91))
check('schedule saved', backups.save(True, 4, 5)['hour'] == 4 and backups.get()['keep'] == 5 and backups.get()['enabled'])

deleted = []
dbops.list_all = lambda cfg: {'databases': [], 'backups': [{'file': 'shop-2026010%d-030000.sql.gz' % i} for i in range(1, 6)] + [{'file': 'blog-20260101-030000.sql.gz'}, {'file': 'blog-20260102-030000.sql.gz'}]}
dbops.delete_backup = lambda cfg, f: deleted.append(f)
n = backups.prune({}, 3)
check('retention removes the oldest beyond the limit, per database', n == 2 and sorted(deleted) == ['shop-20260101-030000.sql.gz', 'shop-20260102-030000.sql.gz'], deleted)

runs = []
backups.run_all = lambda cfg, say=print: (runs.append(1) or (True, 'ok'))
at = lambda day, hour: datetime.datetime(2026, 3, day, hour, 5)
backups.save(False, 4, 5)
check('disabled: nothing runs', backups.tick({}, at(1, 4)) is False and not runs)
backups.save(True, 4, 5)
check('wrong hour: nothing runs', backups.tick({}, at(1, 9)) is False and not runs)
check('at the hour: runs', backups.tick({}, at(1, 4)) is True and len(runs) == 1)
check('same day again: does not run twice', backups.tick({}, at(1, 4)) is False and len(runs) == 1)
check('next day: runs again', backups.tick({}, at(2, 4)) is True and len(runs) == 2)

check('restore refuses a backup of another database', raises(backups.start_restore, {}, 'me', 'shop', 'blog-20260101-030000.sql.gz', 'shop'))
check('restore refuses a bad file name', raises(backups.start_restore, {}, 'me', 'shop', '../etc/passwd', 'shop'))
check('restore needs the name typed', raises(backups.start_restore, {}, 'me', 'shop', 'shop-20260101-030000.sql.gz', 'wrong'))
check('restore refuses system databases', raises(backups.start_restore, {}, 'me', 'postgres', 'postgres-20260101-030000.sql.gz', 'postgres'))

# ---- resource alerts: two high readings, once, then a recovery message
sent = []
monitor.alerts.notify = lambda text, site='': sent.append(text)
cfg = {'host': 'srv'}
mk = lambda disk, ram: (lambda c: {'disk_used': disk, 'disk_total': 100, 'ram_used': ram, 'ram_total': 100})
monitor.check_resources(cfg, mk(90, 10))
check('one high reading is not an alert', not sent)
monitor.check_resources(cfg, mk(91, 10))
check('two high readings alert once', len(sent) == 1 and 'Disk' in sent[0])
monitor.check_resources(cfg, mk(95, 10))
check('no repeat while it stays high', len(sent) == 1)
monitor.check_resources(cfg, mk(70, 10))
check('recovery is announced', len(sent) == 2 and 'back to' in sent[1])
monitor.check_resources(cfg, mk(10, 95))
monitor.check_resources(cfg, mk(10, 96))
check('memory alerts too', len(sent) == 3 and 'Memory' in sent[2])

# ---- e-mail through Gmail with an app password (a fake SMTP client, nothing is sent)
import smtplib  # noqa: E402
SENT, LOGINS = [], []


class FakeSMTP:
    fail = None

    def __init__(self, host, port, timeout=0):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        SENT.append(('tls', self.host, self.port))

    def login(self, user, pw):
        if FakeSMTP.fail == 'auth':
            raise smtplib.SMTPAuthenticationError(535, b'bad credentials')
        if FakeSMTP.fail == 'boom':
            raise OSError('connection to ' + pw + ' lost')
        LOGINS.append((user, pw))

    def send_message(self, msg):
        html = msg.get_body(('html',))
        SENT.append(('mail', msg['From'], msg['To'], msg['Subject'], msg.get_body(('plain',)).get_content(), html.get_content() if html else '', msg['Cc']))


alerts.smtplib.SMTP = FakeSMTP
_sf = os.path.join(os.environ['CODERUNNER_DATA'], 'settings.json')
if os.path.exists(_sf):
    os.remove(_sf)
check('e-mail not set up: sending says so', raises(alerts.send_mail, 'a@example.com', 's', 't') and not alerts.public()['email']['configured'])
check('bad address refused', raises(alerts.save, email_user='nope'))
check('a normal password is refused', raises(alerts.save, email_user='me@gmail.com', email_password='my normal password!'))
alerts.save(email_user='me@gmail.com', email_password='abcd efgh ijkl mnop')
pub = alerts.public()['email']
check('e-mail saved: shown without the password', pub == {'configured': True, 'user': 'me@gmail.com', 'to': ''} and 'abcdefghijklmnop' not in json.dumps(alerts.public()), pub)
check('the app password is stored without spaces', settings.get('alerts')['email']['password'] == 'abcdefghijklmnop')
check('a bad recipient is refused', raises(alerts.save, email_to='x'))
alerts.save(email_to='ops@example.com')
res = alerts.test()
check('test alert is e-mailed to the chosen address over TLS', res == [{'channel': 'email', 'ok': True, 'error': None}] and SENT[0][0] == 'tls' and SENT[1][2] == 'ops@example.com' and LOGINS[-1] == ('me@gmail.com', 'abcdefghijklmnop'), (res, SENT))
SENT.clear()
alerts.save(email_to='')
alerts.deliver('Subject line\nsecond line')
check('without a recipient it goes to yourself; the first line is the subject', SENT[1][2] == 'me@gmail.com' and SENT[1][3] == 'Subject line', SENT)
check('alerts go out as HTML and plain text', '<html' in SENT[1][5] and 'Subject line' in SENT[1][5] and 'Subject line' in SENT[1][4], SENT[1])
SENT.clear()
alerts.send_mail('a@example.com', 'x\r\nBcc: evil@example.com', 'body')
check('a subject cannot carry extra headers', '\n' not in SENT[-1][3] and '\r' not in SENT[-1][3], SENT[-1])
FakeSMTP.fail = 'auth'
check('a refused sign-in is explained', 'app password' in alerts.test()[0]['error'] and not alerts.test()[0]['ok'])
FakeSMTP.fail = 'boom'
err = alerts.test()[0]['error']
check('other errors never contain the app password', 'abcdefghijklmnop' not in err and '***' in err, err)
FakeSMTP.fail = None
check('a bad recipient is refused when sending', raises(alerts.send_mail, 'nope', 's', 't'))
SENT.clear()
alerts.save(email_to='boss@example.com')
alerts.OWNERS.update({'shop': 'owner@example.com', 'blog': 'deployer', 'old': ''})
alerts.deliver('[Coderunner] DOWN: shop (shop.example.com) failed two checks in a row', 'shop')
mail = [x for x in SENT if x[0] == 'mail'][-1]
check('the person who deployed the site gets its alerts, the owner is copied', mail[2] == 'owner@example.com' and mail[6] == 'boss@example.com' and 'you deployed shop' in mail[4], mail)
SENT.clear()
alerts.deliver('[Coderunner] DOWN: blog (blog.example.com) failed two checks in a row', 'blog')
alerts.deliver('[Coderunner] DOWN: old (old.example.com) failed two checks in a row', 'old')
alerts.deliver('Disk is nearly full')
mails = [x for x in SENT if x[0] == 'mail']
check('a plain user name, no owner, or no site: the owner gets it alone, with no CC', len(mails) == 3 and all(m[2] == 'boss@example.com' and m[6] is None for m in mails), mails)
SENT.clear()
alerts.OWNERS['mine'] = 'boss@example.com'
alerts.deliver('[Coderunner] DOWN: mine (x.example.com) failed', 'mine')
check('when the owner deployed it, there is no copy to themselves', [x for x in SENT if x[0] == 'mail'][-1][6] is None)
check('a bad CC address is refused', raises(alerts.send_mail, 'a@example.com', 's', 't', None, 'nope'))
check('owner_email only accepts addresses', alerts.owner_email('shop') == 'owner@example.com' and alerts.owner_email('blog') == '' and alerts.owner_email('ghost') == '')
alerts.OWNERS.clear()
alerts.save(email_user='')
check('removing the address removes the password too', not alerts.public()['email']['configured'] and settings.get('alerts')['email']['password'] == '')

print('\nALL PASSED' if not fails else '\n%d FAILED' % fails)
sys.exit(1 if fails else 0)
