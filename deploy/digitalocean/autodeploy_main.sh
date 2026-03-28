#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/huz-backend}"
APP_USER="${APP_USER:-huz}"
BRANCH="${BRANCH:-main}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
RELEASE_SCRIPT="${RELEASE_SCRIPT:-${APP_DIR}/deploy/digitalocean/release.sh}"
LOCK_FILE="${LOCK_FILE:-/var/lock/huz-backend-autodeploy.lock}"
STATE_DIR="${STATE_DIR:-/var/lib/huz-backend-autodeploy}"
LAST_DEPLOY_FILE="${LAST_DEPLOY_FILE:-${STATE_DIR}/last_deployed_sha}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "Git checkout not found at ${APP_DIR}"
  exit 1
fi

if [[ ! -x "${RELEASE_SCRIPT}" ]]; then
  echo "Release script not found or not executable: ${RELEASE_SCRIPT}"
  exit 1
fi

mkdir -p "${STATE_DIR}"
touch "${LOCK_FILE}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another deploy is already running."
  exit 0
fi

run_git() {
  sudo -u "${APP_USER}" git -C "${APP_DIR}" "$@"
}

run_git fetch "${REMOTE_NAME}" "${BRANCH}"

current_branch="$(run_git rev-parse --abbrev-ref HEAD)"
if [[ "${current_branch}" != "${BRANCH}" ]]; then
  echo "Switching checkout from ${current_branch} to ${BRANCH}."
  run_git checkout "${BRANCH}"
fi

local_sha="$(run_git rev-parse HEAD)"
remote_sha="$(run_git rev-parse "${REMOTE_NAME}/${BRANCH}")"
last_deployed_sha=""

if [[ -f "${LAST_DEPLOY_FILE}" ]]; then
  last_deployed_sha="$(<"${LAST_DEPLOY_FILE}")"
fi

if [[ "${local_sha}" == "${remote_sha}" && "${remote_sha}" == "${last_deployed_sha}" ]]; then
  echo "No new revision to deploy."
  exit 0
fi

if [[ "${local_sha}" != "${remote_sha}" ]]; then
  echo "Pulling ${REMOTE_NAME}/${BRANCH} (${remote_sha})..."
  run_git pull --ff-only "${REMOTE_NAME}" "${BRANCH}"
fi

echo "Running release for ${BRANCH}..."
bash "${RELEASE_SCRIPT}"

deployed_sha="$(run_git rev-parse HEAD)"
printf '%s\n' "${deployed_sha}" > "${LAST_DEPLOY_FILE}"
echo "Deployment complete at ${deployed_sha}."
