#!/usr/bin/env python3
"""One command to make GitHub deploy Shiplane to your server: SSH access, host key, and the repository's secrets and variables.

    python scripts/setup_github_deploy.py --host shiplane.uk --user root --domain shiplane.uk --admin-email faisalshafi69@gmail.com
    python scripts/install_on_server.py --domain shiplane.uk --admin-email faisalshafi69@gmail.com

Run it on your own computer (needs ssh, ssh-keygen, ssh-keyscan and the GitHub CLI `gh`, signed in with `gh auth login`). It:
  1. makes a new SSH key used only for deploying (no passphrase, so the workflow can use it),
  2. reads the server's host key and shows its fingerprint for you to confirm (only that key is trusted from then on),
  3. puts the key's public half on the server (you type the server password once) and checks it works,
  4. saves DEPLOY_HOST / DEPLOY_USER / DEPLOY_SSH_KEY / DEPLOY_KNOWN_HOSTS as repository secrets and DEPLOY_DOMAIN / ADMIN_EMAIL / DEPLOY_PORT as variables,
     creates the `production` environment, and (with --run) starts the first deploy,
  5. deletes the private key from this computer (it now lives only in GitHub; --keep-key keeps it).
Secrets are sent to `gh` on stdin, never on a command line. Python standard library only.

"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HOST_RE = re.compile(r'^[A-Za-z0-9.-]+$')
USER_RE = re.compile(r'^[a-z_][a-z0-9_-]*$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def repo_from_remote(url):
    """owner/name from an https or ssh GitHub remote URL, or ''."""
    m = re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$', url.strip())
    return m.group(1) if m else ''


def check_args(a):
    if not HOST_RE.match(a.host):
        raise ValueError('Server host looks wrong')
    if not USER_RE.match(a.user):
        raise ValueError('Server user looks wrong')
    if not 1 <= a.port <= 65535:
        raise ValueError('SSH port must be between 1 and 65535')
    if not re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', a.domain):
        raise ValueError('--domain must be a hostname like shiplane.uk')
    if not EMAIL_RE.match(a.admin_email):
        raise ValueError('--admin-email does not look like an email address')
    if not REPO_RE.match(a.repo or ''):
        raise ValueError('Could not work out the repository: pass --repo owner/name')


def run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def need(tool):
    if not shutil.which(tool):
        sys.exit('%s is not installed. Install it first (OpenSSH client / GitHub CLI: https://cli.github.com).' % tool)


def gh_set(kind, name, value, repo, runner=run):
    """gh secret|variable set NAME --repo R, the value on stdin so it never appears in a process list."""
    r = runner(['gh', kind, 'set', name, '--repo', repo], input=value.encode(), capture_output=True)
    if r.returncode:
        raise RuntimeError('gh %s set %s failed: %s' % (kind, name, (r.stderr or b'').decode().strip()))


def fingerprints(known_hosts, runner=run):
    r = runner(['ssh-keygen', '-lf', '-'], input=known_hosts.encode(), capture_output=True)
    return (r.stdout or b'').decode().strip()


PLACEHOLDER = 'CHANGE-ME'
DEFAULT_ADMIN_EMAIL = 'faisalshafi69@gmail.com'      # used when --admin-email is not given with --vars-only


def vars_only(a, runner=run):
    """Create the production environment and the repository variables. Values not given become CHANGE-ME, which the workflow refuses to deploy with."""
    if not REPO_RE.match(a.repo or ''):
        sys.exit('Could not work out the repository: pass --repo owner/name')
    need('gh')
    if a.domain and not re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', a.domain):
        sys.exit('--domain must be a hostname like shiplane.uk')
    if a.admin_email and not EMAIL_RE.match(a.admin_email):
        sys.exit('--admin-email does not look like an email address')
    env = runner(['gh', 'api', '-X', 'PUT', 'repos/%s/environments/production' % a.repo], capture_output=True)
    print('production environment: ' + ('ready' if not env.returncode else 'NOT created: %s' % (env.stderr or b'').decode().strip()))
    for name, value in (('DEPLOY_DOMAIN', a.domain or PLACEHOLDER), ('ADMIN_EMAIL', a.admin_email or DEFAULT_ADMIN_EMAIL), ('DEPLOY_PORT', str(a.port))):
        gh_set('variable', name, value, a.repo, runner)
        print('variable %-14s %s' % (name, value) + ('   <- change this' if value == PLACEHOLDER else ''))
    if a.direct_https:
        gh_set('variable', 'DEPLOY_DIRECT_HTTPS', 'true', a.repo, runner)
        print('variable DEPLOY_DIRECT_HTTPS true')
    print('Set the missing ones with:  gh variable set DEPLOY_DOMAIN --repo %s --body shiplane.uk' % a.repo)


def main(argv=None, runner=run, ask=input):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vars-only', action='store_true', help='only create the production environment and the variables (placeholders CHANGE-ME for what you did not give); no SSH, no secrets')
    ap.add_argument('--host', default='', help='the server address')
    ap.add_argument('--user', default='root', help='SSH user: root, or a user with passwordless sudo (default root)')
    ap.add_argument('--port', type=int, default=22)
    ap.add_argument('--direct-https', action='store_true', help='the domain points straight at the server: Caddy serves https itself (sets DEPLOY_DIRECT_HTTPS=true). Leave off behind a Cloudflare tunnel')
    ap.add_argument('--domain', default='', help='where Shiplane will be served, e.g. shiplane.uk')
    ap.add_argument('--admin-email', default='', help='the first super admin')
    ap.add_argument('--repo', default='', help='owner/name (default: the origin remote of this folder)')
    ap.add_argument('--keep-key', action='store_true', help='keep the private key file on this computer')
    ap.add_argument('--run', action='store_true', help='start the first deploy when everything is saved')
    ap.add_argument('--yes', action='store_true', help='do not ask to confirm the server fingerprint (not recommended)')
    a = ap.parse_args(argv)
    if not a.repo:
        r = runner(['git', 'remote', 'get-url', 'origin'], capture_output=True)
        a.repo = repo_from_remote((r.stdout or b'').decode())
    if a.vars_only:
        return vars_only(a, runner)
    if not (a.host and a.domain and a.admin_email):
        sys.exit('--host, --domain and --admin-email are required (or use --vars-only)')
    try:
        check_args(a)
    except ValueError as e:
        sys.exit(str(e))
    for tool in ('ssh', 'ssh-keygen', 'ssh-keyscan', 'ssh-copy-id', 'gh'):
        need(tool)
    if runner(['gh', 'auth', 'status'], capture_output=True).returncode:
        sys.exit('The GitHub CLI is not signed in. Run: gh auth login')
    target, port = '%s@%s' % (a.user, a.host), str(a.port)

    work = tempfile.mkdtemp(prefix='shiplane-deploy-key-')
    key = os.path.join(work, 'deploy_key')
    try:
        print('1/5  Making a new SSH key used only for deploying ...')
        if runner(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'github-deploy-%s' % a.repo.replace('/', '-'), '-f', key]).returncode:
            sys.exit('ssh-keygen failed')
        print('2/5  Reading the server\'s host key ...')
        scan = runner(['ssh-keyscan', '-p', port, '-T', '10', a.host], capture_output=True)
        known = (scan.stdout or b'').decode().strip()
        if not known:
            sys.exit('Could not read the server host key')
        print('     The server identifies itself as:\n       ' + fingerprints(known, runner).replace('\n', '\n       '))
        if not a.yes and ask('     Is this the fingerprint of your server? [y/N] ').strip().lower() not in ('y', 'yes'):
            sys.exit('Stopped. Compare it with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the server.')
        known_file = os.path.join(work, 'known_hosts')
        with open(known_file, 'w', encoding='utf-8') as f:
            f.write(known + '\n')
        pin = ['-o', 'UserKnownHostsFile=' + known_file, '-o', 'StrictHostKeyChecking=yes']      # only the host key you just confirmed is trusted from here on
        print('3/5  Putting the key on %s (type the server password if asked), then checking it works ...' % target)
        if runner(['ssh-copy-id', '-i', key + '.pub', '-p', port] + pin + [target]).returncode:
            sys.exit('Could not add the key to the server. Check the address, user and password.')
        if runner(['ssh', '-i', key, '-p', port, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes'] + pin + [target, 'sudo -n true && echo ok'], capture_output=True).returncode:
            sys.exit('The key logs in, but %s cannot use sudo without a password. Use root, or give that user passwordless sudo, then run this again.' % a.user)
        print('4/5  Saving the secrets and variables in %s ...' % a.repo)
        with open(key, encoding='utf-8') as f:
            private = f.read()
        for name, value in (('DEPLOY_HOST', a.host), ('DEPLOY_USER', a.user), ('DEPLOY_SSH_KEY', private), ('DEPLOY_KNOWN_HOSTS', known)):
            gh_set('secret', name, value, a.repo, runner)
        for name, value in (('DEPLOY_DOMAIN', a.domain), ('ADMIN_EMAIL', a.admin_email), ('DEPLOY_PORT', port)) + ((('DEPLOY_DIRECT_HTTPS', 'true'),) if a.direct_https else ()):
            gh_set('variable', name, value, a.repo, runner)
        env = runner(['gh', 'api', '-X', 'PUT', 'repos/%s/environments/production' % a.repo], capture_output=True)
        print('     production environment ' + ('ready (add required reviewers on GitHub if you want an approval step)' if not env.returncode else 'NOT created (you may need admin rights): create it by hand in Settings, Environments'))
    finally:
        if a.keep_key:
            print('     Private key kept at %s' % key)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print('5/5  %s' % ('Private key removed from this computer: it now lives only in GitHub.' if not a.keep_key else 'Done.'))
    if a.run:
        r = runner(['gh', 'workflow', 'run', 'deploy.yml', '--repo', a.repo], capture_output=True)
        print('Started the deploy.' if not r.returncode else 'Could not start it (%s). Merge the workflow into the default branch first, then run it from the Actions tab.' % (r.stderr or b'').decode().strip())
    else:
        print('Ready. Merge to main (or open Actions, deploy, Run workflow) to deploy.')


if __name__ == '__main__':
    main()
