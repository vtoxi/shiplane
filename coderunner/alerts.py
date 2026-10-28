"""Alerts to your phone or inbox: Telegram bot, Slack, Discord, a generic JSON webhook (optionally HMAC-signed) and/or e-mail through Gmail with an app password.
Secrets live in data/settings.json and are never sent back to the browser."""
import datetime
import hashlib
import hmac
import json
import os
import re
import smtplib
import ssl
import threading
from email.message import EmailMessage
import urllib.request
from urllib.parse import urlparse

from . import activity, mailtpl, settings

TELEGRAM = os.environ.get('ALERT_TELEGRAM_BASE', 'https://api.telegram.org')
SMTP_HOST, SMTP_PORT = os.environ.get('ALERT_SMTP_HOST', 'smtp.gmail.com'), int(os.environ.get('ALERT_SMTP_PORT', '587'))
OWNERS = {}          # site name -> who deployed it; filled by the uptime round so a site's alerts can also reach that person
SLACK_RE = re.compile(r'^https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{10,200}$')
DISCORD_RE = re.compile(r'^https://(discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/\d{5,30}/[A-Za-z0-9_-]{20,200}$')
EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$')


def public():
    c = settings.get('alerts')
    t, w = c.get('telegram') or {}, c.get('webhook') or {}
    e = c.get('email') or {}
    return {'telegram': {'configured': bool(t.get('token') and t.get('chat')), 'chat': t.get('chat', '')},
            'slack': {'configured': bool((c.get('slack') or {}).get('url'))},
            'discord': {'configured': bool((c.get('discord') or {}).get('url'))},
            'webhook': {'configured': bool(w.get('url')), 'host': urlparse(w.get('url', '')).hostname or '', 'signed': bool(w.get('secret'))},
            'email': {'configured': bool(e.get('user') and e.get('password')), 'user': e.get('user', ''), 'to': e.get('to', '')}}


def save(telegram_token=None, telegram_chat=None, webhook_url=None, email_user=None, email_password=None, email_to=None,
         slack_url=None, discord_url=None, webhook_secret=None):
    """None keeps the stored value, an empty string removes it."""
    c = settings.get('alerts')
    t, w = dict(c.get('telegram') or {}), dict(c.get('webhook') or {})
    sl, dc = dict(c.get('slack') or {}), dict(c.get('discord') or {})
    if slack_url is not None:
        u = str(slack_url).strip()
        if u and not SLACK_RE.match(u):
            raise ValueError('A Slack incoming webhook starts with https://hooks.slack.com/services/')
        sl['url'] = u
    if discord_url is not None:
        u = str(discord_url).strip()
        if u and not DISCORD_RE.match(u):
            raise ValueError('A Discord webhook looks like https://discord.com/api/webhooks/<id>/<token>')
        dc['url'] = u
    if webhook_secret is not None:
        sec = str(webhook_secret).strip()
        if sec and not re.fullmatch(r'[!-~]{16,200}', sec):
            raise ValueError('The signing secret must be 16 to 200 characters without spaces')
        w['secret'] = sec
    if telegram_token is not None:
        tok = str(telegram_token).strip()
        if tok and not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{30,}', tok):
            raise ValueError('That does not look like a Telegram bot token (from @BotFather)')
        t['token'] = tok
    if telegram_chat is not None:
        chat = str(telegram_chat).strip()
        if chat and not re.fullmatch(r'-?\d{3,}|@[A-Za-z0-9_]{4,}', chat):
            raise ValueError('Chat id: a number (or @channelname)')
        t['chat'] = chat
    if webhook_url is not None:
        u = str(webhook_url).strip()
        if u and (not u.startswith('https://') or not urlparse(u).hostname):
            raise ValueError('The webhook address must start with https://')
        w['url'] = u
        if not u:
            w['secret'] = ''
    e = dict(c.get('email') or {})
    if email_user is not None:
        u = str(email_user).strip()
        if u and not EMAIL_RE.match(u):
            raise ValueError('The e-mail address looks wrong')
        e['user'] = u
        if not u:
            e['password'] = ''
    if email_password is not None:
        pw = re.sub(r'\s', '', str(email_password))               # Google shows the 16 letters in groups of four
        if pw and not re.fullmatch(r'[A-Za-z0-9]{16}', pw):
            raise ValueError('A Google app password is 16 letters (create one at myaccount.google.com/apppasswords; it needs 2-step verification)')
        e['password'] = pw
    if email_to is not None:
        to = str(email_to).strip()
        if to and not EMAIL_RE.match(to):
            raise ValueError('The address to send alerts to looks wrong')
        e['to'] = to
    settings.put('alerts', {'telegram': t, 'slack': sl, 'discord': dc, 'webhook': w, 'email': e})


def _post(url, payload, secret=''):
    data = json.dumps(payload).encode()
    headers = {'Content-Type': 'application/json', 'User-Agent': 'coderunner'}
    if secret:   # the receiver recomputes HMAC-SHA256 over the raw body to know the alert came from this Shiplane
        headers['X-Shiplane-Signature'] = 'sha256=' + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read(1)


def owner_email(site):
    """The e-mail address of whoever deployed the site, or '' (a plain user name, or nobody)."""
    v = str(OWNERS.get(site) or '').strip()
    return v if EMAIL_RE.match(v) else ''


def send_mail(to, subject, text, html=None, cc=None):
    """One message through the saved Gmail account. Raises ValueError when e-mail is not set up, otherwise whatever SMTP says (without the password)."""
    e = settings.get('alerts').get('email') or {}
    if not (e.get('user') and e.get('password')):
        raise ValueError('E-mail is not set up: add your Google address and app password on Integrations')
    to = str(to or '').strip()
    if not EMAIL_RE.match(to):
        raise ValueError('The recipient address looks wrong')
    msg = EmailMessage()
    cc = str(cc or '').strip()
    if cc and not EMAIL_RE.match(cc):
        raise ValueError('The CC address looks wrong')
    if cc and cc.lower() != to.lower():
        msg['Cc'] = cc
    msg['From'], msg['To'], msg['Subject'] = e['user'], to, re.sub(r'[\r\n]+', ' ', subject)[:200]
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(e['user'], e['password'])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError('Google refused the sign-in: check the address and the app password (a normal account password does not work)')
    except Exception as ex:
        raise RuntimeError(str(ex).replace(e['password'], '***')[:200])


def deliver(text, site=''):
    """Send to every configured channel. Returns [(channel, error or None)]."""
    c = settings.get('alerts')
    t, w = c.get('telegram') or {}, c.get('webhook') or {}
    out = []
    em = c.get('email') or {}
    if em.get('user') and em.get('password'):
        try:
            admin = em.get('to') or em['user']
            owner = owner_email(site)
            # a site's person gets its alerts, and the owner of this Shiplane is copied; without a known person the owner gets them alone
            to, cc = (owner, admin) if owner else (admin, None)
            page, plain = mailtpl.alert(text, footer=('You are receiving this because you deployed %s. The owner of this Shiplane is copied.' % site) if owner else '')
            send_mail(to, text.split('\n')[0][:80], plain, page, cc)
            out.append(('email', None))
        except Exception as ex:
            out.append(('email', str(ex)[:200]))
    sl, dc = (c.get('slack') or {}).get('url'), (c.get('discord') or {}).get('url')
    event = {'event': 'alert', 'text': text, 'title': text.split('\n')[0][:200], 'site': site,
             'time': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'content': text}   # text: Slack-style receivers, content: Discord/Mattermost
    for name, ok_cfg, url, payload, secret in (
            ('telegram', t.get('token') and t.get('chat'), '%s/bot%s/sendMessage' % (TELEGRAM, t.get('token')), {'chat_id': t.get('chat'), 'text': text}, ''),
            ('slack', sl, sl, {'text': text}, ''),
            ('discord', dc, dc, {'content': text[:2000], 'username': 'Shiplane'}, ''),
            ('webhook', w.get('url'), w.get('url'), event, w.get('secret') or '')):
        if not ok_cfg:
            continue
        try:
            _post(url, payload, secret)
            out.append((name, None))
        except Exception as e:
            out.append((name, str(e).replace(t.get('token') or '\0', '***')[:200]))
    return out


def notify(text, site=''):
    """Fire and forget; a failure is written to Activity so a broken channel does not go unnoticed."""
    p = public()
    if not any(v['configured'] for v in p.values()):
        return

    def run():
        for name, err in deliver(text, site):
            if err:
                activity.log('alert-failed', name, err, 'alerts', False)

    threading.Thread(target=run, daemon=True).start()


def test():
    res = deliver('[Shiplane] Test alert: this channel works.')
    if not res:
        raise ValueError('Set up Telegram, Slack, Discord, a webhook or e-mail first')
    return [{'channel': n, 'ok': e is None, 'error': e} for n, e in res]
