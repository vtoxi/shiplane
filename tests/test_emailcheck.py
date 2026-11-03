"""SPF / DKIM / DMARC / MX evaluation with canned DNS answers (no network).

    python tests/test_emailcheck.py
"""
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import emailcheck as ec  # noqa: E402

KEY = 'v=DKIM1; k=rsa; p=' + 'A' * 40


def fake(table):
    return lambda name, rtype: table.get((name, rtype), [])


def by_id(res):
    return {f['id']: f for f in res['findings']}


def test_a_well_configured_domain_is_all_ok():
    f = by_id(ec.check('example.com', fetch=fake({('example.com', 'MX'): ['10 mail.example.com.'], ('example.com', 'TXT'): ['v=spf1 mx -all'],
                                                  ('_dmarc.example.com', 'TXT'): ['v=DMARC1; p=reject; rua=mailto:d@example.com'], ('google._domainkey.example.com', 'TXT'): [KEY]})))
    assert {k: v['status'] for k, v in f.items()} == {'mx': 'ok', 'spf': 'ok', 'dkim': 'ok', 'dmarc': 'ok'} and 'google' in f['dkim']['title']


def test_an_empty_domain_gets_actionable_advice():
    f = by_id(ec.check('example.com', fetch=fake({('example.com', 'MX'): ['x']})))
    assert f['spf']['status'] == 'bad' and 'v=spf1' in f['spf']['fix'] and f['dmarc']['status'] == 'bad' and '_dmarc' in f['dmarc']['fix'] and f['dkim']['status'] == 'warn'


def test_spf_problems():
    assert ec.spf(['v=spf1 mx -all', 'v=spf1 a -all'])['title'] == 'More than one SPF record'
    many = 'v=spf1 ' + ' '.join('include:s%d.example.com' % i for i in range(11)) + ' -all'
    assert ec.spf([many])['status'] == 'bad' and '11' in ec.spf([many])['detail']
    assert ec.spf(['v=spf1 mx +all'])['status'] == 'warn' and ec.spf(['v=spf1 mx'])['status'] == 'warn'
    assert ec.spf(['v=spf1 mx ~all'])['status'] == 'ok' and 'soft fail' in ec.spf(['v=spf1 mx ~all'])['detail']
    assert ec.spf(['v=spf1 ip4:1.2.3.4 ?all'])['title'] == 'SPF is neutral'


def test_dmarc_policies():
    assert ec.dmarc(['v=DMARC1; p=none'])['status'] == 'warn' and 'no rua' in ec.dmarc(['v=DMARC1; p=none'])['detail']
    assert ec.dmarc(['v=DMARC1; p=quarantine; rua=mailto:a@b.c'])['status'] == 'ok'
    assert ec.dmarc(['v=DMARC1; p=bogus'])['status'] == 'bad'


def test_dkim_revoked_and_extra_selector():
    assert ec.dkim({'default': ['v=DKIM1; p=']})['title'].startswith('DKIM key is revoked')
    f = by_id(ec.check('example.com', ['MySel'], fetch=fake({('mysel._domainkey.example.com', 'TXT'): [KEY], ('example.com', 'MX'): ['x']})))
    assert f['dkim']['status'] == 'ok' and 'mysel' in f['dkim']['title']


def test_input_validation_and_offline():
    for bad in ('', 'nodot', 'bad host.com', 'a.com/../x'):
        try:
            ec.check(bad, fetch=fake({}))
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        ec.check('example.com', ['bad selector!'], fetch=fake({}))
    except ValueError as e:
        assert 'selector' in str(e)
    else:
        raise AssertionError
    try:
        ec.check('example.com', fetch=lambda n, t: None)
    except RuntimeError as e:
        assert 'DNS-over-HTTPS' in str(e)
    else:
        raise AssertionError


def test_doh_parsing_and_failure():
    body = json.dumps({'Answer': [{'data': '"v=spf1 ""mx -all"'}, {'data': '10 mail.example.com.'}]}).encode()

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    real = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda req, timeout=0: R(body)
        assert ec.lookup('example.com', 'TXT')[0] == 'v=spf1 mx -all' and ec.lookup('example.com', 'MX')[1] == '10 mail.example.com.'
        urllib.request.urlopen = lambda req, timeout=0: R(b'not json')
        assert ec.lookup('example.com', 'TXT') is None
    finally:
        urllib.request.urlopen = real


if __name__ == '__main__':
    run_tests(dict(globals()))
