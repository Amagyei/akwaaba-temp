#!/usr/bin/env bash
# Builds a bench on the exact framework commits live runs, for CI.
# Usage: ci_bench.sh <path-to-this-repo>
set -euo pipefail

REPO="$1"
LOCK="$REPO/deploy/apps.lock.json"
field() { python -c 'import json,sys; print(json.load(open(sys.argv[1]))["apps"][sys.argv[2]][sys.argv[3]])' "$LOCK" "$1" "$2"; }

pin() {
	local dir="apps/$1" commit remote
	commit="$(field "$1" commit)"
	remote="$(git -C "$dir" remote | head -n 1)"
	git -C "$dir" fetch --quiet --depth 1 "$remote" "$commit"
	git -C "$dir" checkout --quiet "$commit"
}

pip install --quiet frappe-bench
bench init --skip-redis-config-generation --skip-assets --no-backups \
	--frappe-branch "$(field frappe branch)" --python "$(command -v python)" "$HOME/frappe-bench"
cd "$HOME/frappe-bench"
pin frappe

for app in erpnext payments webshop frappe_whatsapp; do
	bench get-app --skip-assets --branch "$(field "$app" branch)" "$(field "$app" repo)"
	pin "$app"
done

cp -R "$REPO" apps/akwaaba_theme
./env/bin/pip install --quiet -e apps/akwaaba_theme
grep -qx akwaaba_theme sites/apps.txt || echo akwaaba_theme >> sites/apps.txt
bench setup requirements

bench set-config -g db_host 127.0.0.1
bench set-config -g redis_cache redis://127.0.0.1:6379
bench set-config -g redis_queue redis://127.0.0.1:6379
bench set-config -g redis_socketio redis://127.0.0.1:6379

# A throwaway site that exists only for this run.
bench new-site test_site --db-root-password root --admin-password admin \
	--mariadb-user-host-login-scope=% --install-app erpnext
bench --site test_site install-app payments webshop frappe_whatsapp akwaaba_theme
bench --site test_site set-config allow_tests true
bench use test_site

for app in frappe erpnext payments webshop frappe_whatsapp; do
	echo "$app at $(git -C "apps/$app" rev-parse --short HEAD), lock says $(field "$app" commit | cut -c1-7)"
done
