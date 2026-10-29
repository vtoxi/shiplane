# Changelog

## Unreleased

### Added
* **Test restore** for Postgres backups (on demand, and optionally after every nightly backup): the backup is loaded into a scratch database and dropped again; a failure raises an alert.

### Security
* Settings files (`server.json`, `cloudflare.json`, `dnsproviders.json`) are tightened to owner-only even when they already existed with looser permissions.
* A deploy can no longer publish this app's own settings, data or SSH keys; deploying a folder by path is limited to super admins (admins and API keys upload a zip).
* Wrong-typed or missing request values answer `400` instead of a server error.

### Tests and tooling
* GitHub Actions runs the unit tests and the JavaScript checks on every push and pull request.
* New tests for zip upload, Postgres operations, deploy jobs, every API route (empty and junk requests) and the HTTP security rules; `uploads.py`, `dbops.py` and `deploy.py` are under the 95% coverage gate.
