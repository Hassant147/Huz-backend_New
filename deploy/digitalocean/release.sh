#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/huz-backend}"
APP_USER="${APP_USER:-huz}"
SERVICE_NAME="${SERVICE_NAME:-huz-backend}"
ENV_FILE="${ENV_FILE:-/etc/huz-backend.env}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo."
  exit 1
fi

if [[ ! -d "${APP_DIR}" ]]; then
  echo "App directory not found: ${APP_DIR}"
  exit 1
fi

cd "${APP_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

if [[ ! -d .venv ]]; then
  sudo -E -u "${APP_USER}" python3 -m venv .venv
fi

sudo -E -u "${APP_USER}" .venv/bin/pip install --upgrade pip wheel
sudo -E -u "${APP_USER}" .venv/bin/pip install -r requirements.txt
sudo -E -u "${APP_USER}" .venv/bin/python manage.py migrate --noinput
sudo -E -u "${APP_USER}" .venv/bin/python manage.py collectstatic --noinput
sudo -E -u "${APP_USER}" .venv/bin/python manage.py check --deploy

systemctl restart "${SERVICE_NAME}"
systemctl status "${SERVICE_NAME}" --no-pager --lines=20
