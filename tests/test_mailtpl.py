"""The e-mail template: escaping, tones, the code box, the button, the plain-text twin (no network).

    python tests/test_mailtpl.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import mailtpl  # noqa: E402


def test_everything_from_outside_is_escaped():
    page, plain = mailtpl.render('<script>x</script>', 'a < b & "c"\nsecond line\n\nnew paragraph', code='<b>1</b>', button=('Open "it"', 'https://x.example.com/?a=1&b="2"'))
    assert '<script>' not in page and '&lt;script&gt;' in page and '&lt;b&gt;1&lt;/b&gt;' in page and 'a &lt; b &amp; &quot;c&quot;<br>second line' in page
    assert 'href="https://x.example.com/?a=1&amp;b=&quot;2&quot;"' in page and page.count('<p style="margin:0 0 16px') == 2
    assert '<script>x</script>' in plain and 'new paragraph' in plain


def test_buttons_must_be_https_and_the_code_is_optional():
    page, plain = mailtpl.render('T', 'body', button=('Go', 'http://insecure.example.com'))
    assert '<a href' not in page and 'insecure' not in plain and 'letter-spacing:3px' not in page
    page, plain = mailtpl.render('T', 'body', code='ABCD-1234', button=('Go', 'https://ok.example.com'))
    assert 'ABCD-1234' in page and '    ABCD-1234' in plain and 'Go: https://ok.example.com' in plain


def test_the_layout_is_responsive_and_branded():
    page, _ = mailtpl.render('Hello', 'x', tone='bad', preheader='preview text', footer='custom footer')
    assert 'max-width:560px' in page and 'width=device-width' in page and 'Shiplane' in page and '#dc2626' in page and 'preview text' in page and 'custom footer' in page
    page, _ = mailtpl.render('Hello', 'x', tone='nonsense')
    assert '#4f46e5' in page and 'switched on in your Shiplane settings' in page


def test_alert_tone_and_headline():
    page, plain = mailtpl.alert('[Coderunner] ROLLED BACK: shop failed its health check\nThe previous release is serving again.')
    assert '#dc2626' in page and '<h1' in page and 'ROLLED BACK: shop failed' in page and '[Coderunner]' not in page and 'previous release is serving again' in plain
    assert '#16a34a' in mailtpl.alert('Site shop is back up')[0] and '#4f46e5' in mailtpl.alert('Disk at 70 percent')[0]
    page, _ = mailtpl.alert('')
    assert 'Shiplane alert' in page
    page, _ = mailtpl.alert('[Shiplane] Test alert: this channel works.')
    assert '#16a34a' in page and '[Shiplane]' not in page


def test_the_activation_mail():
    page, plain = mailtpl.activation('ABCD-EFGH-IJKL-MNOP')
    assert 'ABCD-EFGH-IJKL-MNOP' in page and 'valid for 7 days' in plain and 'ignore it' in page


if __name__ == '__main__':
    run_tests(globals())
