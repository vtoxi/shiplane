# Shiplane Manual

Shiplane is one small app that lets you publish websites and manage a server without typing server commands. You run it on your own computer (or on the server itself), open it in the browser, and click.

This page is the operating manual. Use the search box on the left to find a topic.

**How to read the examples.** Words in angle brackets are yours to fill in: `<server-ip>` is your server's address, `<server-user>` your login on it, `<your-domain>` a domain you own, `<shiplane-address>` the address Shiplane itself is served on, `<your-email>` your e-mail. On the server the app's internal name is `coderunner`: its folder (`/opt/coderunner`), service, system user and `CODERUNNER_*` settings keep that name, so type them exactly as shown.

## Quick start

1. Install **Python 3.8 or newer** (python.org). Windows 10/11, macOS and Linux already have the `ssh` command; nothing else is needed.
2. Double-click `start.bat` (Windows) or run `./start.sh` (Mac/Linux). A terminal window opens and your browser opens this app. **Keep the terminal window open** while you work.
3. Open [Settings, Server connection](#/settings?tab=server) and enter the server address, **your own** server user and the path to **your own** SSH private key. Press **Test connection**, then **Save**.
4. Open **Deploy**, choose a type, pick your build folder, press **Deploy**.

> The link in the terminal contains a one-time access token. Only that link (and the browser tab it opens) can use the app.

## How it works

There is one project and one command. `python app.py` starts a small web server on your computer that does two jobs: it serves this user interface, and it exposes a JSON **API** (everything under `/api/`). The interface only ever talks to that API, so any other program, such as a future mobile app, can do the same things.

The app does not install anything on your computer. To change the server it runs short, fixed scripts over SSH (or directly, in server mode).

| Piece | What it does |
|---|---|
| Cloudflare | Public DNS and HTTPS. Visitors never see your server's IP. |
| Cloudflare Tunnel (`cloudflared`) | An outbound connection from the server to Cloudflare, so no web ports are open on the server. |
| Caddy | The web server on the server. One small file per site in `/etc/caddy/sites/`. |
| systemd services | Run your .NET and Node apps and restart them if they crash. |
| Postgres / SQL Server | Databases, reachable only from the server itself. |
| Releases | Each deploy is a new folder in `/srv/<site>/releases/`; `current` points to the live one. The last 3 are kept. |

A request travels: browser, Cloudflare, tunnel, Caddy on `127.0.0.1:80`, your site (static files, or your app on its local port).

## Running it

### On your PC or Mac (control a remote server)

Run `start.bat` / `./start.sh`. The default mode is **ssh**: the app uses your SSH key to run commands on the server. Nothing runs on the server except the commands you trigger.

### On the server itself (recommended for daily use)

The app can live on the server and be opened from anywhere, protected by an email-and-password sign-in. From your own computer, run once:

```
python scripts/install_on_server.py --domain <shiplane-address> --admin-email <your-email>
```

It creates a system user `coderunner` (with passwordless sudo, because the app manages the server), puts the code in `/opt/coderunner`, keeps settings in `/etc/coderunner.env` (readable only by root), data in `/var/lib/coderunner`, starts a systemd service on `127.0.0.1:8765`, and adds the Caddy site for the domain. A hostname one level under a wildcard domain (like `server.example.com`) needs no Cloudflare change. Run the same command again later to **upgrade**: settings, accounts and data are kept.

**First sign-in.** The `--admin-email` account is created without a password. Fetch its one-time activation code from the server, then open the site, choose **Set up your account**, and enter your email, the code and a password of your choice:

```
ssh -i ~/.ssh/id_ed25519 <server-user>@<server-ip> "sudo cat /var/lib/coderunner/first-admin-code.txt"
```

The code works once and the file is deleted afterwards.

**Private access (optional).** `connect-server.bat` / `connect-server.sh` open an SSH tunnel and the sign-in page at `http://127.0.0.1:8765`, without using the public address.

Notes for server mode:

* The folder picker is not available: use **Upload a .zip** in Deploy. Through the public address Cloudflare accepts uploads up to 100 MB; for bigger builds use the private tunnel.
* This app's own site is called `coderunner` (its internal name). It is protected: it cannot be deleted, stopped or deployed over from inside the app.
* To change a setting later, edit `/etc/coderunner.env` and run `sudo systemctl restart coderunner`.

> Anyone who signs in as an admin can deploy to and restart your server, and a super admin can do everything. Add only people you trust, and use strong passwords.

## Sign-in, users and roles

Server mode (and any copy started with `CODERUNNER_AUTH=login`) asks everyone to sign in. On your own computer the launch link is enough and there are no accounts.

* **Whitelist.** Only emails that exist on the **Users** page can sign in. Nobody can create an account for themselves.
* **First password.** A new account has no password. The person sets it themselves with a one-time **activation code** (valid 7 days, works once), on the sign-in page under **Set up your account**. Passwords are never shown to or chosen by the admin.
* **Roles.**

| Role | Can do |
|---|---|
| Viewer | Read-only: sites, status, activity, the manual. |
| Admin | Everything about sites: deploy, restart, rollback, environment, logs, databases, server operations, Cloudflare mapping. |
| Super admin | Also users, API keys, connection settings and rebooting the server. |

* **Add a user** (Users page): email and role. The activation code is shown once; hand it over privately.
* **Reset password:** creates a new code and signs that person out everywhere. They set a new password with it.
* **Disable / enable / delete:** disabling signs the person out at once. You cannot disable, delete or demote yourself or the last super admin.
* **Change your own password:** [Settings, Account and security](#/settings?tab=account).
* **Passwords:** at least 10 characters with letters and numbers or symbols; stored only as salted scrypt hashes.
* **Lockout:** 5 wrong attempts for an email (or 25 from one address) lock sign-in for about 10 minutes.
* **Sessions** end after 12 hours without use, or 30 days at most, and when you sign out.

### If you are locked out

Run this on the server (over SSH). It prints a fresh activation code for an existing account and signs it out:

```
ssh <server-user>@<server-ip> "sudo -u coderunner env CODERUNNER_DATA=/var/lib/coderunner python3 /opt/coderunner/app.py --reset-code <your-email>"
```

## The pages

The sidebar has four groups. Every page and tab named in this manual is a link: click it to go there.

* **Sites:** [Sites](#/sites), [Deploy](#/deploy), [Databases](#/databases), [Apps](#/apps)
* **Server:** [Server](#/server), [Metrics](#/metrics), [Server care](#/care), [Maintenance](#/maintenance), [Migrate](#/migrate)
* **Network:** [Domains](#/domains), [DNS hosting](#/dns)
* **Workspace:** [Integrations](#/integrations), [Activity](#/activity), [Users](#/users), [Manual](#/manual), [Settings](#/settings)

On a phone, the tabs of a page become a drop-down list at the top.

### Sites

The home page. Shows how many sites exist, how many need attention, memory and uptime, and a table of every site with its type, service state, public check, release age and memory. Use the search box (press `/` to jump to it) and the type filter. Click a row to open the site.

**Needs attention** means the service is not running, or the public address does not answer with a success code.

### Site detail

One page per site. At the top right: **Check now** (checks the public address at once, shows the answer time or the error, and records it like a scheduled uptime check), **Open site** and **Redeploy**. Tabs:

* **Overview**: health (public check, service, memory, restarts), details (address, type, port, database, current release, who deployed it) and the uptime bars.
* **Environment**: every variable with its value, hidden until you press the eye. Edit values in place, **Add variable**, **Paste .env** (many at once), remove (press again to undo). **Save N changes and restart** applies everything with one restart. Only admins can read values, and each read is written to [Activity](#/activity).
* **Releases**: every kept release. Pick an older one and press **Activate** to roll back instantly.
* **Git**: the connected repository with **Deploy now**, the last deploy and its result. Webhook, build settings, private-repository key and disconnect are folded sections below (see Deploy from Git).
* **Logs**: the service log, with search by text, level and time.
* **Config**: read-only view of the generated Caddy file, systemd unit and metadata.
* **Manage**: restart / stop / start, run a one-off command, and **Delete** (type the site name to confirm; optionally also drop its database and remove its Cloudflare or DNS records).

### Change a site's address

On a site's **Manage** tab, **Address** takes the new hostname (for example `shop.<your-domain>` instead of `store.<your-domain>`). Type it again to confirm. Shiplane then:

* rewrites the site's Caddy file (its address, its own HTTPS line and the redirects that point to it), checked by Caddy before anything is replaced;
* updates the site's metadata, so Sites, uptime checks, Git deploys and redeploys use the new name;
* if you leave it on, replaces the old address in the site's environment variables (for example `APP_URL`) and restarts the app;
* if you leave it on, adds the new name in Cloudflare or in Route 53 / DigitalOcean, and only then removes the old one;
* attaches a wildcard certificate if one covers the new name.

The files, service, database, releases and Git connection do not change. The old address stops answering; add it under redirects on a redeploy if you need old links to keep working. The address of Shiplane itself is set when it is installed and cannot be changed here.

### Deploy

[Deploy](#/deploy) publishes a new site or a new version of an existing one.

1. **Name and type.** The name becomes the folder, service and database name. Types: *Static*, *.NET*, *Node / SSR*, *PHP*, *Commands*, *Docker*.
2. **Source.** A folder, an uploaded .zip, **From Git**, or **Empty site** (the address with a starter page, code later). Only a super admin can deploy a folder by its path on the computer running the app, and nobody can deploy the app's own settings, data or SSH key folders; admins and API keys upload a .zip.
3. **Address.** Pick a subdomain and one of your domains (the wildcard domains in [Settings, Server connection](#/settings?tab=server) plus the domains of your connected DNS providers).
4. **Runtime** (apps only). DLL or start file, a local port (a free one is suggested), an optional Postgres database, environment variables.
5. **DNS.** **Map the hostname in Cloudflare** (tunnel), or **Add an A record** in Route 53 / DigitalOcean (served directly with its own certificate). Only one of the two.
6. **Review** on the right shows exactly what will happen. Press **Deploy** and watch the live log.

If the server part succeeds but the DNS step fails, the site stays deployed and the log says what to do.

### Databases

[Databases](#/databases) has one tab per engine, each showing whether its server is installed and running:

* [PostgreSQL](#/databases?tab=postgres): create a database, back one up, drop one.
* [MySQL / MariaDB](#/databases?tab=mysql): create and drop (install MariaDB from [Apps](#/apps?tab=service) first).
* [SQL Server](#/databases?tab=mssql): create and drop in the SQL Server container, with its sa password.
* [Backups](#/databases?tab=backups): nightly backups, the off-server copy, and every backup with **Download**, **Restore** and delete. Backups cover the PostgreSQL databases only for now.

**New database** takes a name and, optionally, a user and a password (8 to 64 characters, no quotes, `$` or spaces). Left empty, the user gets the database's name and the password is generated. The password is shown once and never stored.

### Server

[Server](#/server): memory, disk, swap and uptime; every system service with **Logs** and **Restart**; Docker containers; pending updates with an **Install updates** button; firewall and fail2ban status; listening ports; top processes; recent logins; and **Reboot server** (type `reboot` to confirm).

### Domains

[Domains](#/domains) has a tab per DNS provider, each showing whether it is connected (connect them on [Integrations](#/integrations?tab=dns)):

* [Cloudflare](#/domains?tab=cloudflare): check that the token and tunnel are healthy, see every hostname the tunnel serves and its DNS state, **map** new hostnames and **remove** old ones. It never overwrites a DNS record that already exists; if one does, it says "conflict". To take such a name over, a super admin presses **Replace record**: you see where it points now and must type the hostname; only that name's A/AAAA/CNAME record is deleted. Mail and TXT records are never touched.
* [Amazon Route 53](#/domains?tab=route53) and [DigitalOcean](#/domains?tab=digitalocean): every A record, those pointing at this server marked; **Add records** and **Remove**. Never overwrites a record that points elsewhere.
* [Wildcard certificates](#/domains?tab=certs): one Let's Encrypt certificate for a whole domain (see Wildcard certificates below).

### DNS hosting

[DNS hosting](#/dns) runs your own nameservers on the server (see Your own nameservers). Super admins only.

### Integrations

[Integrations](#/integrations) is where every outside service is connected. Each install connects its own accounts; secrets are checked, stored on the server only and never shown again.

* [DNS providers](#/integrations?tab=dns): Cloudflare, Amazon Route 53, DigitalOcean.
* [Cloud providers](#/integrations?tab=cloud): Hetzner Cloud (a read-only token for the server cost on Sites).
* [Git providers](#/integrations?tab=git): the GitHub and GitLab OAuth client ids for signing in on Deploy.
* [Notifications](#/integrations?tab=alerts): Slack, Discord, Telegram, e-mail (Gmail) and any webhook.

### Activity

[Activity](#/activity): a log of everything changed through the app: who, what, when. Secret values are never logged. It is kept in `data/activity.jsonl` next to the app.

### Settings

[Settings](#/settings) holds the app's own preferences: [Account and security](#/settings?tab=account) (password, two-factor, security policy), [Server connection](#/settings?tab=server) (mode, server, user, key, wildcard domains), [Monitoring](#/settings?tab=monitoring) (warnings and uptime checks), [Performance](#/settings?tab=performance), [API keys](#/settings?tab=api) and [Appearance and about](#/settings?tab=about).

## Deploying each type

### Static (Angular, React, plain HTML)

Build locally, then deploy the output folder.

```
ng build                      # Angular: output is dist/<app>/browser
npm run build                 # React/Vite: output is dist
```

Caddy serves the files and sends unknown paths to `index.html`, so client-side routing works.

### .NET

```
dotnet publish -c Release -o publish
```

Deploy the `publish` folder. Enter the main DLL (for example `MyApi.dll`) and a port. The app is started with `ASPNETCORE_URLS=http://127.0.0.1:<port>`. The server has the **.NET 10** runtime; an app built for .NET 8 or 9 needs the variable `DOTNET_ROLL_FORWARD=Major`.

### Node and Angular SSR

Deploy the folder that contains your server entry. For Angular SSR (`ng build` with SSR) that is `dist/<app>`, with the start file `server/server.mjs`. If a `package.json` is present, dependencies are installed on the server (`node_modules` is not uploaded). The app gets `PORT` and `HOST=127.0.0.1`, so it must listen on `process.env.PORT`.

## Environment variables and secrets

Set variables when you deploy or later on the site's **Environment** tab. They live in `/etc/<site>.env` on the server, readable only by root and the app. Values stay hidden until an admin presses the eye on that tab, and every read is written to [Activity](#/activity). A database you create through the app adds `DATABASE_URL` and `ConnectionStrings__Default` automatically.

## Domains and Cloudflare

* **Wildcard route.** `*.example.com` already points at the tunnel, so `anything.example.com` works as soon as a site is deployed under that name. Unknown names return a plain 404.
* **Other domains.** Each hostname needs a tunnel route and a proxied DNS record. With a token configured, tick **Map the hostname in Cloudflare** in [Deploy](#/deploy), or use [Domains, Cloudflare](#/domains?tab=cloudflare). Without a token, ask the owner.
* **Two-level names** like `a.b.example.com` are not covered by Cloudflare's free certificate; HTTPS there needs Cloudflare's paid certificate add-on.
* **Conflicts.** If a hostname already has an A or CNAME record elsewhere, the app leaves it alone and says so.

### Creating the API token (owner only)

Cloudflare dashboard, My Profile, API Tokens, Create Token, Custom token. Permissions:

* Account, Cloudflare Tunnel, **Edit**
* Zone, Zone, **Read**
* Zone, DNS, **Edit**

Limit it to your account (and the zones you want). Paste it on [Integrations, DNS providers](#/integrations?tab=dns). Without a token the app still works; only Cloudflare mapping is switched off.

## Server maintenance

* **Updates.** Server page, Updates, **Install updates**. If it says a reboot is needed, reboot when convenient.
* **Restart a service** if a site misbehaves: Server page, Services, Restart. Check **Logs** first.
* **Reboot.** All sites are offline about a minute. Services start again on their own.
* **Firewall and fail2ban** are shown read-only; only SSH should be reachable from outside.

## Apps (runtimes, databases and web apps)

Open [Apps](#/apps) to install what your sites need. Each app is a card with a **version picker**, a status badge and an **Install** button.

* **Runtimes** (Node.js, Python, PHP, .NET) install side by side, so you can have Node 20 and 22 at once (commands `node20`, `node22`, `python3.12`, ...). A version that is already installed shows "(installed)" and its Install button is disabled.
* [Databases and services](#/apps?tab=service) (MariaDB, PostgreSQL, Redis) install from your server's own packages.
* **Web apps** (WordPress, Laravel, phpMyAdmin, Adminer) get **their own address and folder**: enter a domain or sub-domain (for example `blog.example.com`), then pick or create the folder with the **server folder browser** (usually `/var/www/blog.example.com`). The folder must be empty, so nothing is ever overwritten. Installing inside a path such as `example.com/blog` is not supported: use a sub-domain.
* If something an app needs is missing (PHP, MariaDB, Composer), tick **Install what is missing** and it is installed first.
* phpMyAdmin and Adminer are always put behind a site user and password.
* Installs run from the official sources (nodejs.org, the PHP packages for Ubuntu/Debian, Microsoft's dotnet-install, astral.sh uv for Python, wordpress.org, ...). Ubuntu and Debian servers are supported.

## Migrating from other hosting

Open **Migrate** to move sites and databases from shared hosting (cPanel) or an older server to this server.

1. **Choose the source.** Either a backup file on this computer, or a live server. Backup files: a cPanel *full account backup* (Backup, then Download a Full Account Backup; `cpmove-USER.tar.gz`) or a backup exported from Plesk (Backup Manager, download as `.tgz`, `.tar` or `.zip`; the older `.tzst` format is not supported). The kind is recognised automatically. Plesk backups are read from Plesk's documented layout (info XML, `backup_user-data` archives, `databases/`, `sites/`) and are best-effort until tried on your own backup. A live server over SSH with a **root** login (key or password; password is used once and never saved). A normal shared-hosting login cannot be read live: use the backup file. Some hosts use an SSH port other than 22; there is a port field.
2. **Check the new server.** The page lists what the move needs (PHP, MySQL/MariaDB, PostgreSQL, a mail server). Missing PHP and databases can be installed with one click. A mail server is never installed automatically: it needs DNS records and care, so set it up first or keep mail with a hosted provider.
3. **Choose what to move and start.** Use **Browse...** to pick a backup file on this computer. Tick the sites and databases to move. Names are kept as they are; under **Customize** you can change a database's name, user and password (leave the password empty to get a strong generated one) and a site's name. Rename only when you have to (for example the name is taken on the new server): changing the *address* of a WordPress or other PHP site does not change its database, which still contains the old address, so the app must be updated before it works there.  A progress view shows every step (installing, exporting, importing, updating credentials, deploying). While a migration runs, a progress card appears in the sidebar on every page; click it to return to the details.

What it does: copies the site files, creates each database on this server with a **new password**, loads the data, rewrites the database settings in `wp-config.php` and `.env`, and deploys the site (PHP sites run through PHP-FPM). It never overwrites a database that already exists here and never changes the old hosting. DNS is yours to switch: the finish screen has a checklist.

Not moved: mailboxes, Node apps (deploy those from the Deploy page), PostgreSQL dumps inside a cPanel backup (use the live source). Files users upload to a PHP site live inside its release, so a later redeploy replaces them.

To try the wizard without a real account, run `python scripts/make_sample_backup.py` and choose `samples/cpmove-demo.tar.gz` (cPanel) or `samples/plesk-demo.tgz` (Plesk).

## Two-factor sign-in

With two-factor on, a correct password is not enough: sign-in also asks for a 6-digit code from an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, Authy, 1Password and similar). A stolen password alone then gets nobody in, and wrong codes count toward the same lockout as wrong passwords.

**Turn it on.** [Settings, Account and security](#/settings?tab=account), Two-factor sign-in, Set up. In your app choose "Enter a setup key", type the key shown (time based), then type the 6-digit code the app shows and press Verify. You then get **8 recovery codes**. Save them in a password manager: each works once if you lose your phone, and they are never shown again.

**Signing in.** Email and password as usual, then the code. A recovery code can be typed in the same box.

**Turn it off.** Two-factor sign-in on [Settings, Account and security](#/settings?tab=account) needs your password and a code (or a recovery code).

**Lost phone and recovery codes.** A super admin opens Users > **Reset 2FA** for that person; they sign in with the password alone and set it up again. If you are the only super admin, run this on the server:

```
sudo -u coderunner env CODERUNNER_DATA=/var/lib/coderunner python3 /opt/coderunner/app.py --reset-2fa <your-email>
```

Each code can be used once (a code that was just used is refused), and the app accepts the code before and after the current one to allow for small clock differences.

## Alerts, backups, rollback and safety rules

### Alerts

**Alerts ([Integrations, Notifications](#/integrations?tab=alerts), super admin).** Each channel has its own block with its logo. Add a Telegram bot (create it with @BotFather, paste the token, and your chat id) and/or a Slack incoming webhook, a Discord webhook, or any other https webhook, then press **Send a test alert**. The other webhook gets JSON `{event, title, text, site, time}` (plus `content` for Mattermost-style receivers); give it a signing secret and every request carries `X-Shiplane-Signature: sha256=<HMAC-SHA256 of the raw body>` so your receiver can reject anything not from this Shiplane. You get a message when: a site fails two checks in a row (and when it is back), the disk passes 85% or memory 92% (and when it recovers), a backup fails, or a deploy is rolled back. Tokens are stored on the server only and are never shown again. A broken channel is written to Activity as "alert-failed".

### Backups and restore

**Automatic backups ([Databases, Backups](#/databases?tab=backups)).** Switch on **Back up every database each night**, choose the hour (UTC) and how many backups to keep per database. Old ones beyond the limit are deleted (manual backups count too). **Back up everything now** runs it immediately. Backups stay on the server in `/var/backups/coderunner`: download them now and then, because a backup on the same disk does not survive losing the server.

**Restore.** On [Databases, Backups](#/databases?tab=backups) press **Restore**, type the database name to confirm. The app first takes a safety backup of the current data, loads the chosen backup into a fresh database, and only then swaps it in. If the backup cannot be loaded, the current data is untouched. Connected apps lose their connection for a moment.

**Test restore.** A backup you have never restored is a guess. **Test restore** on a backup loads it into a scratch database, counts the tables that came back, and drops the scratch database again; the live data is never touched. Turn on **Test-restore each new backup** in the schedule card to do this for every nightly backup; a backup that cannot be restored raises an alert.

**Automatic rollback on a bad deploy.** After a .NET or Node deploy the server waits up to about 25 seconds for the service to run and answer below HTTP 500. If it does not, the previous release is put back, the deploy shows "ROLLED BACK", and you get an alert. A first deploy has nothing to go back to, so it just reports the failure. Static sites cannot fail this way. Database and environment changes made by the deploy are not undone.

**Require two-factor for everyone ([Settings, Account and security](#/settings?tab=account), super admin).** Turn on your own two-factor first. Then anyone without it is taken to a setup screen at their next sign-in and cannot use the app until they finish. API keys are not affected. The server setting `CODERUNNER_REQUIRE_2FA=1` forces it on.

## Server care: security, early warnings and self-repair

[Server care](#/care) checks the server in about a second and shows a score out of 100 as a ring, with counts of what to fix, what could be better and what is good, and a chip per topic (access, network, updates, resources, system). Problems come first, the biggest gain first, each with its topic icon and the points it costs; passing checks are folded into one row. The checks: SSH password login, root login, firewall, fail2ban, automatic security updates, clock, swap, disk space, pending restart and services exposed to the internet. Every problem says why it matters and, when it is safe, has a **Fix** button that tells you exactly what it will change. Nothing runs in the background for this page: it checks when you open it or press **Check again**.

Fixes are built to never lock you out. Turning off SSH passwords is refused when Shiplane itself logs in with a password or when no account has an SSH key; the firewall always allows your SSH port first; a swap file is refused if it would leave less than 5 GB free. Fixes need a super admin, the word "apply" for the risky ones, and are written to the Activity log. They support Ubuntu and Debian.

**Warnings** ([Settings, Monitoring](#/settings?tab=monitoring)) are sent to your alert channels once per problem, with an all-clear when it is over: the disk will be full within a week at the current rate, a site certificate expires within 14 days or is broken, a site keeps crashing, the server is overloaded for several minutes, Linux killed a program because memory ran out, a restart is needed to finish security updates. Each can be switched off.

**Automatic repair** restarts a site whose service died, restarts one that is running but no longer answers (after two failed uptime checks), and restarts core services (web server, tunnel, database, fail2ban) that were running and stopped. It waits 1, 2, 4, 8 and 16 minutes between tries, gives up after five tries in an hour and tells you, and never touches a site you stopped yourself (until you start it again). Every action is in the Activity log.

**Details behind every card.** Click a card on Server care to open it. fail2ban shows who is banned (with **Unban**, and **Ban an address**; you cannot ban your own address or the server's). Disk shows the 10 biggest folders (also on [Maintenance, Disk](#/maintenance?tab=disk)): click one to look inside it (the scan runs at the lowest priority and stops after 30 seconds, so it never slows your sites). Memory shows the biggest programs and your sites by memory, each with a **Restart** button. SSH shows failed logins in the last 24 hours with the top offenders. Updates lists what is pending with **Install updates now**. The cards on the Server and Sites pages (Memory, Disk, Swap, Uptime, Needs attention) are links to the same details.

All of this lives inside the existing uptime round: no extra background process, a state file under 20 KB, and at most one small command per hour.

## Metrics

[Metrics](#/metrics) draws CPU, memory, disk, load and network. **Live** samples every few seconds while the page is open (how often depends on the performance profile) and records nothing; **1 hour / 6 hours / 24 hours** show what the uptime check remembered (one point per check, kept for 24 hours in a file under 30 KB). Move the pointer over any chart, here and on the Server and Site pages, to read the exact value and time; on a phone, touch and drag.

**Missing something?** Wherever a page needs a program that is not on the server (Node.js for Node sites and npm builds, PHP, .NET, Docker for Docker sites, NSD for your own DNS), it says so and shows an **Install** button that runs the same installer as the Apps page, with live output. Docker and NSD are on the Apps page too.

**Who gets a site's alerts:** every site remembers who deployed it (the e-mail they signed in with). When e-mail is set up, that person is e-mailed when their site goes down, comes back up, or is rolled back, and you (the address under *Send alerts to*, or your own) are copied on every one. A site deployed by a plain user name, or by nobody known, e-mails you alone. Telegram, Slack, Discord and the webhook still get everything.

**E-mail (Gmail):** on [Integrations, Notifications](#/integrations?tab=alerts) you can add your Google address and an *app password* (Google account, Security, turn on 2-step verification, then App passwords; it is 16 letters). Alerts are then also e-mailed (to you, or to the address you choose), and when you add a user you can tick **E-mail them the code** so they receive their activation code. The app password is stored on this computer only, never shown again, and removing the address removes the password. Use **Send a test alert** to check it.

**What a site is built with:** next to the type, Sites shows the framework when it can tell (Angular, React, Vue, Next.js, Nuxt, Svelte, Astro, Express, NestJS, Laravel, WordPress, Symfony, Django, Flask, FastAPI and more) with its logo; a framework without a logo of its own shows its language's logo. It is read from a few small files of the live release (package.json, composer.json, wp-config.php, the built index.html), so it costs nothing to keep current.

**Empty site:** on [Deploy](#/deploy) choose the source **Empty site** to create the address (domain, certificate, service) with a small starter page when you have no zip, folder or repository yet. It works for Static, PHP, Node and Commands sites; later, **Redeploy** or the Git tab replaces the starter with your real code.

## MySQL, MariaDB and SQL Server databases

[Databases](#/databases) has a tab per engine; each has its own **New database**. **PostgreSQL** works as before. **MySQL / MariaDB** creates a database and a user of the same name that can only use it (MariaDB must be installed and running: the page offers an **Install** button, and the Server page starts it). **SQL Server** creates a database and a login of the same name inside your SQL Server container; it needs the container's *sa* password every time, which is sent once and never stored. The new password and a ready connection string are shown once. MySQL and SQL Server databases are listed and dropped on the same page (SQL Server's list lives in SSMS or Azure Data Studio). One-click backups are for PostgreSQL only for now.

## Run with Docker

Shiplane also runs as a container, for example on the VPS it manages or on any computer that has Docker. Download `docker-compose.yml` from the repository (or use the image name you were given) and run:

```
docker compose up -d
docker compose logs shiplane
```

The log shows two things: the **sign-in link** (open it on that computer: `http://127.0.0.1:8765/?t=...`) and, the first time, the **public SSH key** of the container. Add that key to the `~/.ssh/authorized_keys` of the user Shiplane should log in as on the server, then open [Settings, Server connection](#/settings?tab=server) and enter the address and user.

- **The VPS Docker runs on:** use its own address, or `host.docker.internal` (the compose file maps it). The container controls the machine over SSH like any other server, so it can manage everything outside Docker: sites, databases, services, DNS, the firewall.
- **Another VPS:** just enter that server's address. Shiplane in Docker can manage many servers over time by changing the connection.
- **What the container keeps:** one volume, `/data` (settings, the SSH key, saved connections, activity). The image itself is read-only, runs as an unprivileged user, drops every Linux capability and is limited to 256 MB.
- **Reaching it from other computers:** the compose file publishes the port on `127.0.0.1` only. Put a reverse proxy or a tunnel in front, set `CODERUNNER_AUTH: login` and `CODERUNNER_ALLOW_HOSTS`, rather than publishing the port openly.
- **"This app runs on the server" mode** is for the plain (non-Docker) install: a container cannot run commands on its host, so with Docker always use SSH.
- **Publishing your own image:** push a tag such as `v1.0.0` to your GitHub repository; the included workflow (`.github/workflows/docker.yml`) builds amd64 and arm64 images and pushes them to Docker Hub once you add the `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets and the `DOCKERHUB_IMAGE` variable.

## DNS providers and certificates

### Cloudflare

[Integrations, DNS providers](#/integrations?tab=dns) shows whether Cloudflare is connected. To connect, create an API token in your own Cloudflare account (My Profile, API Tokens, Custom token) with *Account / Cloudflare Tunnel / Edit*, *Zone / Zone / Read* and *Zone / DNS / Edit* (add *Account / Account Settings / Read* to let the app list your accounts), paste it and press **Check the token**: you pick the account and the tunnel and press **Connect**. Cloudflare has no sign-in flow for apps like this, so a token is the right way. It is stored only in `cloudflare.json` on the computer running the app (owner-only), never shown again, never sent anywhere but Cloudflare. **Disconnect** deletes it; mappings already made in Cloudflare stay. A token set by the `CLOUDFLARE_API_TOKEN` variable (an optional override, for example in Docker) cannot be changed from the screen; the server installer never sets it, so every install connects its own account here. Only super admins can connect or disconnect.

### Wildcard certificates

**Wildcard certificates** ([Domains, Wildcard certificates](#/domains?tab=certs), super admin): pick a domain held by a connected DNS provider and press **Get certificate**. Certbot on the server asks Let's Encrypt for `*.domain` and `domain`, proving ownership with a DNS record made through the provider's API (about a minute), then renews it by itself; each renewal is copied for Caddy and Caddy is reloaded. Every site with Direct HTTPS one level under the domain uses it (new deploys and the Direct HTTPS switch attach it too). Sites behind the Cloudflare tunnel do not need it. The provider credentials are stored root-only in `/etc/shiplane/dns` for renewals. **Remove** gives those sites their own certificates again.

### Route 53 and DigitalOcean

**Route 53 and DigitalOcean** ([Integrations, DNS providers](#/integrations?tab=dns), one card each; the server's public IP is filled in for you) map hostnames without a tunnel: an A record points the hostname at the server's public IP and the site is served directly with its own certificate, so ports 80 and 443 must be open (DNS page, Direct HTTPS). Route 53 takes an IAM access key allowed `route53:ListHostedZones`, `route53:ListResourceRecordSets` and `route53:ChangeResourceRecordSets`; DigitalOcean takes an API token with domain read/write. The credentials are checked, stored only in `dnsproviders.json` (owner-only) and never shown again. On the Deploy page, **Add an A record** creates the record when it is missing and never overwrites one pointing elsewhere; deleting a site can remove records that point at this server.

## Your own nameservers (DNS)

[DNS hosting](#/dns) (super admins) runs authoritative DNS for your domains on the server with NSD, a very small DNS server (installed the first time you press Apply). **Setup:** choose your nameserver names (for example ns1.example.com and ns2.example.com), see the server's state, and press **Apply to the server**. **Zones:** add a domain (it starts with your nameservers and the bare name and www pointing at the server) and edit A, AAAA, CNAME, MX, TXT and NS records. Changes are saved at once and go live on the next Apply; every zone is checked with `nsd-checkzone` first, so a typo cannot replace working DNS. At your registrar you then create glue records for the nameservers and switch the domain to them.

Things to know: one server is a single point of failure (use a second nameserver when it matters); a domain that moves here leaves Cloudflare (no tunnel, proxy or free certificate); port 53 stays closed until you press **Open port 53** and type `open`.

**Direct HTTPS** ([DNS hosting, Direct HTTPS](#/dns?tab=direct)): a site can also answer on https:// by itself (Caddy gets its own certificate) instead of only through the tunnel. Open ports 80 and 443 (confirmed the same way), then press **Turn on** for a site and type its name. The site that runs this app can never be switched. Redeploys keep the setting.

## Deploy from Git

Open a site from [Sites](#/sites), tab **Git**. Before connecting you pick the repository; once connected the tab shows a status card (repository, branch, last deploy, **Deploy now**) and folded sections for the webhook, build settings, deploy key and disconnect. Enter the repository address, the branch, and optionally a build (**npm**: `npm ci` then `npm run build`) and the folder to publish (for example `dist`). **Deploy now** fetches the newest commit, builds it and switches the site to it with the usual health check and automatic rollback; the commit is shown on the tab. Works for static, Node and PHP sites that already exist.

**Deploy on every push:** after connecting you are shown a *Payload URL* and a *secret* (once; you can make a new one). In GitHub: Settings, Webhooks, Add webhook, paste the URL and secret, content type `application/json`, "Just the push event". In GitLab: Settings, Webhooks, paste the URL and put the secret in "Secret token". Only pushes to the chosen branch deploy; quick successive pushes become one deploy. Calls without the right signature are refused and rate limited.

**Private repositories:** use the SSH address (`git@github.com:you/project.git`), press **Create a deploy key**, and add the public key to the repository as a read-only deploy key.

**New site straight from a repository:** on [Deploy](#/deploy) choose the source **From Git**. Either sign in with GitHub or GitLab (device login: you type a short code on their website; needs the OAuth client id from **[Integrations, Git providers](#/integrations?tab=git), GitHub / GitLab sign-in**, a free OAuth app on your own account) or paste a read-only access token, or just type the address of a public repository. Pick the repository (private ones included) and branch: the page looks at the top-level files and suggests how to deploy it (a Dockerfile becomes a Docker site, a `package.json` with a build script becomes a static site, one with a start script becomes a Commands site, `composer.json` PHP). Everything stays editable. One click then creates the site, adds a read-only deploy key and, when this portal has a public https address, the push webhook; the access token is forgotten right after (it only lives in memory, at most 30 minutes). Only super admins can use this.

**Site types for Git:** static, Node, PHP, **Commands** (you write one build line and one start line; the start command must listen on `$PORT`; builds run as the site's own unprivileged user) and **Docker** (the Dockerfile is built on the server and run as a container bound to 127.0.0.1; the previous image comes back automatically if the health check fails; the memory it uses shows like any service). Stop, start, restart and logs work for all of them.

Security: cloning and building run on the server as an unprivileged user (`shiplane-build`), never as root, but a build still runs the repository's own scripts, so connect only repositories you trust. The server needs `git` (installed automatically) and, for npm builds, Node.js (Apps page).

## Services and containers

On the **Server** page every service and container shows the memory it uses now. **Stop** frees that memory at once; **Start** brings it back; the **Starts at boot / Off at boot** button decides whether it starts after a restart of the server. Containers can be stopped and started too (a stopped container keeps its data) and have their own **Starts automatically** switch. Services you stop on purpose are left alone by automatic repair. Stopping Caddy or Cloudflare is blocked when this app runs on the server itself (you would cut yourself off), and the firewall service cannot be stopped here (use Server care).

## Performance: light by default, full power when you want it

The app is built to stay small on the server. [Settings, Performance](#/settings?tab=performance) has three profiles:

| Profile | For | What changes |
|---|---|---|
| **Light** (default) | servers with 1-2 GB | 8 requests at once, the server's status is read once and shared for 20 seconds, screens refresh every 30 seconds, short job logs, memory is handed back to the system every minute, and the app restarts itself (only when idle) if it ever passes 256 MB |
| **Balanced** | 4 GB and up | 16 requests at once, status shared for 10 seconds, refresh every 15 seconds |
| **Full power** | when speed matters more than memory | 64 requests at once, status shared for 2 seconds, refresh every 5 seconds, long logs, no memory watchdog |

The same page shows what the app is using right now and has a **Free memory now** button. How it stays light: a hidden browser tab asks for nothing; the status read is shared between everyone looking at it and dropped the moment anything changes; the status script asks the server a handful of questions in total instead of several per site; the uptime check results are reused instead of probing the same address twice; old job and migration logs are trimmed and forgotten; page files are revalidated instead of downloaded again; extra requests get a clear "busy" answer instead of piling up threads. On the server the service also runs at a lower CPU and disk priority (`Nice`, `CPUWeight`, `IOWeight`) so your sites always come first. Memory is deliberately not capped by the kernel, because installs and deploys run under the same service and must never be killed halfway.

## Live monitoring, logs and billing

**Uptime checks** send one small request (headers only, about 1 KB) to each site's public address and report a site down after two failed checks in a row. Choose how often, or switch them off, in [Settings, Monitoring](#/settings?tab=monitoring) (default every 5 minutes). More sites or shorter gaps mean more requests, but the bandwidth is negligible; the sites' own logs will show the checks as visits from `coderunner-health`.

**Server page.** Four live charts (CPU, memory, network in and out) update every 5 seconds and keep the last 5 minutes. They pause while the browser tab is hidden. Each service and container has a **Logs** button that opens a live stream.

**Live logs.** Site > Logs, and the Logs buttons on the Server page, follow the log as it is written (journalctl or docker logs). Use **Pause** to read without the page moving, the filter box to keep only matching lines, and **Download** to save what is on screen (the last 2000 lines). The stream reconnects by itself. Admins and super admins only.

**Uptime.** While Shiplane runs, it opens every site's public address every 2 minutes. A site is marked **Down** after two failed checks in a row (no answer, or an HTTP 5xx), and **Up** again on the first good one; both are written to Activity. The Sites list shows a 24-hour graph and percentage, the site page shows the detail with red bars for failed checks. The history is kept in the data folder, so it survives restarts. There is no email or phone alert yet.

**Billing card (Sites page, admins).** Hetzner: add a **read-only** API token (Hetzner Cloud console > your project > Security > API tokens, permission Read) on [Integrations, Cloud providers](#/integrations?tab=cloud). It is checked against Hetzner, stored on the server only and never shown again. It shows each server's monthly price, an estimate for the month so far (hourly billing, capped at the monthly price) and traffic used. Cloudflare: uses the existing token; it needs the account permission **Billing: Read** to list paid plans, otherwise the card says so. Tunnel and DNS are free. Neither provider offers invoices through its API, so these are estimates, not what you will be charged to the cent.

**Loading feedback.** A thin bar runs across the top while a request takes longer than a blink; buttons show a spinner while they work; deploys show a step list (pack, unpack, database, switch, Cloudflare, done) above the raw log.

## Mobile app and API access

Everything the interface does goes through the API, so a mobile app can use the same endpoints.

1. Run the app on the server (see above) so it has a stable address.
2. In [Settings, API keys](#/settings?tab=api), create a key (for example "phone"). It is shown once; only a hash is stored.
3. The client sends the header `Authorization: Bearer <key>`. No cookie and no extra header is needed.

```
curl https://<shiplane-address>/api/status -H "Authorization: Bearer crk_xxxxx"
curl -X POST https://<shiplane-address>/api/action \
  -H "Authorization: Bearer crk_xxxxx" -H "Content-Type: application/json" \
  -d '{"name":"my-api","action":"restart"}'
```

An API key has the **admin** role. Errors come back as JSON `{"error": "..."}` with a normal HTTP status (401 not signed in, 403 role too low, 429 too many attempts). `GET /api/routes` lists every endpoint and the role it needs. API keys cannot manage users or other keys.

### Endpoint reference

| Method and path | Purpose |
|---|---|
| `GET /api/session` | Mode, version, whether Cloudflare is set up |
| `GET /api/status` | Server health and every site |
| `GET /api/job?id=&since=` | Progress of a background job (deploy, updates) |
| `GET /api/logs?name=&lines=&q=&level=&since=` | Service log of a site; filter by text, `level` (warning, error) and `since` (15m, 1h, 6h, 24h, 7d) |
| `GET /api/runtimes` | Node versions a site can use |
| `GET /api/cloudflare/zones` | Domains of the connected Cloudflare account |
| `GET /api/site/config?name=` | Generated Caddy, systemd and metadata files |
| `POST /api/site/run` | Run a one-off command in a site (admin): `{name, command}` |
| `POST /api/site/env` | Set or remove environment variables: `{name, set:{}, unset:[]}` |
| `POST /api/deploy` | Start a deploy; returns `{job}` |
| `POST /api/upload` | Raw .zip body; returns the unpacked `{path}` to deploy |
| `POST /api/action` | `restart`, `start`, `stop`, `activate` (`release`), `delete` (`confirm`) |
| `GET /api/databases` | Databases and backups |
| `POST /api/databases/create`, `/backup`, `/drop`, `/backup-delete` | Database operations |
| `GET /api/databases/download?file=` | Download a backup |
| `GET /api/server/info` | Updates, firewall, fail2ban, processes |
| `POST /api/server/service`, `/container`, `/reboot`, `/upgrade` | Server operations |
| `GET /api/activity` | The activity log |
| `GET /api/mapping`, `/api/cloudflare/verify` | Cloudflare state and health |
| `POST /api/mapping/check`, `/map`, `/unmap` | Cloudflare mapping |
| `GET /api/keys`, `POST /api/keys/create`, `/revoke` | API keys (super admin, browser session only) |
| `POST /api/auth/login`, `/activate`, `/logout`, `/change-password`, `GET /api/auth/me` | Sign-in (server mode) |
| `GET /api/users`, `POST /api/users/create`, `/update`, `/reset-code`, `/reset-2fa`, `/delete` | Accounts (super admin) |
| `GET/POST /api/alerts`, `POST /api/alerts/test`, `GET/POST /api/backups/schedule`, `POST /api/backups/run`, `POST /api/databases/restore`, `POST /api/policy` | Alerts, backup schedule and restore, security policy |
| `POST /api/auth/login-2fa`, `/api/auth/2fa/start`, `/2fa/enable`, `/2fa/disable` | Two-factor sign-in (account sessions only, not API keys) |
| `GET /api/routes` | This list, generated |

## Deploy from the command line or CI

`scripts/shiplane-deploy.py` zips a folder, uploads it, deploys it and prints the log. It exits with an error when the deploy failed or was rolled back, so a CI job turns red. Create an API key on the Users page (role admin).

```
export SHIPLANE_URL=https://<shiplane-address>
export SHIPLANE_KEY=crk_...
python shiplane-deploy.py ./dist --site my-api            # redeploy with the site's saved settings
python shiplane-deploy.py ./dist --site new --type static --hostname new.<your-domain>   # first deploy
```

GitHub Actions (store the key as the secret `SHIPLANE_KEY` and the address as the variable `SHIPLANE_URL`):

```
name: deploy
on: { push: { branches: [main] } }
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci && npm run build
      - run: python scripts/shiplane-deploy.py dist --site my-site
        env:
          SHIPLANE_URL: ${{ vars.SHIPLANE_URL }}
          SHIPLANE_KEY: ${{ secrets.SHIPLANE_KEY }}
```

.NET publish (in place of Web Deploy, which needs IIS): import `scripts/Shiplane.targets` in the `.csproj`, set `SHIPLANE_URL` / `SHIPLANE_KEY`, then

```
dotnet publish -c Release -p:ShiplaneSite=my-api                                                   # redeploy
dotnet publish -c Release -p:ShiplaneSite=my-api -p:ShiplaneHost=api.<your-domain> -p:ShiplanePort=5000   # first deploy
```

In Visual Studio, use a Folder publish profile and add `<ShiplaneSite>my-api</ShiplaneSite>` to its `.pubxml`.

The server must be reachable from the CI runner. Sites connected to Git on the Git tab can also deploy by themselves on every push.

## Health checks, Node versions, log search and off-server backups

- **Health check:** each app site has an optional path (for example `/health/ready`) and an expected status. A release that fails is rolled back; on a site's first deploy there is nothing to roll back to, so the service is stopped instead of crash-looping.
- **Node version:** the Deploy page lists the Node versions installed under `/usr` or `/opt` on the server. Pick one per site; the default is the system Node.
- **Run a command:** the Manage tab of a site can run a one-off command (migrations, creating the first admin) as the site's own user with its environment variables.
- **Logs:** the site's Logs tab can search the log by text, level and time range.
- **Environment variables:** **Paste .env** on a site's Environment tab adds many variables at once.
- **Off-server backups:** on [Databases, Backups](#/databases?tab=backups), give an S3-compatible bucket (Cloudflare R2, Amazon S3, ...). Each backup is uploaded there too. The secret key stays on this computer. Expire old copies with the bucket's own lifecycle rule.

## Sharing it with a teammate

Everyone needs their **own** server user and SSH key, so that actions can be traced and access can be removed per person.

1. They install Python, download or clone this project, and run `start.bat` (Windows) or `./start.sh`.
2. They enter their own user and key (or password) in [Settings, Server connection](#/settings?tab=server).
3. Without a connected Cloudflare account, the Cloudflare tab of [Domains](#/domains) says so and Deploy does not offer Cloudflare mapping.

## Security model

* **Who can get in.** On your computer: the app listens on `127.0.0.1` and needs the launch token. On the server: it still listens only on `127.0.0.1` (Cloudflare Tunnel brings traffic in; no port is open) and needs a signed-in account or an API key.
* **Passwords and sessions.** Salted scrypt hashes; sessions are random tokens stored hashed; cookies are `HttpOnly`, `SameSite=Lax` and `Secure`; sign-in is rate-limited and gives the same message for a wrong email and a wrong password.
* **Browser protections.** A strict Content-Security-Policy (no inline scripts), no framing, `nosniff`, HSTS, and a custom header required on every browser write (against cross-site requests). Unknown `Host` headers are refused.
* **Roles are enforced on the server** for every endpoint, not just hidden in the interface.
* **Remote actions** run fixed scripts with validated arguments; names are checked against strict patterns and quoted before they reach a shell.
* **Secrets** (database passwords, environment values, the Cloudflare token, API keys, activation codes) are never shown back and never written to the activity log.
* **The service user has root-level sudo** because managing a server requires it. That is why sign-in matters: guard accounts like root access.
* The Cloudflare token can edit DNS for the zones it was given. Keep `cloudflare.json` and `/etc/coderunner.env` private.

## Troubleshooting

| What you see | What to do |
|---|---|
| Blank page or "Open the link printed in the terminal" | On your computer, use the link from the terminal window. If you closed it, run the start script again. |
| "Too many attempts" at sign-in | Wait about 10 minutes, or ask a super admin. |
| Lost the activation code | A super admin uses Users, Reset password. For the first admin or when locked out: the `--reset-code` command above. |
| Zip upload fails on the public address | Cloudflare limits uploads to 100 MB. Use `connect-server` (private tunnel) for bigger builds. |
| "SSH key not found" | [Settings, Server connection](#/settings?tab=server): set the full path of your private key. |
| "Permission denied (publickey)" | Your key is not added to your server user. Ask the owner to add the public key. |
| Status shows "Not reachable" | Check your internet and that the server is up; press **Test connection** in [Settings, Server connection](#/settings?tab=server). |
| Site is deployed but the public check is red | Open the site's Logs. For apps, check the port and the entry file. Static sites: check the folder has an index.html. |
| Public check says HTTP 404 | The hostname has no route to the tunnel. Map it in Cloudflare. |
| HTTPS error on a two-level name | Needs Cloudflare's certificate add-on, or use a one-level name. |
| Old site still shows after a deploy | Clear your browser cache or try a private window; DNS and browsers cache. |
| Domains says Cloudflare is not connected | Connect it on [Integrations, DNS providers](#/integrations?tab=dns) (super admin). |
| Mapping says "CONFLICT" | That name already has a DNS record elsewhere. Delete or change it in Cloudflare, then map again. |
| Cannot connect SSMS to SQL Server | Run `ssh -L 14330:127.0.0.1:1433 user@server` and connect to `localhost,14330` with the `sa` login and "Trust server certificate". |
| Restore a Postgres backup | `gunzip -c file.sql.gz \| sudo -u postgres psql dbname` on the server. |

## Files and folders

| Path | What it is |
|---|---|
| `app.py` | Starts everything. |
| `coderunner/` | The backend: configuration, SSH runner, deploy logic, Cloudflare, databases, server operations, the API. |
| `coderunner/remote/` | The fixed scripts that run on the server. |
| `web/` | The user interface: `index.html`, compiled `app.css`, `js/` (router, components, pages) and this manual. |
| `frontend/` | The design system source (Tailwind 3). Only needed to change the look: `npm install` then `npm run build`. |
| `server.json` | Your connection settings (created by Settings). |
| `cloudflare.json` | Account and tunnel IDs, and the API token if you have one. |
| `data/` | The activity log, API key hashes and the accounts database (`users.db`). On the server this is `/var/lib/coderunner`. |
| `scripts/install_on_server.py` | Installs or upgrades the app on the server. |
| `connect-server.bat` / `.sh` | Private SSH tunnel to the app on the server. |
| `tests/` | Automated checks (`run_unit_tests.py` runs all the no-server unit tests with a coverage gate; plus `test_app.py`, `test_local_mode.py`, `check_js.mjs`). |

## FAQ

**Do I need Node.js?** Only to change the design. Running the app needs just Python.

**Is it safe to close the terminal window?** Yes, but the app stops. Sites keep running on the server; only the management tool stops.

**Can two people use it at once?** Yes, each on their own computer with their own key. Activity shows who did what.

**Where is my data?** Sites and databases are on the server. This app keeps only its settings and the activity log.


## Firewall, email checks, status page and scoped API keys

Screens: **Firewall & email** in the sidebar (Network group) has tabs for the firewall, the email check and the status page. The same things are available through the API (`GET /api/routes`).

**Firewall (ufw).** `GET /api/firewall` shows the rules. `POST /api/firewall/add` and `/delete` take `{action: allow|deny|limit, port: "443" or "6000:6100", proto: tcp|udp|any, from: "any" or an IP/network}`; add `"preview": true` to see the exact commands without running them. Lock-out guard: SSH's port(s) (the saved port plus whatever sshd listens on) cannot be denied, and the last rule that lets SSH in cannot be removed. `POST /api/firewall/power` (super admin) turns the firewall on (SSH is allowed first) or off; type `"confirm": "firewall"`.

**Email check.** `GET /api/email/check?domain=example.com[&selector=mysel]` reads MX, SPF, DKIM (common selectors plus yours) and DMARC over DNS-over-HTTPS and returns findings with a fix for each. It changes nothing.

**Status page (super admin).** `POST /api/statuspage {enabled, title, sites, rotate}` turns on a read-only page at `/status?t=<token>`. It shows only the site names you choose, up/down and uptime: no addresses or server details. Share the link; `rotate: true` makes a new link and kills the old one. The app is also installable as a PWA (the service worker caches nothing).

**Scoped API keys.** When creating a key choose a scope: `admin`, `viewer` (read-only) or `deploy` (only `/api/deploy`, `/api/upload`, `/api/job`, `/api/status`; made for CI), and an expiry in days (0 = never).

**MySQL / MariaDB backups.** Back up, nightly schedule, test restore and restore now cover MySQL / MariaDB too (files end in `.my.sql.gz`). A MySQL restore is proven on a scratch database first, then the real database is emptied and loaded; the safety backup taken just before is the way back if that second load fails.


## Several servers, single sign-on, people limited to some sites, staging and undo

**All servers** (sidebar, super admin). Add several servers (SSH only). The list shows each server's health side by side (sites, memory, disk, failed services). One server is *active*: the one every other page manages and the one uptime checks, backups and auto-heal follow. **Manage** switches to another (the uptime history and live metrics start afresh, because they describe one server). **Prepare** (adopt) installs the tools Shiplane needs plus Caddy on a fresh Ubuntu/Debian server and creates its deploy folders; it does not change SSH, the firewall or existing sites. Run Server care afterwards. Connections are stored in `fleet.json` (owner-only, passwords never shown again).

**Single sign-on** (Settings, Account and security, sign-in mode only, super admin). Works with any OpenID Connect provider (Google, Microsoft Entra, Okta, Keycloak, Authentik...). Register `https://your-address/api/auth/sso/callback` as the redirect address with the provider, then enter its issuer address, client ID and client secret. Only people who already have an account here can sign in; SSO never creates accounts. Put the public address in **Public address of this app** when Shiplane runs behind a proxy. Sign-in uses PKCE, a one-time state, and checks the provider's answer (issuer, audience, nonce, expiry, verified email). Two-factor is left to the provider.

**Limit a person to some sites** (Users, Sites column). A limited person sees and manages only those sites (deploy, logs, environment, Git, restart) and nothing server-wide: no databases, firewall, settings, server care, other people's sites. Their role still applies (a limited viewer stays read-only). Super admins cannot be limited.

**Staging and production.** Deploy a second site of the same type (for example `shop-staging`) and test there. On its **Manage** tab, **Promote to production** makes another site of the same type serve the same release. The production site keeps its address, environment variables and database; the same health check as a normal deploy runs and it goes back by itself if the check fails. Docker sites cannot be promoted. Releases are copied as they are, so apps that bake absolute paths in (for example a Python virtualenv) should be rebuilt instead.

**Preview and undo for care fixes.** Before a fix runs, the confirmation shows the exact commands. Fixes that Shiplane applies are recorded on the server, and **Applied by Shiplane** (Server care page) lists them with **Undo** (type `undo`). Undo removes only what the fix added (SSH setting files, the fail2ban jail, the swap file, the update settings, the firewall switch, time sync) and never touches anything Shiplane did not apply.
