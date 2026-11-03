# Security

Shiplane can run commands as root on your servers, so treat access to it like root access.

## Reporting a problem

Please report vulnerabilities privately (GitHub: Security, Report a vulnerability on this repository) and not in a public issue. Include the version (Settings, About), what you did and what happened. You will get an answer within a few days.

## How it is protected

* Nothing runs arbitrary shell. Fixed scripts in `coderunner/remote/` run on the server and every argument is validated first.
* Requests need an allowed `Host` header; browser writes need the `X-Requested-With: coderunner` header. The only exception is the Git webhook, which is protected by an HMAC or token over the raw body and rate-limited.
* Passwords are salted scrypt hashes, sessions and API keys are stored as hashes, sign-in is throttled, two-factor is supported and can be required.
* `server.json`, `cloudflare.json` and `dnsproviders.json` are written owner-only (`0600`), including when they already existed with looser permissions. Secrets are write-only in the UI and API.
* A deploy cannot publish this app's own settings, data or SSH keys. Only a super admin can deploy a folder by path; others upload a zip.

## Running it safely

* Do not expose the app over plain HTTP. Put it behind HTTPS (for example the Cloudflare tunnel) and set `CODERUNNER_SECURE_COOKIES=1` and `CODERUNNER_ALLOW_HOSTS=your.host`.
* Prefer `CODERUNNER_AUTH=login` with two-factor for more than one person.
* Use SSH keys rather than passwords, and give API keys the least role that works.
* Keep the host updated and back up `data/` and the `*.json` settings files (they hold secrets).
