# Deploy Huz Backend to a DigitalOcean Droplet

This guide is written for a beginner and matches this repository.

Current plan for this project:

- backend goes live on `hajjumrah.org` now
- localhost frontends are temporarily allowed to call the production backend
- later, frontend can also live on `hajjumrah.org`
- when that happens, the frontend should take `/` and Django should keep backend routes like `/api/v1/`, `/admin/`, `/partner/`, `/bookings/`, `/management/`, and `/common/`

Important limitation:

- frontend and backend can share the same host name, but they cannot both own `/` at the same time
- for now, while only backend is deployed, it is fine for Django to answer the whole domain
- later, we will adjust Nginx so the frontend serves `/` and Django serves only backend paths

## What you are building

You will create one Ubuntu server on DigitalOcean and run:

- `Nginx` as the public web server
- `Gunicorn` as the Django application server
- `Django` from this repository
- your existing MySQL database using the values in your production env file

This is a manual deployment flow. You create the server yourself, connect to it over SSH, and run deploy commands when you want to update production.

## Before you start

You need these things ready:

- a DigitalOcean account
- the domain `hajjumrah.org`, which already exists in DigitalOcean DNS
- access to this repository
- the production database credentials
- the production email credentials
- the Firebase JSON file if the backend uses Firebase in production

## Step 0: Make these deployment files available to the server

I prepared the deployment files in your local copy of this repository. The server must be able to read that updated code.

### Recommended option: push this repo first

Run these commands on your own computer:

```bash
cd /Users/macbook/Desktop/Huz/Huz-Backend
git status
git add .gitignore huz/settings.py requirements.txt deploy docs
git commit -m "Prepare DigitalOcean deployment"
git push origin main
```

If you prefer another branch, push to that branch instead.

### Fallback option: copy the local folder directly to the server

If you do not want to push yet, copy the current folder after the droplet is created:

```bash
scp -r /Users/macbook/Desktop/Huz/Huz-Backend root@YOUR_DROPLET_IP:/root/huz-backend-src
```

## Recommended droplet size

Start with one of these:

- `Basic 2 GB / 1 vCPU` for light traffic
- `Basic 4 GB / 2 vCPU` for safer headroom

This app uses Django, MySQL, file uploads, and background server processes, so `2 GB` is the minimum I would recommend for production.

## Step 1: Create the droplet in DigitalOcean

In the DigitalOcean control panel:

1. Click `Create`.
2. Click `Droplets`.
3. Choose `Ubuntu 24.04 LTS`.
4. Choose a region close to your users.
5. Choose `Basic`.
6. Choose either `2 GB / 1 vCPU` or `4 GB / 2 vCPU`.
7. Turn `Monitoring` on.
8. Add your `SSH key`.
9. Name the server something simple like `huz-backend-prod`.
10. Click `Create Droplet`.

Why this matters:

- Ubuntu 24.04 is stable and easy to manage.
- SSH keys are safer than passwords.
- Monitoring helps you see CPU, RAM, and disk usage later.

## Step 2: Point your domain to the droplet

After the droplet is created, DigitalOcean shows you its public IP address.

In your DNS provider:

1. Create an `A` record.
2. Host should be `@` for `hajjumrah.org`.
3. Value should be the droplet IP address.
4. Save the record.

If you also want `www.hajjumrah.org`, keep a second record for `www` pointing to the same droplet IP.

From your screenshot, `hajjumrah.org` and `www.hajjumrah.org` already point to `165.232.160.29`.
If that is the exact droplet you will use, you do not need to change DNS.
If you create a new droplet and get a different IP, update both records to the new IP.

Then wait a few minutes for DNS to start working.

## Step 3: Connect to the server

From your own computer:

```bash
ssh root@YOUR_DROPLET_IP
```

If DigitalOcean asks you to confirm the fingerprint, type `yes`.

Why this matters:

- `root` is the first admin user on a fresh droplet.
- You only use `root` for initial setup. The deploy scripts create a normal app user for the Django app.

## Step 4: Install git and get the repository onto the server

Still on the server:

```bash
apt update
apt install -y git
git clone --branch main https://github.com/Hassant147/Huz-backend_New.git /root/huz-backend-src
cd /root/huz-backend-src
```

If you used the fallback `scp` option in Step 0, skip the `git clone` line and just run:

```bash
cd /root/huz-backend-src
```

Why this matters:

- the bootstrap script lives inside this repository
- cloning first lets the server use the exact deploy files prepared for this project

## Step 5: Run the one-time server bootstrap script

Use your current production domain:

```bash
cd /root/huz-backend-src
sudo bash deploy/digitalocean/bootstrap_droplet.sh hajjumrah.org
```

What this script does:

- installs Python, Nginx, Certbot, and MySQL build packages
- creates the `huz` Linux user
- copies the repo into `/srv/huz-backend`
- creates a separate live media directory at `/srv/huz-media`
- creates the Python virtual environment
- installs Python dependencies
- installs the `systemd` service
- installs the Nginx site config
- creates `/etc/huz-backend.env` from the production env template

## Step 6: Fill in the real production environment values

Open the env file on the server:

```bash
sudo nano /etc/huz-backend.env
```

Replace every placeholder value. The important ones are:

- `APP_ENV`
- `API_PUBLIC_ORIGIN`
- `WEB_APP_ORIGIN`
- `OPERATOR_APP_ORIGIN`
- `ADMIN_APP_ORIGIN`
- `SECRET_KEY`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `CORS_ALLOWED_ORIGINS`
- `SERVER_EMAIL_PASSWORD`
- `OPERATOR_PANEL_BASE_URL`
- `MEDIA_ROOT`

Use the reviewed environment checklist at `docs/BACKEND_ENVIRONMENT_CHECKLIST_2026-04-01.md` when you fill these values. That checklist is the canonical source for which origins should appear in the production env file.

For your current setup, use these reviewed known values, then replace the blank admin origin with the real separately deployed admin domain before release:

```env
APP_ENV=production
API_PUBLIC_ORIGIN=https://hajjumrah.org
WEB_APP_ORIGIN=https://hajjumrah.co
OPERATOR_APP_ORIGIN=https://operator.hajjumrah.co
# Required before release: replace the blank value below with the real admin frontend origin.
ADMIN_APP_ORIGIN=
ALLOW_LOCALHOST_ORIGINS=False
ALLOWED_HOSTS=hajjumrah.org,www.hajjumrah.org
# After setting ADMIN_APP_ORIGIN, append the same origin here for admin session requests.
CSRF_TRUSTED_ORIGINS=https://hajjumrah.org,https://www.hajjumrah.org,https://hajjumrah.co,https://www.hajjumrah.co,https://operator.hajjumrah.co
CORS_ALLOW_ALL_ORIGINS=False
# After setting ADMIN_APP_ORIGIN, append the same origin here for admin browser API calls.
CORS_ALLOWED_ORIGINS=https://hajjumrah.co,https://www.hajjumrah.co,https://operator.hajjumrah.co
OPERATOR_PANEL_BASE_URL=https://operator.hajjumrah.co
MEDIA_ROOT=/srv/huz-media
```

The exact admin production domain is not discoverable from current repo config. Do not guess it. Get the real deployed admin origin and use that same value for `ADMIN_APP_ORIGIN`, `CORS_ALLOWED_ORIGINS`, and `CSRF_TRUSTED_ORIGINS` before release.

Why these values are correct:

- `API_PUBLIC_ORIGIN`, `WEB_APP_ORIGIN`, `OPERATOR_APP_ORIGIN`, and `ADMIN_APP_ORIGIN` make the deployed topology reviewable in one place
- `ALLOWED_HOSTS` tells Django which backend hostnames are allowed
- `CSRF_TRUSTED_ORIGINS` allows secure browser requests from the reviewed frontend origins
- `CORS_ALLOWED_ORIGINS` allows only the reviewed browser origins to call the production backend
- `ADMIN_APP_ORIGIN` is required in this deployment model, and production checks fail until its real origin is also included in both browser-origin allowlists

How to create a strong `SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Then paste that value into `SECRET_KEY`.

Why this matters:

- this file contains the private production settings
- it is read by Gunicorn and Django on startup
- it should never be committed to git

## Step 7: Upload the Firebase credentials file if you use Firebase

If production uses Firebase, copy the JSON file from your computer to a path outside the git checkout, for example:

```bash
ssh root@YOUR_DROPLET_IP "mkdir -p /srv/huz-secrets && chown huz:huz /srv/huz-secrets"
scp /absolute/path/to/firebase.json root@YOUR_DROPLET_IP:/srv/huz-secrets/firebase.json
ssh root@YOUR_DROPLET_IP "chown huz:huz /srv/huz-secrets/firebase.json && chmod 600 /srv/huz-secrets/firebase.json"
```

Then set this in `/etc/huz-backend.env`:

```env
FIREBASE_CREDENTIAL_PATH=/srv/huz-secrets/firebase.json
```

Why this matters:

- the credential file should not live in git
- the app will fail if it needs Firebase and the file is missing

## Step 8: Run the release script

This script installs dependencies, runs migrations, collects static files, runs Django deploy checks, and restarts the service.

```bash
sudo bash /srv/huz-backend/deploy/digitalocean/release.sh
```

If the service starts correctly, the last lines should show `active (running)`.

This repository also includes a GitHub Actions workflow at `.github/workflows/deploy-backend.yml` for push-to-production deploys from `main`. That workflow needs one repository secret named `HUZ_GITHUB_DEPLOY_KEY`, which should contain the private SSH key used only for deploys.

On the server side, that deploy key should be attached to the `huz` user with a forced command so the key can only run the deploy script and cannot open a normal shell.

To inspect production deploy activity on the server later:

```bash
sudo journalctl -u huz-backend -n 100 --no-pager
```

## Step 9: Test over plain HTTP first

In your browser, open:

```text
http://hajjumrah.org/admin/
```

If the page loads or you get a normal Django response, the app is reachable.

Useful commands if something fails:

```bash
sudo systemctl status huz-backend --no-pager
sudo journalctl -u huz-backend -n 100 --no-pager
sudo nginx -t
sudo systemctl status nginx --no-pager
```

What these commands mean:

- `systemctl status huz-backend` shows whether Gunicorn started
- `journalctl -u huz-backend` shows Django and Gunicorn logs
- `nginx -t` checks whether the Nginx config is valid

## Step 10: Add HTTPS with Let's Encrypt

Once HTTP is working and DNS points to the droplet:

```bash
sudo certbot --nginx -d hajjumrah.org -d www.hajjumrah.org
```

If you also want `www` or another hostname, add more `-d` flags.

Choose the redirect option when Certbot asks whether it should force HTTPS.

Then test renewal:

```bash
sudo certbot renew --dry-run
```

Why this matters:

- HTTPS protects passwords, sessions, and API traffic
- Certbot updates the Nginx config for you

## Step 11: Create the Django admin user

If you need admin access:

```bash
cd /srv/huz-backend
sudo -u huz .venv/bin/python manage.py createsuperuser
```

## Step 12: How future deployments work

When you update the backend code:

```bash
ssh root@YOUR_DROPLET_IP
cd /srv/huz-backend
git pull
sudo bash deploy/digitalocean/release.sh
```

That is your manual deployment flow.

## Common problems and what they usually mean

`DisallowedHost`

- `ALLOWED_HOSTS` does not contain your real domain

`CSRF verification failed`

- `CSRF_TRUSTED_ORIGINS` is missing your web, operator, or admin `https://...` domain
- your web, operator, or admin frontend domain is missing from `CORS_ALLOWED_ORIGINS`

`mysqlclient` build error during pip install

- the server is missing `default-libmysqlclient-dev` or `pkg-config`
- the bootstrap script installs both

`502 Bad Gateway`

- Nginx is running but Gunicorn failed
- check `sudo journalctl -u huz-backend -n 100 --no-pager`

OTP signup fails with `Failed to send OTP. Please try again later.`

- `SMS_GATEWAY_API_KEY` is missing in `/etc/huz-backend.env`
- reload the app after updating env: `sudo systemctl restart huz-backend`

Static files do not load

- run `sudo bash /srv/huz-backend/deploy/digitalocean/release.sh` again
- make sure `/srv/huz-backend/staticfiles` exists

## Important paths on the server

- app code: `/srv/huz-backend`
- environment file: `/etc/huz-backend.env`
- systemd service: `/etc/systemd/system/huz-backend.service`
- nginx site: `/etc/nginx/sites-available/huz-backend`

## What changed in this repository for deployment

- production-friendly Django settings in `huz/settings.py`
- Gunicorn config in `deploy/digitalocean/gunicorn.conf.py`
- systemd template in `deploy/digitalocean/huz-backend.service.template`
- Nginx template in `deploy/digitalocean/nginx.conf.template`
- env template in `deploy/digitalocean/env.production.example`
- one-time bootstrap script in `deploy/digitalocean/bootstrap_droplet.sh`
- repeatable release script in `deploy/digitalocean/release.sh`
