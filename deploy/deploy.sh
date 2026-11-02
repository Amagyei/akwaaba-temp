#!/usr/bin/env bash
# Deploys one version of akwaaba_theme to this server. Run on the server, as the
# bench user. Every step that could hurt comes after a verified backup.
#
#   deploy.sh <git-ref>            deploy that tag, branch or commit
#   deploy.sh <git-ref> --check    only run the preflight, and change nothing
#
# Framework apps are never touched: this refuses to run if they differ from
# deploy/apps.lock.json. Never `bench update` here, which would move them all.
set -euo pipefail

BENCH="${AKW_BENCH:-$HOME/frappe-bench}"
SITE="${AKW_SITE:-akwaaba.prod}"
CANARY="${AKW_CANARY:-akwaaba.staging}"   # migrated first, so a bad patch stops here
URL="${AKW_URL:-https://serwakcollections.com}"
REMOTE="${AKW_REMOTE:-upstream}"
APP=akwaaba_theme
MIN_DISK_GB=2
MIN_MEM_MB=300
RESTART_GROUPS="frappe-bench-web: frappe-bench-workers:"
STATE_DIR="$HOME/deploy-state"

REF="${1:-}"
MODE="${2:-}"
if [ -z "$REF" ]; then
	echo "Usage: deploy.sh <git-ref> [--check]" >&2
	exit 2
fi

export PATH="$HOME/.local/bin:$PATH"
APP_DIR="$BENCH/apps/$APP"
PYTHON="$BENCH/env/bin/python"

# `git checkout` below replaces this file, so run from a private copy.
if [ "$MODE" != "--check" ] && [ -z "${AKW_DEPLOY_COPY:-}" ] && [ -f "$0" ]; then
	copy="$(mktemp /tmp/akw-deploy.XXXXXX)"
	cp "$0" "$copy"
	AKW_DEPLOY_COPY="$copy" exec bash "$copy" "$@"
fi

step() { printf '\n== %s\n' "$*"; }

preflight() {
	local problems=0 target_lock="$1"
	note_problem() { echo "  PROBLEM  $*"; problems=$((problems + 1)); }

	command -v bench >/dev/null || note_problem "bench isn't on PATH"
	[ -d "$APP_DIR/.git" ] || note_problem "$APP_DIR isn't a git checkout"

	local free_gb
	free_gb="$(df -BG --output=avail "$BENCH" | tail -n 1 | tr -dc 0-9)"
	[ "$free_gb" -ge "$MIN_DISK_GB" ] || note_problem "only ${free_gb} GB of disk free, need ${MIN_DISK_GB}"

	local avail_mb
	avail_mb="$(awk '/MemAvailable|SwapFree/ {sum += $2} END {print int(sum / 1024)}' /proc/meminfo)"
	[ "$avail_mb" -ge "$MIN_MEM_MB" ] || note_problem "only ${avail_mb} MB of memory free, need ${MIN_MEM_MB}"

	if [ -n "$(git -C "$APP_DIR" status --porcelain)" ]; then
		note_problem "$APP has uncommitted changes on the server"
	fi

	if [ -s "$target_lock" ]; then
		local app want have
		while read -r app want; do
			have="$(git -C "$BENCH/apps/$app" rev-parse HEAD 2>/dev/null || echo missing)"
			[ "$have" = "$want" ] || note_problem "$app is at ${have:0:7}, the lock says ${want:0:7}"
		done < <("$PYTHON" -I -c 'import json,sys; [print(a, v["commit"]) for a, v in json.load(open(sys.argv[1]))["apps"].items()]' "$target_lock" 2>/dev/null)
	else
		note_problem "couldn't read deploy/apps.lock.json for $REF"
	fi

	sudo -n supervisorctl status frappe-bench-web: >/dev/null 2>&1 \
		|| note_problem "can't restart services: the limited sudoers rule for supervisorctl is missing"

	curl -fs -o /dev/null --max-time 30 "$URL/api/method/ping" || note_problem "$URL isn't answering"

	if [ "$problems" -gt 0 ]; then
		echo "Preflight found $problems problem(s)."
		return 1
	fi
	echo "Preflight passed."
}

lock_for_ref="$(mktemp)"
trap 'rm -f "$lock_for_ref"' EXIT

if [ "$MODE" = "--check" ]; then
	# Nothing on the server changes, so nothing is fetched: use a lock file given
	# by the caller, or what's already in this checkout.
	if [ -n "${AKW_LOCK_FILE:-}" ]; then cp "$AKW_LOCK_FILE" "$lock_for_ref"; else
		git -C "$APP_DIR" show "$REF:deploy/apps.lock.json" > "$lock_for_ref" 2>/dev/null || true
	fi
	step "Preflight for $REF (no changes)"
	preflight "$lock_for_ref"
	exit $?
fi

mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
flock -n 9 || { echo "Another deploy is running." >&2; exit 1; }
LOG="$STATE_DIR/deploy-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

step "Fetching $REF"
git -C "$APP_DIR" fetch --tags --prune "$REMOTE" "+refs/heads/*:refs/remotes/$REMOTE/*"
TARGET="$(git -C "$APP_DIR" rev-parse --verify --quiet "$REMOTE/$REF^{commit}" \
	|| git -C "$APP_DIR" rev-parse --verify "$REF^{commit}")"
PREVIOUS="$(git -C "$APP_DIR" rev-parse HEAD)"
echo "Going from ${PREVIOUS:0:7} to ${TARGET:0:7}"
git -C "$APP_DIR" show "$TARGET:deploy/apps.lock.json" > "$lock_for_ref"

step "Preflight"
preflight "$lock_for_ref"

if ! git -C "$APP_DIR" diff --quiet "$PREVIOUS" "$TARGET" -- pyproject.toml; then
	echo "pyproject.toml changed, so Python dependencies may have too. This script doesn't install those."
	exit 1
fi

phase="before anything changed"
on_failure() {
	local code=$?
	[ "$code" -eq 0 ] && return
	echo
	echo "DEPLOY FAILED $phase. Log: $LOG"
	case "$phase" in
		"after the backup"|"while migrating")
			echo "$SITE stays in maintenance mode so nobody uses a half-migrated site."
			echo "Backup: ${BACKUP_DB:-see private/backups}"
			echo "Back to the old code: deploy/rollback.sh ${PREVIOUS:0:12}"
			echo "Then, if the database needs restoring, read that script's notes first."
			;;
	esac
}
trap 'on_failure; rm -f "$lock_for_ref" "${AKW_DEPLOY_COPY:-}"' EXIT

step "Backup with files"
stamp="$(mktemp)"
(cd "$BENCH" && bench --site "$SITE" backup --with-files)
BACKUP_DB="$(find "$BENCH/sites/$SITE/private/backups" -name '*database.sql.gz' -newer "$stamp" | sort | tail -n 1)"
rm -f "$stamp"
[ -n "$BACKUP_DB" ] && [ -s "$BACKUP_DB" ] || { echo "No fresh database backup was written."; exit 1; }
gzip -t "$BACKUP_DB"
echo "Verified: $BACKUP_DB ($(du -h "$BACKUP_DB" | cut -f1))"
echo "$PREVIOUS" > "$STATE_DIR/previous"
echo "$BACKUP_DB" > "$STATE_DIR/previous-backup"
phase="after the backup"

step "Maintenance mode on"
(cd "$BENCH" && bench --site "$SITE" set-maintenance-mode on)

step "Checking out ${TARGET:0:7}"
git -C "$APP_DIR" checkout --quiet --detach "$TARGET"

phase="while migrating"
if [ -n "$CANARY" ] && [ -d "$BENCH/sites/$CANARY" ]; then
	step "Migrating $CANARY first"
	(cd "$BENCH" && bench --site "$CANARY" migrate)
fi
step "Migrating $SITE"
(cd "$BENCH" && bench --site "$SITE" migrate)

step "Clearing caches"
(cd "$BENCH" && bench --site "$SITE" clear-cache && bench --site "$SITE" clear-website-cache)

step "Restarting web and workers"
# shellcheck disable=SC2086
sudo -n supervisorctl restart $RESTART_GROUPS

step "Maintenance mode off"
(cd "$BENCH" && bench --site "$SITE" set-maintenance-mode off)
phase="during smoke checks"

step "Smoke checks"
for attempt in 1 2 3 4 5 6; do
	curl -fs -o /dev/null --max-time 20 "$URL/api/method/ping" && break
	echo "Waiting for the site to come back ($attempt)…"
	sleep 10
done
if ! bash "$APP_DIR/deploy/smoke.sh" "$URL"; then
	echo "Smoke checks failed, so going back to ${PREVIOUS:0:7}. The database stays as migrated."
	bash "$APP_DIR/deploy/rollback.sh" "$PREVIOUS"
	exit 1
fi

phase="after finishing"
echo "$TARGET" > "$STATE_DIR/current"
echo
echo "Deployed ${TARGET:0:7} to $SITE. Previous version: ${PREVIOUS:0:7}. Log: $LOG"
