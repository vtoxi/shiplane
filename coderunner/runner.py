"""Runs shell commands on the server: over SSH from your PC/Mac, or directly when the app runs on the server itself.

Mode comes from server.json: "ssh" (default) or "local". Commands are always bash strings, so the rest of the app
does not care which mode is active. SSH logs in with a key file (default) or a password.
"""
import getpass
import os
import shlex
import subprocess
import sys

NOWIN = 0x08000000 if os.name == 'nt' else 0  # no console window flash on Windows


def q(v):
    return shlex.quote(str(v))


def is_local(cfg):
    return cfg.get('mode') == 'local'


def who(cfg):
    return getpass.getuser() if is_local(cfg) else cfg['user']


def script(name):
    """Contents of a script in coderunner/remote/ (LF line endings, ready to pipe into bash/python)."""
    from . import config
    with open(os.path.join(config.REMOTE, name), encoding='utf-8') as f:
        return f.read().replace('\r\n', '\n')


def _askpass():
    """Tiny helper that lets ssh read the password from an environment variable (OpenSSH SSH_ASKPASS). It holds no secret."""
    from . import config
    win = os.name == 'nt'
    path = os.path.join(config.DATA, 'askpass.cmd' if win else 'askpass.sh')
    if not os.path.isfile(path):
        os.makedirs(config.DATA, exist_ok=True)
        if win:
            text = '@"%s" -c "import os;print(os.environ[\'CR_SSH_PASSWORD\'])"\r\n' % sys.executable
        else:
            text = '#!/bin/sh\nprintf \'%s\\n\' "$CR_SSH_PASSWORD"\n'
        with open(path, 'w', newline='') as f:
            f.write(text)
        os.chmod(path, 0o700)
    return path


def environment(cfg):
    """Environment for the ssh process: carries the password (never the command line) in password mode."""
    if is_local(cfg) or cfg.get('auth') != 'password':
        return None
    return dict(os.environ, SSH_ASKPASS=_askpass(), SSH_ASKPASS_REQUIRE='force', CR_SSH_PASSWORD=cfg['password'],
                DISPLAY=os.environ.get('DISPLAY') or 'none:0')


def command(cfg, remote):
    if is_local(cfg):
        return ['bash', '-c', remote]
    common = ['-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10']
    if cfg.get('auth') == 'password':
        auth = ['-o', 'PreferredAuthentications=password,keyboard-interactive', '-o', 'PubkeyAuthentication=no',
                '-o', 'NumberOfPasswordPrompts=1']
    else:
        key = os.path.expanduser(cfg['key'])
        if not os.path.isfile(key):
            raise RuntimeError('SSH key not found: %s (set it in Settings)' % key)
        auth = ['-i', key, '-o', 'BatchMode=yes']
    port = ['-p', str(cfg.get('port') or 22)]
    return ['ssh'] + auth + port + common + ['%s@%s' % (cfg['user'], cfg['host']), remote]


def explain_ssh(out, cfg=None):
    """Plain-language version of a failed ssh attempt (the raw ssh message follows it)."""
    text = (out or '').strip()
    target = '%s port %s' % (cfg.get('host'), cfg.get('port') or 22) if cfg else 'the server'
    low = text.lower()
    if 'timed out' in low or 'no route to host' in low:
        why = ('Could not reach %s. Check the address and the SSH port (many shared hosts use another port, such as 2222, or have SSH switched off), '
               'and that this computer\'s IP is allowed through the host\'s firewall.' % target)
    elif 'refused' in low:
        why = 'Nothing is accepting SSH connections on %s. SSH may be switched off, or the port is different.' % target
    elif 'could not resolve' in low or 'name or service not known' in low:
        why = 'The host name could not be found. Check the spelling of the address.'
    elif 'permission denied' in low or 'authentication failed' in low:
        why = 'The server rejected the login. Check the user name and password (or key); some hosts need SSH access enabled for the account first.'
    else:
        return text
    return '%s\n(%s)' % (why, text.splitlines()[-1] if text else '')


def run(cfg, remote, inp=None, timeout=60):
    p = subprocess.run(command(cfg, remote), input=inp, capture_output=True, timeout=timeout, creationflags=NOWIN, env=environment(cfg))
    return p.returncode, (p.stdout + p.stderr).decode('utf-8', 'replace')


def popen(cfg, remote, stdin=True):
    """Process with stdout+stderr merged, for streaming output (stdin open unless stdin=False)."""
    return subprocess.Popen(command(cfg, remote), stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NOWIN, env=environment(cfg))
