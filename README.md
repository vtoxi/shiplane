# Shiplane

Deploy sites and manage a Linux server (sites, databases, Cloudflare mapping, services, updates) from a browser.
One project, one command, one API.

```
python app.py        # Windows: start.bat   Mac/Linux: ./start.sh
```

Needs only Python 3.8+ (and the `ssh` command when it controls a remote server). No pip packages, no Node.

## What it does

* **Deploy** static, Node, .NET and PHP sites with automatic rollback, environment variables and optional Postgres databases.
* **Apps**: install Node.js, Python, PHP and .NET versions side by side, MariaDB / PostgreSQL / Redis, and WordPress, Laravel, phpMyAdmin or Adminer on their own address and folder.
* **Migrate** sites and databases from cPanel or Plesk backups, or from a live server, with live step-by-step progress.
* **Operate**: Cloudflare tunnel and DNS mapping, cron jobs, disk cleanup, SSL and uptime checks (interval is configurable), backups, alerts, users with roles and two-factor sign-in.
* **Feels managed**: a server care checklist with a security score and safe one-click fixes, early warnings (disk forecast, certificates, crash loops, overload, out-of-memory, pending restart) and automatic repair of crashed sites and services.
* **Live and interactive**: metrics charts (live and 24 hours) with tooltips, clickable cards that open the details behind them, start/stop of services and containers with their memory shown.
* **Docker image**: `docker compose up -d` runs Shiplane in a hardened container (read-only, unprivileged, 256 MB) that manages the machine it runs on, or any VPS, over SSH.
* **Your own nameservers and direct HTTPS**: run authoritative DNS (NSD) for your domains, edit records, open firewall ports only after you confirm, let sites answer on 443 themselves. **Connect your own Cloudflare account** from Settings with an API token.
* **Deploy from Git**: connect a repository per site, deploy on demand or on every push (GitHub/GitLab webhook), optional npm build or your own commands, Docker and command sites, GitHub/GitLab sign-in to pick private repositories, new site straight from a repository, automatic rollback.
* **Light on the server**: a few tens of MB at idle, a Light / Balanced / Full power profile you can switch in Settings, shared and cached status reads, bounded threads and logs.
* Everything is also available through a JSON API with API keys (`GET /api/routes`).

## Quick start

1. Run it (command above), open the link it prints.
2. Open **Settings** and enter your server address, user and SSH key or password (see [server.example.json](server.example.json)). A Cloudflare token is optional ([cloudflare.example.json](cloudflare.example.json)).
3. Settings and secrets live in `server.json`, `cloudflare.json` and `data/`; all three are git-ignored.
The full operating manual is inside the app (**Manual** page, source: [web/manual.md](web/manual.md)).

## Architecture

```
app.py                      entry point (starts the server)
coderunner/                 backend, standard library only
  server.py                 HTTP server, routing, auth (token cookie or API key), static files
  config.py                 paths, validation patterns, server.json / cloudflare.json
  runner.py                 runs bash on the server: over ssh, or directly in "local" mode
  deploy.py                 validation, deploy/rollback/env/logs/status, background jobs
  dbops.py  serverops.py    Postgres and server operations (allowlisted, validated)
  cloudflare.py             tunnel routes + DNS (never overwrites an existing record)
  auth.py                   accounts, scrypt passwords, sessions, roles, login throttling (SQLite)
  apikeys.py  activity.py   Bearer keys (hashed), activity log
  uploads.py                safe .zip upload
  migrate.py  migrator.py   read an old cPanel/Plesk account, then move sites and databases with live progress
  plesk.py                  reads Plesk backup exports (tgz/tar/zip) without extracting them
  maintenance.py            domain/SSL health, per-site scheduled tasks (cron), disk usage and cleanup
  monitor.py  billing.py    live logs, 24 h uptime history, Hetzner/Cloudflare cost estimate
  alerts.py  backups.py     Telegram/webhook alerts; nightly backups, retention, restore
  settings.py               small JSON store for those settings (data/settings.json)
  remote/                   the fixed scripts that run on the server
web/                        frontend, no build step to run it
  index.html  app.css       shell + compiled Tailwind 3
  js/                       router, store, component kit, pages
  manual.md                 the in-app manual
frontend/                   design system source (Tailwind 3) - only to change the look
scripts/install_on_server.py  installs/upgrades the app on the server (systemd + Caddy)
tests/                      end-to-end and unit checks (fake Cloudflare, real server)
```

The UI only calls `/api/*`, so any other client (a mobile app) can use the same API with an API key:
`Authorization: Bearer crk_...` (create one in Settings). `GET /api/routes` lists every endpoint.

## Design system

Tailwind 3 with design tokens as CSS variables ([frontend/src/input.css](frontend/src/input.css)): a neutral surface scale, one brand colour, four status colours, light and dark themes. [frontend/tailwind.config.js](frontend/tailwind.config.js) only maps those variables to utilities (`bg-surface`, `text-muted`, `border-line`, `bg-brand`, `text-ok`, ...), so a re-skin means editing the variables.

Reusable pieces live in two places and nowhere else:

* **CSS components** (`@layer components`): `btn` (+`btn-primary|secondary|ghost|danger|danger-soft`, `btn-sm`, `btn-icon`), `input`, `select`, `textarea`, `label`, `card`, `alert-*`, `badge-*`, `table`, `nav-link`, `tabs`, `modal`, `toast`, `console`, `switch`, `segmented`, `meter`, `kv`.
* **JS components** ([web/js/kit.js](web/js/kit.js)): `Button`, `Badge`, `Card`, `Alert`, `Field`, `Input`, `Select`, `Switch`, `Segmented`, `Tabs`, `Table`, `Stat`, `Meter`, `EmptyState`, `PageHeader`, `KeyValue`, `KeyValueEditor`, `Console`, `Modal`, `confirmDialog`, `toast`, `icon`. Everything is built with real DOM nodes (`h()`), never `innerHTML`.

To change the look:

```
cd frontend
npm install
npm run build        # writes web/app.css
```

## Tests

```
python tests/test_cloudflare.py     # mapping logic against a fake Cloudflare API
python tests/test_app.py            # the whole app against the real server (creates and removes apptest* sites)
python tests/test_auth.py           # sign-in mode: accounts, roles, lockout, sessions, headers (no SSH needed)
python tests/test_ops.py           # alerts, backup schedule/retention, resource alerts (fake services)
python tests/run_unit_tests.py     # every no-server unit test + 95% coverage gate (pip install coverage)
python tests/test_maintenance.py   # scheduled tasks, disk cleanup rules (fake server, no SSH)
python tests/test_migrate.py   # reading an old cPanel backup (synthetic archive, no server)
python tests/test_live.py          # log commands, uptime logic and billing against a fake Hetzner API
python tests/test_local_mode.py     # a throwaway copy running ON the server, through an SSH tunnel
node tests/check_js.mjs             # every frontend module parses
```

## Running on the server

```
python scripts/install_on_server.py --domain server.example.com --admin-email you@example.com
```

Works on a fresh Debian/Ubuntu VPS from any provider: it installs python3, Caddy and cloudflared when missing. Open ports (or a Cloudflare tunnel) in your provider's firewall yourself.

Sign-in mode (`CODERUNNER_AUTH=login`), accounts in SQLite, roles viewer / admin / super admin. See the Manual for the first sign-in.

## License

[MIT](LICENSE): free to use, change and share, including commercially. Keep the copyright notice: Shiplane was created by **Faisal Shafi**, and copies or forks must credit the original author.

## Security notes

Listens on `127.0.0.1` only; needs the launch token, a signed-in account (server mode) or an API key; validates every argument before it reaches a shell;
never shows secret values back; never logs them. `cloudflare.json` may hold an API token: keep it private (it is git-ignored).
