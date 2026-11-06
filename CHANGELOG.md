# Changelog

## Unreleased

### Added
* **Test restore** for Postgres backups (on demand, and optionally after every nightly backup): the backup is loaded into a scratch database and dropped again; a failure raises an alert.

* **Firewall & email** page (firewall rules, email check, status page) and the matching with a lock-out guard, command previews and typed confirmation to switch it on/off.
* **Email check** (MX, SPF, DKIM, DMARC) with a fix for every finding.
* **Public status page** behind an unguessable, rotatable link; installable PWA shell.
* **Scoped API keys**: admin, read-only or deploy-only, with optional expiry.
* **MySQL / MariaDB backups**: manual, nightly, off-server, test restore and restore.

* **All servers**: a list of servers with a side-by-side health overview, switching the managed server, and one-click Prepare (adopt) for a fresh Ubuntu/Debian server.
* **Single sign-on** with any OpenID Connect provider (authorization code + PKCE); existing accounts only.
* **Per-site permissions**: limit an admin or viewer to some sites.
* **Staging to production**: promote a site's live release to another site of the same type, with health check and rollback.
* **Care fixes**: preview of the exact commands, a record of applied fixes, and Undo.
* Status page: choose which sites it shows.

* `scripts/setup_github_deploy.py`: one command creates the deploy SSH key, installs it on the server, pins the host key and saves the repository's secrets and variables.
* **GitHub Actions**: `deploy.yml` (deploys Shiplane to your server after the tests pass, pinned host key, optional approval) and the reusable `deploy-site.yml` (deploy a site from any repository with a deploy-only API key); `ci.yml` is callable.

* Sites page: sites are grouped under their main domain in a collapsible row (subdomain count, health, memory); open state is remembered.

* **Migrate: upload a backup from your computer** (Plesk/cPanel .zip, .tgz, .tar.gz, .tar): resumable pieces, progress, pause/cancel, free-space check, file-type check, then straight into the existing analysis.

* **Files** page: browse, create, preview, edit and save files and folders on the server (atomic saves, conflict detection, secrets hidden, roles and read-only areas).

* **Terminal** page: a real root or app-account shell in the browser (xterm.js, pseudo-terminal), reattachable, idle timeout, super admins only, switch-off, audited open/close (never keystrokes).

### Fixed
* `install_on_server.py`: when Caddy rejects the new site, it now prints Caddy's own error, lists other Caddy files that define the same address, and puts the previous site file back (it used to hide the reason and delete the file).
* `install_on_server.py`: re-running with a new `--domain` now also updates the allowed hostname (it was left on the old one, so the app refused the new address). New `--direct-https` serves `https://DOMAIN` from the server itself (Let's Encrypt); the default is plain http behind a Cloudflare tunnel.
* `server_perf` test no longer races the server thread that releases its request slot.

### Security
* Settings files (`server.json`, `cloudflare.json`, `dnsproviders.json`) are tightened to owner-only even when they already existed with looser permissions.
* A deploy can no longer publish this app's own settings, data or SSH keys; deploying a folder by path is limited to super admins (admins and API keys upload a zip).
* Wrong-typed or missing request values answer `400` instead of a server error.

### Tests and tooling
* GitHub Actions runs the unit tests and the JavaScript checks on every push and pull request.
* New tests for zip upload, Postgres operations, deploy jobs, every API route (empty and junk requests) and the HTTP security rules; `uploads.py`, `dbops.py` and `deploy.py` are under the 95% coverage gate.
