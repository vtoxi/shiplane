"""The look of every e-mail Shiplane sends: one responsive HTML layout (inline styles, table based, safe in Gmail and Outlook) plus a plain-text twin.

Every value that comes from outside is escaped; nothing here talks to the network.
"""
import html

BRAND = 'Shiplane'
TONES = {'info': '#4f46e5', 'ok': '#16a34a', 'warn': '#d97706', 'bad': '#dc2626'}     # the accent bar and the button
BAD_WORDS = ('failed', 'down', 'rolled back', 'refused', 'full', 'error', 'unreachable', 'banned')
GOOD_WORDS = ('back up', 'recovered', 'works', 'back to', 'deployed')


def tone_for(text):
    """A colour for an alert, picked from its first line."""
    first = str(text).split('\n')[0].lower()
    if any(w in first for w in BAD_WORDS):
        return 'bad'
    if any(w in first for w in GOOD_WORDS):
        return 'ok'
    return 'info'


def _paragraphs(text):
    return [p.strip() for p in str(text).replace('\r', '').split('\n\n') if p.strip()]


def render(title, body='', code='', button=None, tone='info', preheader='', footer=''):
    """(html, plain text). `body` is plain text (blank line = new paragraph), `code` a one-time code shown large, `button` a (label, https url) pair."""
    color = TONES.get(tone, TONES['info'])
    e = html.escape
    paras = ''.join('<p style="margin:0 0 16px;font-size:15px;line-height:24px;color:#374151;">%s</p>' % e(p).replace('\n', '<br>') for p in _paragraphs(body))
    code_html = ('<div style="margin:8px 0 20px;padding:16px;background:#f3f4f6;border-radius:8px;text-align:center;font-family:SFMono-Regular,Consolas,Menlo,monospace;'
                 'font-size:24px;letter-spacing:3px;color:#111827;">%s</div>' % e(code)) if code else ''
    btn_html = ''
    if button and str(button[1]).startswith('https://'):
        btn_html = ('<p style="margin:8px 0 20px;"><a href="%s" style="display:inline-block;padding:12px 22px;background:%s;color:#ffffff;text-decoration:none;'
                    'border-radius:8px;font-size:15px;font-weight:600;">%s</a></p>' % (e(button[1], quote=True), color, e(button[0])))
    foot = e(footer or 'You are receiving this because e-mail alerts are switched on in your Shiplane settings.')
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"><title>%(title)s</title></head>'
        '<body style="margin:0;padding:0;background:#f4f5f7;">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">%(pre)s</div>'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;"><tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;">'
        '<tr><td style="height:5px;background:%(color)s;font-size:0;line-height:0;">&nbsp;</td></tr>'
        '<tr><td style="padding:22px 28px 0;font-size:14px;font-weight:700;letter-spacing:.4px;color:#111827;">%(brand)s</td></tr>'
        '<tr><td style="padding:14px 28px 6px;"><h1 style="margin:0;font-size:20px;line-height:28px;color:#111827;">%(title)s</h1></td></tr>'
        '<tr><td style="padding:8px 28px 12px;">%(paras)s%(code)s%(btn)s</td></tr>'
        '<tr><td style="padding:16px 28px 24px;border-top:1px solid #e5e7eb;font-size:12px;line-height:18px;color:#6b7280;">%(foot)s</td></tr>'
        '</table></td></tr></table></body></html>'
    ) % {'title': e(title), 'pre': e(preheader or title), 'color': color, 'brand': BRAND, 'paras': paras, 'code': code_html, 'btn': btn_html, 'foot': foot}
    plain = [title, '=' * min(len(title), 60), '']
    plain += [p + '\n' for p in _paragraphs(body)]
    if code:
        plain += ['    ' + code, '']
    if button and str(button[1]).startswith('https://'):
        plain += ['%s: %s' % (button[0], button[1]), '']
    plain += ['--', footer or 'You are receiving this because e-mail alerts are switched on in your Shiplane settings.']
    return page, '\n'.join(plain) + '\n'


def alert(text, footer=''):
    """An alert: its first line is the headline, the rest is the message."""
    head, _, rest = str(text).partition('\n')
    head = head.replace('[Coderunner]', '').replace('[Shiplane]', '').strip() or 'Shiplane alert'
    return render(head, rest.strip() or head, tone=tone_for(text), preheader=head, footer=footer)


def activation(code, days=7):
    return render('Your Shiplane account is ready', 'An account was created for you. Open the app, choose "Set up your account" and enter this one-time code (valid for %d days):' % days,
                  code=code, tone='ok', preheader='Your one-time activation code',
                  footer='If you were not expecting this message you can ignore it: the code does nothing on its own.')
