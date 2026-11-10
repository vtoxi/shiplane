"""The domain readiness check (is a registrar domain ready to be served from here?) with canned DNS answers: no network.

    python tests/test_domaincheck.py
"""
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import domaincheck as dc  # noqa: E402

IP = '65.109.160.94'


def ans(apex=(), www=(), apex6=(), www6=(), ns=(('NS', 'ns1.registrar.example'), ('NS', 'ns2.registrar.example')), caa=()):
    return {'apex': list(apex), 'www': list(www), 'apex6': list(apex6), 'www6': list(www6), 'ns': list(ns), 'caa': list(caa)}


def by(findings):
    return {f['id']: f for f in findings}


def test_everything_right_is_all_ok():
    f = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('CNAME', 'example.pk'), ('A', IP)], caa=[('CAA', '0 issue "letsencrypt.org"')])))
    assert {k: v['status'] for k, v in f.items()} == {'ns': 'ok', 'apex': 'ok', 'www': 'ok', 'caa': 'ok'} and 'an alias of example.pk' in f['www']['title']


def test_the_registrars_parking_page_is_called_out():
    f = by(dc.evaluate('example.pk', IP, ans(apex=[('A', '192.0.2.10')], www=[('A', '192.0.2.10')])))
    assert f['apex']['status'] == 'bad' and '192.0.2.10, not at this server' in f['apex']['title'] and 'parking page' in f['apex']['detail'] and IP in f['apex']['fix']
    assert f['www']['status'] == 'bad'


def test_no_records_and_mixed_records():
    f = by(dc.evaluate('example.pk', IP, ans()))
    assert f['apex']['status'] == 'bad' and 'Add an A record' in f['apex']['fix'] and f['www']['status'] == 'warn' and 'CNAME' in f['www']['fix']
    f = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP), ('A', '192.0.2.10')], www=[('A', IP)])))
    assert f['apex']['status'] == 'bad' and 'also points elsewhere' in f['apex']['title'] and 'Delete the A records that are not %s' % IP in f['apex']['fix']


def test_ipv6_and_caa_traps():
    f = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('A', IP)], apex6=[('AAAA', '2001:db8::1')], caa=[('CAA', '0 issue "digicert.com"'), ('CAA', '0 issuewild "letsencrypt.org"')])))
    assert f['ipv6']['status'] == 'warn' and '2001:db8::1' in f['ipv6']['detail'] and 'Delete the AAAA records' in f['ipv6']['fix']
    assert f['caa']['status'] == 'bad' and 'issue' in f['caa']['fix']
    ok = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('A', IP)], caa=[('CAA', '0 issue "digicert.com"'), ('CAA', '0 issue "letsencrypt.org"')])))
    assert ok['caa']['status'] == 'ok'
    only_wild = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('A', IP)], caa=[('CAA', '0 issuewild "digicert.com"')])))
    assert 'caa' not in only_wild                                                                                          # issuewild does not stop the name itself
    unknown_form = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('A', IP)], caa=[('CAA', '\\# 22 00050769737375656C')])))
    assert 'caa' not in unknown_form                                                                                       # a form we cannot read is not a false alarm


def test_who_hosts_the_dns():
    cf = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('A', IP)], ns=[('NS', 'ada.ns.cloudflare.com'), ('NS', 'bob.ns.cloudflare.com')])))
    assert cf['ns']['status'] == 'warn' and 'Cloudflare' in cf['ns']['title'] and 'DNS only' in cf['ns']['fix']
    own = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('A', IP)], ns=[('NS', 'ns1.example.pk'), ('NS', 'ns2.example.pk')])))
    assert own['ns']['status'] == 'ok' and 'own nameservers' in own['ns']['title']
    assert by(dc.evaluate('example.pk', IP, ans()))['ns']['status'] == 'ok'
    none = by(dc.evaluate('example.pk', IP, ans(ns=[])))
    assert none['ns']['status'] == 'bad'
    reg = by(dc.evaluate('example.pk', IP, ans(apex=[('A', IP)], www=[('A', IP)])))
    assert 'registrar.example' in reg['ns']['title']


def test_unknown_server_ip_still_reports():
    f = by(dc.evaluate('example.pk', '', ans(apex=[('A', '203.0.113.7')], www=[('A', '203.0.113.7')])))
    assert f['apex']['status'] == 'warn' and f['www']['status'] == 'warn' and 'could not be read' in f['apex']['detail']


def test_check_validates_input_and_lookups():
    for bad in ('', 'nodot', 'www.example.pk', 'a b.com', 'x/y.com', 'bad;domain.com'):
        try:
            dc.check(bad, IP, fetch=lambda n, t: [])
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        dc.check('example.pk', 'not-an-ip', fetch=lambda n, t: [])
    except ValueError as e:
        assert 'IPv4' in str(e)
    else:
        raise AssertionError
    asked = []
    out = dc.check('Example.PK', IP, fetch=lambda n, t: asked.append((n, t)) or ([('A', IP)] if t == 'A' else []))
    assert out['domain'] == 'example.pk' and ('www.example.pk', 'A') in asked and ('example.pk', 'CAA') in asked and out['records']['apex'] == [{'type': 'A', 'data': IP}]
    try:
        dc.check('example.pk', IP, fetch=lambda n, t: None)
    except RuntimeError as e:
        assert 'DNS-over-HTTPS' in str(e)
    else:
        raise AssertionError


def test_the_lookup_parses_dns_over_https_answers():
    body = json.dumps({'Answer': [{'type': 5, 'data': 'example.pk.'}, {'type': 1, 'data': '65.109.160.94'}, {'type': 2, 'data': 'ns1.registrar.example.'}, {'type': 257, 'data': '0 issue "letsencrypt.org"'}]}).encode()

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    real = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda req, timeout=0: R(body)
        rows = dc.query('www.example.pk', 'A')
        assert rows == [('CNAME', 'example.pk'), ('A', '65.109.160.94'), ('NS', 'ns1.registrar.example'), ('CAA', '0 issue "letsencrypt.org"')]
        urllib.request.urlopen = lambda req, timeout=0: R(b'garbage')
        assert dc.query('example.pk', 'A') is None
    finally:
        urllib.request.urlopen = real


if __name__ == '__main__':
    run_tests(dict(globals()))
