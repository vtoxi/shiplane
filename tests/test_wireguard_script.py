"""scripts/setup_wireguard.sh: the real script run against stand-ins for wg, qrencode and ip (no packages, no services, no firewall touched).

    python tests/test_wireguard_script.py
"""
import os
import re
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'scripts', 'setup_wireguard.sh')
BIN = tempfile.mkdtemp()
STUBS = {
    # deterministic fake keys: wg genkey -> PRIVn, wg pubkey -> PUB(of stdin), wg genpsk -> PSKn
    'wg': '#!/bin/bash\nbump() { c="$WG_DIR.counter"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo $n > "$c"; }\ncase "$1" in genkey) bump; echo "PRIV$n";; genpsk) bump; echo "PSK$n";; pubkey) echo "PUB-$(cat)";; show) echo "(wg show)";; *) exit 0;; esac\n',
    'qrencode': '#!/bin/bash\necho "[QR of $(head -1 | tr -d "\\n")]"\n',
    'ip': '#!/bin/bash\nif [[ "$*" == *"-6 addr"* ]]; then [ -n "$FAKE_V6" ] && echo "    inet6 2a01:4f8::1/64 scope global"; fi\nexit 0\n',
}
for name, body in STUBS.items():
    p = os.path.join(BIN, name)
    open(p, 'w').write(body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)


def run(wgdir, *args, v6=False, ok=True):
    env = dict(os.environ, PATH=BIN + os.pathsep + os.environ['PATH'], WG_DIR=wgdir, WG_NO_SYSTEM='1')
    env.pop('FAKE_V6', None)
    if v6:
        env['FAKE_V6'] = '1'
    r = subprocess.run(['bash', SCRIPT, '--iface', 'eth0', '--endpoint', '203.0.113.5'] + list(args) if not args or args[0] not in ('--add', '--show', '--remove', '--list', '--status', '--uninstall') else ['bash', SCRIPT] + list(args),
                       capture_output=True, text=True, env=env, timeout=60)
    assert (r.returncode == 0) == ok, (args, r.returncode, r.stdout, r.stderr)
    return r.stdout + r.stderr


def read(wgdir, rel):
    return open(os.path.join(wgdir, rel)).read()


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_first_run_builds_the_server_and_a_phone():
    d = tempfile.mkdtemp()
    out = run(d)
    conf = read(d, 'wg0.conf')
    assert 'Address = 10.8.0.1/24\n' in conf and 'ListenPort = 51820' in conf and 'PrivateKey = PRIV1' in conf
    assert '-o eth0 -j MASQUERADE' in conf and '-i wg0 -o wg0 -j DROP' in conf                 # NAT out of the public interface, devices cannot reach each other
    assert 'ip6tables' not in conf and 'fd42' not in conf                                       # no IPv6 on this server
    assert '# shiplane-client: phone\n[Peer]\nPublicKey = PUB-PRIV2\nPresharedKey = PSK3\nAllowedIPs = 10.8.0.2/32' in conf
    phone = read(d, 'shiplane/clients/phone.conf')
    assert 'PrivateKey = PRIV2' in phone and 'Address = 10.8.0.2/32\n' in phone and 'DNS = 1.1.1.1, 9.9.9.9' in phone
    assert 'PublicKey = PUB-PRIV1' in phone and 'Endpoint = 203.0.113.5:51820' in phone and 'AllowedIPs = 0.0.0.0/0, ::/0' in phone and 'PersistentKeepalive = 25' in phone
    assert '[QR of [Interface]]' in out and 'Scan from QR code' in out
    for f in ('wg0.conf', 'shiplane/clients/phone.conf'):
        assert mode(os.path.join(d, f)) == 0o600, f
    assert mode(os.path.join(d, 'shiplane')) == 0o700


def test_running_again_keeps_the_server_and_the_device():
    d = tempfile.mkdtemp()
    run(d)
    before = read(d, 'wg0.conf')
    out = run(d)
    assert read(d, 'wg0.conf') == before and 'already exists' in out and 'keeping it' in out


def test_more_devices_get_their_own_addresses_and_keys():
    d = tempfile.mkdtemp()
    run(d)
    run(d, '--add', 'laptop')
    run(d, '--add', 'tablet')
    conf = read(d, 'wg0.conf')
    assert re.findall(r'AllowedIPs = (10\.8\.0\.\d+)/32', conf) == ['10.8.0.2', '10.8.0.3', '10.8.0.4']
    keys = re.findall(r'PrivateKey = (\S+)', read(d, 'shiplane/clients/phone.conf') + read(d, 'shiplane/clients/laptop.conf') + read(d, 'shiplane/clients/tablet.conf'))
    assert len(set(keys)) == 3
    assert 'Address = 10.8.0.3/32' in read(d, 'shiplane/clients/laptop.conf')
    out = run(d, '--list')
    assert 'phone' in out and 'laptop' in out and 'tablet' in out and '10.8.0.4/32' in out
    assert 'already exists' in run(d, '--add', 'laptop', ok=False)


def test_removing_a_device_revokes_it_and_frees_its_address():
    d = tempfile.mkdtemp()
    run(d)
    run(d, '--add', 'laptop')
    run(d, '--add', 'tablet')
    run(d, '--remove', 'laptop')
    conf = read(d, 'wg0.conf')
    assert 'laptop' not in conf and 'AllowedIPs = 10.8.0.3' not in conf
    assert '# shiplane-client: phone' in conf and '# shiplane-client: tablet' in conf and '[Interface]' in conf        # the others and the server stay
    assert not os.path.exists(os.path.join(d, 'shiplane/clients/laptop.conf'))
    run(d, '--add', 'watch')
    assert 'AllowedIPs = 10.8.0.3/32' in read(d, 'wg0.conf')                                                           # the freed address is reused
    assert 'no device called' in run(d, '--remove', 'ghost', ok=False)


def test_names_and_options_are_validated():
    d = tempfile.mkdtemp()
    run(d)
    for bad in ('Phone', '../etc', 'a b', '1abc', 'x' * 40, 'a;rm'):
        assert 'name must be' in run(d, '--add', bad, ok=False), bad
    assert 'unknown option' in run(d, '--frobnicate', ok=False)
    assert '--port' in run(tempfile.mkdtemp(), '--port', '80', ok=False)
    assert '--dns' in run(tempfile.mkdtemp(), '--dns', '1.1.1.1; reboot', ok=False)
    assert '--endpoint' in run(tempfile.mkdtemp(), '--endpoint', 'a b;c', ok=False)
    assert 'not set up yet' in run(tempfile.mkdtemp(), '--add', 'phone', ok=False)


def test_ipv6_is_routed_only_when_the_server_has_it():
    d = tempfile.mkdtemp()
    run(d, v6=True)
    conf = read(d, 'wg0.conf')
    assert 'Address = 10.8.0.1/24, fd42:42:42::1/64' in conf and 'ip6tables -t nat -A POSTROUTING -s fd42:42:42::/64 -o eth0 -j MASQUERADE' in conf
    assert 'AllowedIPs = 10.8.0.2/32, fd42:42:42::2/128' in conf
    assert 'Address = 10.8.0.2/32, fd42:42:42::2/128' in read(d, 'shiplane/clients/phone.conf')
    d2 = tempfile.mkdtemp()
    run(d2, '--no-ipv6', v6=True)
    assert 'ip6tables' not in read(d2, 'wg0.conf')


def test_custom_port_and_endpoint_and_uninstall_needs_yes():
    d = tempfile.mkdtemp()
    run(d, '--port', '4000', '--endpoint', 'vpn.example.com')
    assert 'ListenPort = 4000' in read(d, 'wg0.conf') and 'Endpoint = vpn.example.com:4000' in read(d, 'shiplane/clients/phone.conf')
    d2 = tempfile.mkdtemp()
    run(d2, '--endpoint', 'vpn.example.com:5000')
    assert 'ListenPort = 5000' in read(d2, 'wg0.conf')
    assert '--yes' in run(d, '--uninstall', ok=False) and os.path.exists(os.path.join(d, 'wg0.conf'))
    run(d, '--uninstall', '--yes')
    assert not os.path.exists(os.path.join(d, 'wg0.conf')) and not os.path.exists(os.path.join(d, 'shiplane'))


def test_show_prints_the_config_again():
    d = tempfile.mkdtemp()
    run(d)
    out = run(d, '--show', 'phone')
    assert '[QR of [Interface]]' in out and 'PrivateKey = PRIV2' in out
    assert 'no device called' in run(d, '--show', 'ghost', ok=False)


if __name__ == '__main__':
    run_tests(globals())
