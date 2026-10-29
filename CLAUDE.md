# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Shiplane (internal package name `coderunner`; do not rename it, the live server and `CODERUNNER_*` env vars depend on it): a browser UI + JSON API to deploy sites and manage a Linux server (sites, Postgres, Cloudflare tunnel/DNS, services, backups). Backend is **Python standard library only** (3.8+, no pip), frontend is vanilla JS ES modules with **no build step to run**. `README.md` has the file map; the in-app Manual is `web/manual.md`.

## Commands

```
python app.py                 # run (start.bat on Windows, ./start.sh elsewhere); http://127.0.0.1:8765
python app.py --no-browser    # on the server itself (with "mode": "local" in server.json)

python tests/run_unit_tests.py    # ALL no-server unit tests + 95% coverage gate on the backend modules listed in the script (needs `pip install coverage`)
python tests/test_<name>.py       # one test file (e.g. test_cloudflare.py, test_auth.py, test_ops.py); fakes are in tests/fakes.py, tests/mock_cloudflare.py
python tests/test_app.py          # needs real SSH to the server in server.json; creates/removes apptest* sites
python tests/test_local_mode.py   # throwaway copy over an SSH tunnel
node tests/check_js.mjs           # every frontend module parses, no unused imports, and no replaceChildren() that could print "null" (use fill() from kit.js)

cd frontend && npm install && npm run build   # recompile web/app.css (Tailwind 3); only when changing the design
```

Tests are plain scripts (run each directly); there is no pytest runner or single-test selector. A coverage run leaves `.coverage` in the repo root; don't commit it.

## Architecture notes

- `coderunner/server.py` is the single HTTP server: routing, auth, static files for `web/`. The UI only calls `/api/*`; `GET /api/routes` lists endpoints. Any client can use `Authorization: Bearer crk_...` API keys.
- Two auth modes: `token` (default, local launch link -> cookie) and `login` (`CODERUNNER_AUTH=login`, accounts/roles in SQLite via `auth.py`). Requests need an allowed Host header; browser writes need a custom header.
- Nothing runs arbitrary shell: `runner.py` executes **fixed scripts from `coderunner/remote/`** on the server (over `ssh`, or directly when `mode` is `local`), and every argument is validated against the regexes in `config.py` (`NAME_RE`, `DB_RE`, ...) first. Keep new operations on that pattern.
- Config/state files: `server.json` (ssh/local, host, domains), `cloudflare.json`, `data/` (activity log, key hashes, settings; override with `CODERUNNER_DATA`). `DEFAULT_SERVER` in `config.py` is used when `server.json` is absent.
- SSH logs in by key or password (`config.py` `auth`/`password`/`port`; `runner.py` passes the password to ssh through `SSH_ASKPASS` and an env var, never argv). The password is write-only: `config.public_server()` strips it from API output.
- `migrate.py` reads an old account (cPanel `cpmove` or Plesk export backup in place via `plesk.py`, or live cPanel/Plesk via `remote/discover.py`) and checks/installs components; `migrator.py` runs the migration as a background `Run` of named steps polled by `web/js/pages/migration-run.js` and the sidebar dock in `main.js`. Site type `php` (Caddy `php_fastcgi` + PHP-FPM, no systemd unit) exists for it. Sample input: `python scripts/make_sample_backup.py`.
- `apps.py` is a catalog-driven installer (Node/Python/PHP/.NET, MariaDB/PostgreSQL/Redis, WordPress/Laravel/phpMyAdmin/Adminer): every install is rebuilt server-side from the catalog and the fixed scripts in `coderunner/remote/apps/`; folders are limited to `/var/www`, `/srv`, `/home` (`check_path`). Uptime-check frequency is a setting (`monitor.schedule()`).
- `perf.py` holds the performance profiles (light/balanced/full): request slots (`BoundedServer` in `server.py`), the shared status cache (`perf.cached`, dropped with `perf.invalidate('status')` after any change), job/run log caps and pruning, the memory watchdog, systemd CPU/IO limits. Anything that changes what `deploy.status` reports must call `perf.invalidate('status')`. `remote/status.py` must stay subprocess-light (one batch per question, not per site).
- `care.py` (+ `remote/care_check.py`, `remote/care/*.sh`) is the server care checklist and its guarded fixes (never lock the user out; judged on fresh facts, not on what the browser saw). `watch.py` (smart alerts) and `heal.py` (auto-heal) run inside `monitor.run_round`, share `data/care.json` (keep it under 20 KB) and the cached status; `deploy.ON_SITE_ACTION` tells heal what the user stopped on purpose.
- `caredetail.py` backs the expandable Care cards (fixed commands, validated folder/IP only). `metricslog.py` is the 24 h metrics ring (fed by `monitor.run_round`, file under 30 KB). `gitdeploy.py` + `remote/gitfetch.sh`/`gitkey.sh` implement Deploy from Git: builds run as the unprivileged `shiplane-build` user; the public `POST /api/hooks/git` is the ONLY write route exempt from the `X-Requested-With` check and is protected by an HMAC/token over the raw body (`ctx.raw`), rate-limited. `gitproviders.py` is the GitHub/GitLab sign-in (device login or pasted token kept in memory only, 30 min / 5 sessions, repo/branch lists, detection, one-click deploy key + webhook via `gitdeploy.provider_link`); `gitdeploy.create` makes a new site from a repository. Site types `command` (systemd unit running `.shiplane-start.sh`) and `docker` (`remote/deploy_docker.sh`, container `shiplane-NAME`, no systemd unit; `status.docker_site`) sit next to static/node/dotnet/php. `dns.py` (+ `remote/dns_apply.sh`, `dns_ports.sh`, `dns_status.sh`, `site_direct.sh`) holds zones/records in `data/settings.json`, renders zone files for NSD (checked with `nsd-checkzone` before anything is replaced) and runs only after Apply; firewall ports and per-site direct HTTPS (`http://host, https://host {` Caddy header, `direct` in site meta) need typed confirmations. `cloudflare.connect/disconnect` write `cloudflare.json` from the Settings screen (token write-only). `dnsproviders.py` is Route 53 (SigV4 signed with stdlib) and DigitalOcean: A records to the server IP, credentials in `dnsproviders.json`; a deploy with `mapDns` forces `direct`. Provider credentials are per install (Integrations page), never pushed by `install_on_server.py`. `wildcard.py` + `remote/wildcard.sh`: Let's Encrypt `*.zone` via Certbot DNS plugins; installs `/usr/local/sbin/shiplane-wildcard-attach`, which `deploy.sh` and `site_direct.sh` call so direct-HTTPS sites import the zone's Caddy snippet. `serverops.py` allow-lists services (start/stop/enable/disable; docker is stopped together with its socket; caddy/cloudflared cannot be stopped when the app runs on the server) and tells `heal` via `ON_SERVICE_ACTION` so deliberate stops are respected.
- Frontend: `web/js/kit.js` is the component kit (DOM via `h()`, never `innerHTML`); pages live in `web/js/pages/`. Design tokens are CSS variables in `frontend/src/input.css`; compiled `web/app.css` is committed.
- Docker: `Dockerfile`, `docker/entrypoint.sh`, `docker-compose.yml`, `.github/workflows/docker.yml` (publishes to Docker Hub on a `v*` tag). Inside the container `CODERUNNER_CONFIG`/`CODERUNNER_DATA` point into the `/data` volume (server.json, cloudflare.json, state), `HOME=/data` so the container's own SSH key is `/data/.ssh/id_ed25519`; always SSH mode (a container cannot run host scripts). `.dockerignore` keeps secrets and state out of the image.
- `scripts/install_on_server.py` installs/upgrades the app on the server (systemd + Caddy).

## Caution

`cloudflare.json`, `dnsproviders.json` and `server.json` can hold secrets (an API token, an SSH password) and are git-ignored. Never print, copy or commit their contents.
