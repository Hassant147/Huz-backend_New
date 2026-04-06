#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/huz-backend}"
BRANCH="${BRANCH:-main}"
RELEASE_SCRIPT="${RELEASE_SCRIPT:-${APP_DIR}/deploy/digitalocean/release.sh}"
LOCK_FILE="${LOCK_FILE:-/tmp/huz-backend-github-actions.lock}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "Git checkout not found at ${APP_DIR}"
  exit 1
fi

if [[ ! -x "${RELEASE_SCRIPT}" ]]; then
  echo "Release script not found or not executable: ${RELEASE_SCRIPT}"
  exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another deploy is already running."
  exit 0
fi

cd "${APP_DIR}"

# Older single-branch clones need to learn about main before pull works.
git remote set-branches --add origin "${BRANCH}" >/dev/null 2>&1 || true
git fetch origin "${BRANCH}"

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${current_branch}" != "${BRANCH}" ]]; then
  git checkout -B "${BRANCH}" "origin/${BRANCH}"
fi

# Production should always mirror origin/<branch>; discard local drift.
git reset --hard "origin/${BRANCH}"
git clean -fd \
  -e .env \
  -e .venv/ \
  -e media/ \
  -e static/ \
  -e staticfiles/

sudo /usr/bin/bash "${RELEASE_SCRIPT}"
