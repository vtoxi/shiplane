#!/usr/bin/env python3
"""Runs every unit test that needs no server and no network, measures line coverage, and fails below the gate.

    pip install coverage          (development only; the app itself needs nothing)
    python tests/run_unit_tests.py

The gate (95 %) applies to the modules below. The rest of the project is reported but is mostly covered by the
end-to-end scripts (test_app.py, test_local_mode.py), which need a real server.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT = ['migrate', 'plesk', 'migrator', 'migrator_options', 'maintenance', 'apps', 'monitor', 'care', 'caredetail', 'watch', 'heal', 'care_budget', 'metricslog', 'gitdeploy', 'gitproviders', 'dns', 'mailtpl', 'dbengines', 'git_http', 'serverops', 'routes_care', 'perf', 'server_perf', 'status_script', 'routes_apps', 'ssh_auth', 'runner_config', 'deploy_types', 'features', 'routes', 'cloudflare', 'dnsproviders', 'wildcard', 'rename', 'auth', 'ops', 'live', 'uploads', 'deploy_ops', 'route_sweep', 'http_security', 'firewall', 'emailcheck', 'site_scope', 'sso', 'fleet', 'care_undo', 'promote']
GATED = ['coderunner/migrate.py', 'coderunner/plesk.py', 'coderunner/migrator.py', 'coderunner/maintenance.py', 'coderunner/apps.py', 'coderunner/perf.py', 'coderunner/care.py', 'coderunner/caredetail.py', 'coderunner/watch.py', 'coderunner/heal.py', 'coderunner/metricslog.py', 'coderunner/gitdeploy.py', 'coderunner/gitproviders.py', 'coderunner/dns.py', 'coderunner/dbengines.py', 'coderunner/mailtpl.py', 'coderunner/serverops.py', 'coderunner/runner.py', 'coderunner/config.py', 'coderunner/uploads.py', 'coderunner/dbops.py', 'coderunner/deploy.py', 'coderunner/firewall.py', 'coderunner/emailcheck.py', 'coderunner/statuspage.py', 'coderunner/apikeys.py', 'coderunner/sso.py', 'coderunner/fleet.py']
GATE = 95


def coverage(*args, **kw):
    return subprocess.run([sys.executable, '-m', 'coverage'] + list(args), cwd=ROOT, **kw)


def main():
    if coverage('--version', capture_output=True).returncode:
        sys.exit('Install the coverage tool first:  pip install coverage')
    for f in os.listdir(ROOT):
        if f.startswith('.coverage'):
            os.remove(os.path.join(ROOT, f))
    failed = []
    for name in UNIT:
        r = coverage('run', '--parallel-mode', '--source=coderunner', 'tests/test_%s.py' % name, capture_output=True, text=True)
        print('%-4s test_%s' % ('ok' if r.returncode == 0 else 'FAIL', name))
        if r.returncode:
            failed.append(name)
            print(r.stdout[-1500:] + r.stderr[-1500:])
    coverage('combine', capture_output=True)
    print('\nCoverage of the gated modules (gate %d%%):' % GATE)
    gated = coverage('report', '--include=' + ','.join(GATED), '--fail-under=%d' % GATE, '-m')
    print('\nWhole project (informational):')
    coverage('report', '--include=coderunner/*')
    if failed or gated.returncode:
        sys.exit('\nFAILED: %s' % (', '.join(failed) if failed else 'coverage below %d%%' % GATE))
    print('\nAll unit tests passed; coverage gate met.')


if __name__ == '__main__':
    main()
