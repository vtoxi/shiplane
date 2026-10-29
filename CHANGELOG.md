# Changelog

## Unreleased

### Added
* **Test restore** for Postgres backups (on demand, and optionally after every nightly backup): the backup is loaded into a scratch database and dropped again; a failure raises an alert.

* **Firewall & email** page (firewall rules, email check, status page) and the matching with a lock-out guard, command previews and typed confirmation to switch it on/off.
* **Email check** (MX, SPF, DKIM, DMARC) with a fix for every finding.
* **Public status page** behind an unguessable, rotatable link; installable PWA shell.
* **Scoped API keys**: admin, read-only or deploy-only, with optional expiry.
* **MySQL / MariaDB backups**: manual, nightly, off-server, test restore and restore.

### Security
* Settings files (`server.json`, `cloudflare.json`, `dnsproviders.json`) are tightened to owner-only even when they already existed with looser permissions.
* A deploy can no longer publish this app's own settings, data or SSH keys; deploying a folder by path is limited to super admins (admins and API keys upload a zip).
* Wrong-typed or missing request values answer `400` instead of a server error.

### Tests and tooling
* GitHub Actions runs the unit tests and the JavaScript checks on every push and pull request.
* New tests for zip upload, Postgres operations, deploy jobs, every API route (empty and junk requests) and the HTTP security rules; `uploads.py`, `dbops.py` and `deploy.py` are under the 95% coverage gate.
