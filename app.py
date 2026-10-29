#!/usr/bin/env python3
"""Coderunner: deploy and manage your sites, databases and server. Run:  python app.py

Opens http://127.0.0.1:8765 in your browser. Needs Python 3.8+ (and the OpenSSH client when it controls a remote
server). No pip packages. Keep this window open while you use it.

On the server itself:  python app.py --no-browser   with "mode": "local" in server.json (see the Manual page).
"""
import argparse
import os
import sys
import webbrowser

from coderunner import backups, config, monitor, perf, server


def main():
    ap = argparse.ArgumentParser(description='Coderunner deploy and server manager')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--bind', default='127.0.0.1', help='address to listen on (keep 127.0.0.1 unless you know why)')
    ap.add_argument('--allow-host', action='append', default=[], metavar='HOSTNAME',
                    help='extra Host header to accept, e.g. when it runs behind a tunnel (repeatable)')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--reset-2fa', metavar='EMAIL', help='remove two-factor sign-in from an account, then exit (lost phone and recovery codes)')
    ap.add_argument('--reset-code', metavar='EMAIL', help='print a new one-time activation code for an existing account, then exit (recovery)')
    args = ap.parse_args()
    if args.reset_2fa:
        from coderunner import auth
        auth.init()
        try:
            auth.totp_reset(next(u['id'] for u in auth.list_users() if u['email'] == auth.normalize(args.reset_2fa)))
        except (ValueError, StopIteration):
            sys.exit('no account with that email')
        print('Two-factor sign-in removed for %s (signed out everywhere). They sign in with the password and can set it up again.' % args.reset_2fa)
        return
    if args.reset_code:
        from coderunner import auth
        auth.init()
        try:
            code = auth.reset_code_for_email(args.reset_code)
            print('Activation code for %s: %s' % (args.reset_code, code))
            print('Open the sign-in page, choose "Set up your account" and use it (valid 7 days, works once).')
        except (ValueError, LookupError) as e:
            sys.exit(str(e))
        return
    server.STATE['allowed_hosts'] += [h.lower() for h in args.allow_host]
    try:
        srv = server.make_server(args.port, args.bind)
    except OSError as e:
        sys.exit(str(e))
    pinned = bool(os.environ.get('CODERUNNER_TOKEN'))
    url = 'http://127.0.0.1:%d/?t=%s' % (server.STATE['port'], server.STATE['token'])
    shown = 'http://127.0.0.1:%d/?t=<the CODERUNNER_TOKEN value>' % server.STATE['port'] if pinned else url  # keep a pinned secret out of logs
    if server.STATE['auth'] == 'login':
        url = 'http://127.0.0.1:%d/' % server.STATE['port']
        shown = url + '  (sign in with your account)'
    perf.start()                        # memory housekeeping, request limits (see Settings, Performance)
    perf.apply_limits(config.load_server())
    monitor.start(config.load_server)   # 24-hour site uptime history, resource alerts
    backups.start(config.load_server)   # nightly backups when switched on
    print('Coderunner is running. Keep this window open.\n  %s\nPress Ctrl+C to stop.' % shown, flush=True)
    if args.bind not in ('127.0.0.1', 'localhost'):
        print('WARNING: listening on %s. Anyone who can reach this port and has the token can control the server.' % args.bind, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
