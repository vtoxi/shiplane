"""Own nameservers: validation, zone files, the scripts that go to the server, the firewall and direct-HTTPS actions (fake server, no network).

    python tests/test_dns.py
"""
import os
import subprocess
import sys
import tempfile

os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import deploy, dns, settings  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
ORIGINAL = {(dns, 'run'): dns.run, (deploy, 'stream_script'): deploy.stream_script, (deploy, 'start_job'): deploy.start_job}
RAN = []


def before():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    path = os.path.join(os.environ['CODERUNNER_DATA'], 'settings.json')
    if os.path.exists(path):
        os.remove(path)
    RAN.clear()


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def setup_zone(domain='example.com', ns=('ns1.example.com', 'ns2.example.com')):
    dns.set_nameservers({'ns': list(ns), 'admin': 'ops.team@example.com'})
    return dns.add_zone(domain, '203.0.113.7')


def rec(domain='example.com', **kw):
    return dns.put_record(domain, dict({'name': 'app', 'type': 'A', 'value': '203.0.113.9'}, **kw))


def rec_fails(text, kind=ValueError, **kw):
    raises(lambda: rec(**kw), text=text, kind=kind)


def test_nameserver_settings_are_validated():
    raises(dns.set_nameservers, {'ns': []}, text='one or two')
    raises(dns.set_nameservers, {'ns': ['ns1.example.com', 'ns1.example.com']}, text='different')
    raises(dns.set_nameservers, {'ns': ['bad name']}, text='Nameserver')
    raises(dns.set_nameservers, {'ns': ['ns1.example.com'], 'admin': 'nope'}, text='contact address')
    out = dns.set_nameservers({'ns': ['NS1.Example.com.', 'ns2.example.com', 'ns3.example.com'], 'admin': ''})
    assert out['ns'] == ['ns1.example.com', 'ns2.example.com'] and out['zones'] == []                  # at most two, normalised


def test_a_zone_starts_with_nameservers_addresses_and_glue():
    raises(dns.add_zone, 'example.com', '1.2.3.4', text='nameserver names first')
    z = setup_zone()
    types = sorted((r['name'], r['type'], r['value']) for r in z['records'])
    assert ('@', 'NS', 'ns1.example.com') in types and ('@', 'A', '203.0.113.7') in types and ('www', 'A', '203.0.113.7') in types
    assert ('ns1', 'A', '203.0.113.7') in types and ('ns2', 'A', '203.0.113.7') in types                  # glue for nameservers inside the zone
    assert z['serial'] >= 2000000000 and dns.view()['zones'] == [{'domain': 'example.com', 'records': 6, 'serial': z['serial']}]
    raises(dns.add_zone, 'example.com', '1.2.3.4', text='already has a zone')
    raises(dns.add_zone, 'nodot', '1.2.3.4', text='Domain')
    raises(dns.add_zone, 'other.com', 'not-an-ip', text='IPv4')
    other = dns.add_zone('other.net', '203.0.113.7')                                                      # nameservers elsewhere: no glue
    assert not [r for r in other['records'] if r['name'].startswith('ns')]


def test_zone_limits():
    dns.set_nameservers({'ns': ['ns1.host.net']})
    for i in range(dns.MAX_ZONES):
        dns.add_zone('site%d.com' % i, '203.0.113.7')
    raises(dns.add_zone, 'one-too-many.com', '203.0.113.7', text='At most')


def test_records_are_validated():
    setup_zone()
    for bad, text in (({'type': 'SRV'}, 'Record type'), ({'name': 'bad name!'}, 'name looks wrong'), ({'ttl': 'abc'}, 'TTL'), ({'ttl': 5}, 'TTL'), ({'value': 'x'}, 'IPv4'),
                      ({'type': 'AAAA', 'value': '1.2.3.4'}, 'IPv6'), ({'type': 'CNAME', 'value': 'not a host'}, 'target'), ({'type': 'MX', 'value': 'mail.example.com', 'prio': 'x'}, 'Priority'),
                      ({'type': 'MX', 'value': 'mail.example.com', 'prio': 70000}, 'Priority'), ({'type': 'TXT', 'value': 'say "hi"'}, 'Text records'), ({'type': 'TXT', 'value': 'a\\b'}, 'Text records')):
        rec_fails(text, **bad)
    raises(dns.put_record, 'missing.com', {'name': 'a', 'type': 'A', 'value': '1.2.3.4'}, text='no zone', kind=LookupError)


def test_adding_changing_and_removing_records():
    setup_zone()
    z = rec()
    first = [r for r in z['records'] if r['name'] == 'app'][0]
    serial = z['serial']
    assert z['serial'] == serial and first['ttl'] == 300
    rec_fails('already exists')
    z = rec(id=first['id'], value='203.0.113.10', ttl=600)                                              # change by id
    changed = [r for r in z['records'] if r['name'] == 'app']
    assert len(changed) == 1 and changed[0]['value'] == '203.0.113.10' and changed[0]['ttl'] == 600 and z['serial'] > serial
    rec_fails('does not exist any more', LookupError, id='nope')
    z = dns.put_record('example.com', {'name': 'mail.example.com.', 'type': 'MX', 'value': 'mx.example.com', 'prio': ''})     # a name typed in full is made relative
    mx = [r for r in z['records'] if r['type'] == 'MX'][0]
    assert mx['name'] == 'mail' and mx['prio'] == 10
    z = dns.put_record('example.com', {'name': 'example.com', 'type': 'TXT', 'value': 'v=spf1 mx -all'})
    assert [r for r in z['records'] if r['type'] == 'TXT'][0]['name'] == '@'
    z = dns.delete_record('example.com', changed[0]['id'])
    assert not [r for r in z['records'] if r['name'] == 'app']
    raises(dns.delete_record, 'example.com', 'nope', text='does not exist any more', kind=LookupError)


def test_cname_rules():
    setup_zone()
    rec_fails('cannot be the bare domain', name='@', type='CNAME', value='other.net')
    rec_fails('cannot share its name', name='www', type='CNAME', value='other.net')            # www already has an A record
    rec(name='shop', type='CNAME', value='shops.example.net')
    rec_fails('is a CNAME', name='shop', type='A', value='203.0.113.1')
    assert rec(name='*.apps', type='A', value='203.0.113.2')['records']                                  # wildcard names are fine


def test_the_record_limit():
    setup_zone()
    s = settings.get('dns')
    s['zones']['example.com']['records'] += [{'id': 'f%03d' % i, 'name': 'h%d' % i, 'type': 'A', 'value': '203.0.113.5', 'ttl': 300} for i in range(dns.MAX_RECORDS)]
    settings.put('dns', s)
    rec_fails('At most')


def test_zone_file_text():
    setup_zone()
    rec(name='mail', type='MX', value='mx.example.net', prio=5)
    rec(name='@', type='TXT', value='v=spf1 mx -all')
    rec(name='long', type='TXT', value='x' * 300)
    rec(name='shop', type='CNAME', value='shops.example.net')
    text = dns.render('example.com')
    lines = text.splitlines()
    assert lines[0] == '$ORIGIN example.com.' and lines[2].startswith('@ IN SOA ns1.example.com. ops\\.team.example.com. ')
    assert '@ 3600 IN NS ns1.example.com.' in text and 'mail 300 IN MX 5 mx.example.net.' in text and 'shop 300 IN CNAME shops.example.net.' in text
    assert '@ 300 IN TXT "v=spf1 mx -all"' in text and 'long 300 IN TXT "%s" "%s"' % ('x' * 255, 'x' * 45) in text      # long text is split into 255-byte pieces
    assert lines[3].startswith('@ ') and text.endswith('\n')
    dns.set_nameservers({'ns': ['ns1.example.com'], 'admin': ''})
    assert ' hostmaster.example.com. ' in dns.render('example.com')
    s = settings.get('dns')
    s['ns'] = []
    settings.put('dns', s)
    raises(dns.render, 'example.com', text='nameserver names first')


def test_the_apply_script_is_valid_bash_and_lists_every_zone():
    raises(dns.apply_script, text='nameserver names first')
    dns.set_nameservers({'ns': ['ns1.host.net']})
    raises(dns.apply_script, text='Add a zone first')
    dns.add_zone('a-site.com', '203.0.113.7')
    dns.add_zone('b-site.org', '203.0.113.7')
    text = dns.apply_script()
    assert 'DOMAINS="a-site.com b-site.org"' in text and text.count('<<\'ZONE_') == 2 and '__' not in text.replace('__pycache__', '')
    assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0
    assert 'nsd-checkzone' in text and 'ip-address: $IP' in text


def test_apply_runs_as_a_job_and_reports_the_outcome():
    dns.set_nameservers({'ns': ['ns1.host.net']})
    dns.add_zone('a-site.com', '203.0.113.7')
    scripts = []

    def start(fn, *args):
        job = deploy.Job()
        fn(job, *args)
        scripts.append(job)
        return 'job-1'
    deploy.start_job = start
    deploy.stream_script = lambda job, cfg, text: 0
    assert dns.apply(CFG, 'bob') == {'job': 'job-1'} and scripts[0].ok and 'DNS is being served' in scripts[0].lines[-1]
    deploy.stream_script = lambda job, cfg, text: 1
    dns.apply(CFG, 'bob')
    assert not scripts[1].ok and 'nothing was changed' in scripts[1].lines[-1]


def test_firewall_changes_need_the_word_open():
    dns.run = lambda cfg, cmd, inp=None, timeout=60: RAN.append(inp.decode()) or (0, 'opened 53/tcp\nopened 53/udp\n')
    raises(dns.ports, CFG, 'dns', 'yes', text='Type "open"')
    raises(dns.ports, CFG, 'ssh', 'open', text='Unknown port set')
    assert not RAN
    assert dns.ports(CFG, 'dns', 'open') == ['opened 53/tcp', 'opened 53/udp'] and 'MODE=dns' in RAN[0]
    assert dns.ports(CFG, 'web', 'open') and 'MODE=web' in RAN[1]
    dns.run = lambda cfg, cmd, inp=None, timeout=60: (1, 'ufw: boom')
    raises(dns.ports, CFG, 'dns', 'open', text='ufw: boom', kind=RuntimeError)
    dns.run = lambda cfg, cmd, inp=None, timeout=60: (1, '')
    raises(dns.ports, CFG, 'dns', 'open', text='could not change the firewall', kind=RuntimeError)


def test_status_is_parsed():
    dns.run = lambda cfg, cmd, inp=None, timeout=30: (0, 'IP 203.0.113.7\nNSD active\nLISTEN53 yes\nUFW active\nFW53 allowed\nFW80 blocked\nFW443 blocked\nnoise\n')
    s = dns.status(CFG)
    assert s == {'ip': '203.0.113.7', 'nsd': 'active', 'listening': True, 'firewall': 'active', 'open': {'53': 'allowed', '80': 'blocked', '443': 'blocked'}}
    dns.run = lambda cfg, cmd, inp=None, timeout=30: (1, '')
    assert dns.status(CFG)['nsd'] == 'missing' and dns.status(CFG)['listening'] is False


def test_direct_https_is_confirmed_and_never_touches_the_portal():
    dns.run = lambda cfg, cmd, inp=None, timeout=60: RAN.append(inp.decode()) or (0, 'ok\n')
    raises(dns.site_direct, CFG, 'Bad Name', True, 'x', text='bad site name')
    raises(dns.site_direct, CFG, 'coderunner', True, 'coderunner', text='behind the tunnel')
    raises(dns.site_direct, CFG, 'shop', True, 'nope', text='Type the site name')
    assert not RAN
    assert dns.site_direct(CFG, 'shop', True, 'shop') == ['ok'] and 'MODE=on' in RAN[0] and 'NAME=shop' in RAN[0]
    dns.site_direct(CFG, 'shop', False, 'shop')
    assert 'MODE=off' in RAN[1]
    dns.run = lambda cfg, cmd, inp=None, timeout=60: (1, 'Caddy rejected the change')
    raises(dns.site_direct, CFG, 'shop', True, 'shop', text='Caddy rejected', kind=RuntimeError)
    dns.run = lambda cfg, cmd, inp=None, timeout=60: (1, '')
    raises(dns.site_direct, CFG, 'shop', True, 'shop', text='could not change the site', kind=RuntimeError)


def test_the_other_scripts_are_valid_bash():
    for name, fill in (('dns_ports.sh', {'__MODE__': 'dns'}), ('dns_status.sh', {}), ('site_direct.sh', {'__NAME__': 'shop', '__MODE__': 'on'})):
        text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'coderunner', 'remote', name), encoding='utf-8').read()
        for k, v in fill.items():
            text = text.replace(k, v)
        assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0, name


def test_the_direct_switch_changes_the_caddy_header_and_keeps_the_hosts_readable():
    text = deploy.deploy_script(deploy.validate_app({'name': 'shop', 'hostname': 'shop.example.com', 'type': 'php', 'source': tempfile.gettempdir(), 'direct': True}), 'bob')
    assert 'http://shop.example.com, https://shop.example.com {' in text and '"direct": true' in text
    plain = deploy.deploy_script(deploy.validate_app({'name': 'shop', 'hostname': 'shop.example.com', 'type': 'php', 'source': tempfile.gettempdir()}), 'bob')
    assert 'https://shop.example.com' not in plain and '"direct"' not in plain
    import re
    pattern = r'^http://([^\s,{]+)(?:,\s*https://[^\s,{]+)?\s*\{'
    assert re.findall(pattern, 'http://a.example.com, https://a.example.com {\n}', re.M) == ['a.example.com']
    assert re.findall(pattern, 'http://a.example.com {\n}', re.M) == ['a.example.com']


if __name__ == '__main__':
    run_tests(globals(), before=before)
