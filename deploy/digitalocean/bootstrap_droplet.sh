#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo."
  exit 1
fi

DOMAIN="${1:-}"
SERVER_NAMES="${SERVER_NAMES:-${DOMAIN}}"
APP_USER="${APP_USER:-huz}"
APP_DIR="${APP_DIR:-/srv/huz-backend}"
MEDIA_ROOT="${MEDIA_ROOT:-/srv/huz-media}"
SERVICE_NAME="${SERVICE_NAME:-huz-backend}"

if [[ -z "${DOMAIN}" ]]; then
  echo "Usage: sudo bash deploy/digitalocean/bootstrap_droplet.sh your-api-domain.com"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "Installing server packages..."
apt update
apt install -y \
  python3-pip \
  python3-venv \
  git \
  nginx \
  certbot \
  python3-certbot-nginx \
  build-essential \
  pkg-config \
  rsync \
  default-libmysqlclient-dev

echo "Creating application user and folders..."
id -u "${APP_USER}" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "${APP_USER}"
mkdir -p "${APP_DIR}"
mkdir -p "${MEDIA_ROOT}"
chown -R "${APP_USER}":"${APP_USER}" "${APP_DIR}"
chown -R "${APP_USER}":"${APP_USER}" "${MEDIA_ROOT}"

echo "Copying repository into ${APP_DIR}..."
rsync -a --delete \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'staticfiles' \
  "${REPO_DIR}/" "${APP_DIR}/"
chown -R "${APP_USER}":"${APP_USER}" "${APP_DIR}"

echo "Creating Python virtual environment..."
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip wheel
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

if [[ ! -f /etc/huz-backend.env ]]; then
  echo "Creating /etc/huz-backend.env from template..."
  cp "${APP_DIR}/deploy/digitalocean/env.production.example" /etc/huz-backend.env
  chmod 600 /etc/huz-backend.env
fi

echo "Installing systemd service..."
sed \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_DIR__|${APP_DIR}|g" \
  "${APP_DIR}/deploy/digitalocean/huz-backend.service.template" \
  > "/etc/systemd/system/${SERVICE_NAME}.service"

echo "Installing nginx site..."
sed \
  -e "s|__SERVER_NAMES__|${SERVER_NAMES}|g" \
  -e "s|__APP_DIR__|${APP_DIR}|g" \
  -e "s|__MEDIA_ROOT__|${MEDIA_ROOT}|g" \
  "${APP_DIR}/deploy/digitalocean/nginx.conf.template" \
  > "/etc/nginx/sites-available/${SERVICE_NAME}"

ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
nginx -t
systemctl restart nginx
systemctl enable nginx
systemctl enable "${SERVICE_NAME}"

cat <<EOF

Bootstrap finished.

Next steps:
1. Edit /etc/huz-backend.env and replace all placeholder values.
2. If you use Firebase, upload its JSON key outside the git checkout and set FIREBASE_CREDENTIAL_PATH in /etc/huz-backend.env.
3. Run:
   sudo bash ${APP_DIR}/deploy/digitalocean/release.sh
4. After the app is responding on HTTP, run:
   sudo certbot --nginx -d ${SERVER_NAMES// / -d }

EOF
