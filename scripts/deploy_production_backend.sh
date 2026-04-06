#!/usr/bin/env bash

set -euo pipefail

REPO_PATTERN="Huz-backend_New"
TARGET_BRANCH="main"

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

find_repo_dir() {
  local candidates=(
    "$HOME/Huz-Backend"
    "$HOME/Huz-backend_New"
    "$HOME/backend"
    "$HOME/app"
  )
  local dir
  local remote_url

  for dir in "${candidates[@]}"; do
    if [ ! -d "$dir/.git" ]; then
      continue
    fi

    remote_url="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
    case "$remote_url" in
      *"$REPO_PATTERN"*)
        printf '%s\n' "$dir"
        return 0
        ;;
    esac
  done

  while IFS= read -r dir; do
    remote_url="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
    case "$remote_url" in
      *"$REPO_PATTERN"*)
        printf '%s\n' "$dir"
        return 0
        ;;
    esac
  done < <(find "$HOME" -maxdepth 4 -type d -name .git 2>/dev/null | sed 's#/.git$##')

  return 1
}

find_service_name() {
  local repo_dir="$1"
  local unit

  while IFS= read -r unit; do
    if [ -z "$unit" ]; then
      continue
    fi

    if systemctl cat "$unit" 2>/dev/null | grep -Fq "$repo_dir"; then
      printf '%s\n' "$unit"
      return 0
    fi
  done < <(systemctl list-units --type=service --all --no-legend 2>/dev/null | awk '{print $1}' | grep -Ei 'gunicorn|huz|django' || true)

  return 1
}

restart_service() {
  local service_name="$1"

  if sudo -n true >/dev/null 2>&1; then
    sudo -n systemctl restart "$service_name"
    sudo -n systemctl is-active --quiet "$service_name" || fail "Service $service_name is not active after restart."
    return 0
  fi

  if systemctl --user status "$service_name" >/dev/null 2>&1; then
    systemctl --user restart "$service_name"
    systemctl --user is-active --quiet "$service_name" || fail "User service $service_name is not active after restart."
    return 0
  fi

  fail "Unable to restart backend service $service_name. Grant passwordless sudo or configure a user service."
}

main() {
  local repo_dir
  local service_name

  repo_dir="$(find_repo_dir)" || fail "Could not locate the deployed backend repository for $REPO_PATTERN on the production server."
  log "Using backend repo: $repo_dir"

  cd "$repo_dir"

  if [ ! -f ".venv/bin/activate" ]; then
    fail "Missing backend virtualenv at $repo_dir/.venv."
  fi

  git fetch origin "$TARGET_BRANCH"
  git reset --hard "origin/$TARGET_BRANCH"

  # Keep runtime-only server artifacts intact.
  git clean -fd \
    -e .env \
    -e .venv/ \
    -e media/ \
    -e static/ \
    -e staticfiles/

  # shellcheck disable=SC1091
  source ".venv/bin/activate"

  python manage.py check
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput

  python manage.py shell -c "from django.urls import resolve; print(resolve('/api/v1/operator/auth/login/').url_name); print(resolve('/api/v1/auth/users/exists/').url_name); print(resolve('/api/v1/packages/public/').url_name)"

  service_name="$(find_service_name "$repo_dir")" || fail "Could not detect the backend systemd service. Expected a unit referencing $repo_dir."
  log "Restarting backend service: $service_name"
  restart_service "$service_name"

  log "Deployed commit: $(git rev-parse HEAD)"
}

main "$@"
