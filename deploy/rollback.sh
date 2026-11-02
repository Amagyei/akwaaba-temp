#!/usr/bin/env bash
# Puts akwaaba_theme back on an earlier version. Run on the server.
#
#   rollback.sh [git-ref]     defaults to the version before the last deploy
#
# This moves the CODE only, on purpose. Migrations aren't undone, and that's
# normally harmless: older code ignores fields it doesn't know about.
#
# Restoring the DATABASE is a separate decision for a person, because it erases
# every order, payment and stock entry since the backup. Check first:
#   bench --site akwaaba.prod mariadb -e "select count(*) from \`tabSales Order\` where creation > 'BACKUP TIME'"
# and only then, in maintenance mode:
#   bench --site akwaaba.prod restore <backup.sql.gz>
# The last deploy's backup is named in ~/deploy-state/previous-backup.
set -euo pipefail

BENCH="${AKW_BENCH:-$HOME/frappe-bench}"
SITE="${AKW_SITE:-akwaaba.prod}"
URL="${AKW_URL:-https://serwakcollections.com}"
RESTART_GROUPS="frappe-bench-web: frappe-bench-workers:"
STATE_DIR="$HOME/deploy-state"
APP_DIR="$BENCH/apps/akwaaba_theme"
export PATH="$HOME/.local/bin:$PATH"

REF="${1:-$(cat "$STATE_DIR/previous" 2>/dev/null || true)}"
if [ -z "$REF" ]; then
	echo "Usage: rollback.sh <git-ref>. No previous deploy is on record." >&2
	exit 2
fi
TARGET="$(git -C "$APP_DIR" rev-parse --verify "$REF^{commit}")"

# This file may be replaced by the checkout below, so bash must not keep reading it.
{
	echo "Going back to ${TARGET:0:7}"
	git -C "$APP_DIR" checkout --quiet --detach "$TARGET"
	(cd "$BENCH" && bench --site "$SITE" clear-cache && bench --site "$SITE" clear-website-cache)
	# shellcheck disable=SC2086
	sudo -n supervisorctl restart $RESTART_GROUPS
	(cd "$BENCH" && bench --site "$SITE" set-maintenance-mode off)
	for attempt in 1 2 3 4 5 6; do
		curl -fs -o /dev/null --max-time 20 "$URL/api/method/ping" && break
		echo "Waiting for the site to come back ($attempt)…"
		sleep 10
	done
	echo "Code is back on ${TARGET:0:7}. The database wasn't touched."
	exit 0
}
