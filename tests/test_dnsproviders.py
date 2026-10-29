"""Route 53 and DigitalOcean: connect checks, A records added only when missing, never overwritten, removed only when they point here (fake APIs).

    python tests/test_dnsproviders.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import dnsproviders as d  # noqa: E402

IP = '93.184.216.34'
KEY, SECRET, TOKEN = 'AKIAEXAMPLEKEY12', 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', 'dop_v1_' + 'a' * 64
NS = 'xmlns="https://route53.amazonaws.com/doc/2013-04-01/"'
R53 = {}      # host -> ip
DO = {}       # (domain, host) -> (id, ip)
SENT = []


def fake_http(method, url, headers, body=None):
    SENT.append((method, url, headers, body))
    if url.startswith(d.R53_API):
        if not headers.get('Authorization', '').startswith('AWS4-HMAC-SHA256 Credential=' + KEY):
            return 403, b'<ErrorResponse><Error><Message>The security token included in the request is invalid</Message></Error></ErrorResponse>'
        if url.endswith('/hostedzone?maxitems=100'):
            return 200, ('<ListHostedZonesResponse %s><HostedZones><HostedZone><Id>/hostedzone/Z1</Id><Name>aws.example.com.</Name><Config><PrivateZone>false</PrivateZone></Config></HostedZone>'
                         '<HostedZone><Id>/hostedzone/Z2</Id><Name>internal.lan.</Name><Config><PrivateZone>true</PrivateZone></Config></HostedZone></HostedZones></ListHostedZonesResponse>' % NS).encode()
        if method == 'GET':
            host = re.search(r'name=([^&]+)', url).group(1).rstrip('.')
            sets = ''.join('<ResourceRecordSet><Name>%s.</Name><Type>A</Type><ResourceRecords><ResourceRecord><Value>%s</Value></ResourceRecord></ResourceRecords></ResourceRecordSet>' % (h, ip)
                           for h, ip in R53.items() if h == host)
            return 200, ('<ListResourceRecordSetsResponse %s><ResourceRecordSets>%s</ResourceRecordSets></ListResourceRecordSetsResponse>' % (NS, sets)).encode()
        action, host, ip = re.search(rb'<Action>(\w+)</Action><ResourceRecordSet><Name>([^<]+)\.</Name>.*<Value>([^<]+)</Value>', body).groups()
        if action == b'CREATE':
            R53[host.decode()] = ip.decode()
        else:
            R53.pop(host.decode())
        return 200, b''
    if headers.get('Authorization') != 'Bearer ' + TOKEN:
        return 401, b'{"id":"unauthorized","message":"Unable to authenticate you"}'
    if '/v2/domains?' in url:
        return 200, b'{"domains":[{"name":"do.example.com"},{"name":"example.com"}]}'
    dom = url.split('/v2/domains/')[1].split('/')[0]
    if method == 'GET':
        host = re.search(r'name=([^&]+)', url).group(1)
        return 200, json.dumps({'domain_records': [{'id': i, 'data': ip} for (z, h), (i, ip) in DO.items() if z == dom and h == host]}).encode()
    if method == 'POST':
        rec = json.loads(body)
        host = dom if rec['name'] == '@' else rec['name'] + '.' + dom
        DO[(dom, host)] = (len(DO) + 1, rec['data'])
        return 201, b'{}'
    rid = int(url.rsplit('/', 1)[1])
    for k, v in list(DO.items()):
        if v[0] == rid:
            del DO[k]
    return 204, b''


def before():
    d.FILE = os.path.join(tempfile.mkdtemp(), 'dnsproviders.json')
    d._http = fake_http
    R53.clear()
    DO.clear()
    SENT.clear()


def raises(fn, *a, text='', kind=Exception):
    try:
        fn(*a)
    except kind as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('did not raise')


def test_connect_checks_input_and_credentials_and_never_returns_secrets():
    raises(d.connect, 'godaddy', {}, IP, text='Pick')
    raises(d.connect, 'route53', {'key_id': 'bad', 'secret': SECRET}, IP, text='looks wrong')
    raises(d.connect, 'digitalocean', {'token': TOKEN}, '10.0.0.1', text='public')
    raises(d.connect, 'digitalocean', {'token': TOKEN}, 'nope', text='IPv4')
    raises(d.connect, 'digitalocean', {'token': 'x' * 30}, IP, text='Unable to authenticate')
    assert d.connected() == []
    out = d.connect('route53', {'key_id': KEY, 'secret': SECRET}, IP)
    assert out['zones'] == ['aws.example.com'] and out['ip'] == IP           # the private zone is left out
    d.connect('digitalocean', {'token': TOKEN}, IP)
    assert d.connected() == ['route53', 'digitalocean']
    text = json.dumps(d.connection())
    assert SECRET not in text and TOKEN not in text and KEY in text
    assert oct(os.stat(d.FILE).st_mode)[-3:] in ('600', '666')               # 666: Windows has no owner-only mode
    assert d.zone_names() == ['aws.example.com', 'do.example.com', 'example.com']
    d.disconnect('route53')
    assert d.connected() == ['digitalocean'] and d.connection()['ip'] == IP
    d.disconnect('digitalocean')
    assert d.connection()['ip'] == ''


def test_map_adds_missing_records_and_never_overwrites():
    d.connect('route53', {'key_id': KEY, 'secret': SECRET}, IP)
    d.connect('digitalocean', {'token': TOKEN}, IP)
    DO[('example.com', 'taken.example.com')] = (99, '198.51.100.7')
    lines = d.map_hosts(['app.aws.example.com', 'app.do.example.com', 'example.com', 'taken.example.com'])
    assert R53 == {'app.aws.example.com': IP}
    assert DO[('do.example.com', 'app.do.example.com')][1] == IP and DO[('example.com', 'example.com')][1] == IP   # most specific zone wins; apex is @
    assert DO[('example.com', 'taken.example.com')] == (99, '198.51.100.7') and 'not overwritten' in lines[3]
    assert 'already' in d.map_hosts(['app.aws.example.com'])[0]
    raises(d.map_hosts, ['site.other.org'], text='No connected DNS provider')


def test_unmap_removes_only_records_that_point_here():
    d.connect('route53', {'key_id': KEY, 'secret': SECRET}, IP)
    d.connect('digitalocean', {'token': TOKEN}, IP)
    d.map_hosts(['a.aws.example.com', 'a.do.example.com'])
    DO[('example.com', 'other.example.com')] = (77, '198.51.100.7')
    lines = d.unmap_hosts(['a.aws.example.com', 'a.do.example.com', 'other.example.com'])
    assert R53 == {} and ('do.example.com', 'a.do.example.com') not in DO
    assert ('example.com', 'other.example.com') in DO and 'left alone' in lines[2]


def test_route53_requests_are_signed():
    d.connect('route53', {'key_id': KEY, 'secret': SECRET}, IP)
    m, url, headers, _ = SENT[-1]
    assert re.match(r'^AWS4-HMAC-SHA256 Credential=%s/\d{8}/us-east-1/route53/aws4_request, SignedHeaders=host;x-amz-date, Signature=[0-9a-f]{64}$' % KEY, headers['Authorization'])
    assert SECRET not in json.dumps(headers)



def test_signature_matches_the_aws_test_suite():
    # "get-vanilla" from the AWS Signature V4 test suite
    import datetime
    old = d.R53_API
    d.R53_API = 'https://example.amazonaws.com'
    try:
        h = d._sign({'key_id': 'AKIDEXAMPLE', 'secret': 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY'}, 'GET', '/', '', None,
                    now=datetime.datetime(2015, 8, 30, 12, 36), service='service')
    finally:
        d.R53_API = old
    assert h['Authorization'].endswith('Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31'), h['Authorization']


if __name__ == '__main__':
    run_tests(globals(), before=before)
