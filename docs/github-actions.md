# Deploying with GitHub Actions

Two workflows ship with this repository.

| Workflow | What it does |
|---|---|
| `.github/workflows/deploy.yml` | Deploys **Shiplane itself** to your server over SSH after the tests pass (every push to `main`, or by hand). |
| `.github/workflows/deploy-site.yml` | A **reusable workflow**: deploys a site from any repository to your Shiplane server, using a deploy-only API key. |

`ci.yml` runs the unit tests and JavaScript checks on every push and pull request; `docker.yml` publishes the Docker image on a `v*` tag.

## 1. Deploy Shiplane itself (`deploy.yml`)

It runs `scripts/install_on_server.py`, which upgrades the code on the server and keeps its settings, accounts and data (a first run also installs Caddy, cloudflared and the service).

Set up once in the repository, under Settings:

1. **Environments**: create `production`. Add *required reviewers* there if you want someone to approve every deploy.
2. **Secrets and variables, Actions**:

| Kind | Name | Value |
|---|---|---|
| secret | `DEPLOY_HOST` | the server's address |
| secret | `DEPLOY_USER` | SSH user: `root`, or a user with passwordless sudo |
| secret | `DEPLOY_SSH_KEY` | the **private** key of a key pair made only for deploying |
| secret | `DEPLOY_KNOWN_HOSTS` | the server's host key: `ssh-keyscan -p 22 your.server` |
| variable | `DEPLOY_DOMAIN` | where Shiplane is served, e.g. `shiplane.example.com` |
| variable | `ADMIN_EMAIL` | the first super admin |
| variable | `DEPLOY_PORT` | optional, SSH port (default 22) |

Create the key pair with `ssh-keygen -t ed25519 -f deploy_key -N ""`, put `deploy_key.pub` in the server user's `~/.ssh/authorized_keys`, paste `deploy_key` into the secret, then delete the local copy.

What keeps it safe: the host key is pinned (`DEPLOY_KNOWN_HOSTS` is required, the runner never trusts an unknown machine); secrets reach the steps only as environment variables; only one deploy runs at a time and a running one is never cancelled; the key and `server.json` are removed from the runner at the end; the workflow has read-only repository permissions.

After the upgrade it checks that the service answers on the server itself and fails the run if it does not.

## 2. Deploy a site from another repository (`deploy-site.yml`)

1. In Shiplane: **Settings, API keys**, create a key with scope **Deploy only**. It can upload, deploy and read the status of sites, nothing else. Save it as the secret `SHIPLANE_KEY` in the repository you deploy from.
2. Add a workflow to that repository:

```yaml
name: deploy
on:
  push:
    branches: [main]
jobs:
  deploy:
    uses: vtoxi/shiplane/.github/workflows/deploy-site.yml@v1   # pin a tag or a commit, not a moving branch
    with:
      url: https://shiplane.example.com
      site: my-site
      folder: dist
      node-version: '20'
      build-command: npm ci && npm run build
    secrets:
      key: ${{ secrets.SHIPLANE_KEY }}
```

For the **first** deploy of a site also pass `hostname: my-site.example.com` and `type: static` (or `node`, `php`, `dotnet`, `command`); later deploys reuse the site's saved settings. `extra-args` passes more flags to `scripts/shiplane-deploy.py`, for example `--port 3000 --start server.js`.

The job fails if the deploy fails or is rolled back (the health check did not pass and the previous release came back), so a red run always means the old version is what is live.

If the Shiplane repository is private, the reusable workflow cannot be called from other repositories unless you allow it (Settings, Actions, Access); inputs `shiplane-repo` and `shiplane-ref` choose where `shiplane-deploy.py` comes from.

## Staging first

Deploy a second site of the same type (for example `my-site-staging`) from a pull request or a branch, check it, then use **Promote to production** on its Manage tab in Shiplane.

## Troubleshooting

| Message | Meaning |
|---|---|
| `DEPLOY_KNOWN_HOSTS is not set` | Run `ssh-keyscan -p PORT HOST` and save the output as the secret. |
| `Host key verification failed` | The server's key changed (reinstall?). Check it is really your server, then update the secret. |
| `This site does not exist yet: pass --type and --hostname` | First deploy: add `hostname` and `type`. |
| `HTTP 403 ... This API key can only deploy` | The key is deploy-only and the call needs more; deploy-only keys cannot do anything but deploy. |
