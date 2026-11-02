#!/usr/bin/env bash
# Read-only checks that the storefront works, including that every CSS and JS
# file a page asks for really exists. A stale assets.json once took all styling
# down while the pages themselves still answered 200.
#
# Usage: smoke.sh [base-url]
set -uo pipefail

BASE="${1:-https://serwakcollections.com}"
BASE="${BASE%/}"
failures=0
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() {
	echo "  FAIL  $1"
	failures=$((failures + 1))
}

fetch_page() {
	# Leaves the body in $work/page. Answers whether the page is healthy.
	local path="$1" code
	code="$(curl -s -o "$work/page" -w '%{http_code}' --max-time 40 "$BASE$path")"
	if [ "$code" != "200" ]; then
		fail "$path answered $code"
		return 1
	fi
	if grep -qE 'Traceback \(most recent call last\)|Internal Server Error' "$work/page"; then
		fail "$path shows a server error"
		return 1
	fi
	echo "  ok    $path"
}

code="$(curl -s -o "$work/page" -w '%{http_code}' --max-time 40 "$BASE/api/method/ping")"
if [ "$code" = "200" ] && grep -q pong "$work/page"; then echo "  ok    ping"; else fail "ping answered $code"; fi

: > "$work/urls"
collect_assets() { grep -oE '/assets/[^"'"'"' )?]+\.(css|js)' "$work/page" | sort -u >> "$work/urls"; }

product=""
if fetch_page /shop; then
	collect_assets
	product="$(grep -oE '/product/[^"'"'"' ]+' "$work/page" | head -n 1)"
	# A few product photos, without downloading the whole catalog.
	grep -oE '/files/[^"'"'"' )?]+' "$work/page" | sort -u | head -n 8 >> "$work/urls"
fi
[ -n "$product" ] || fail "/shop lists no products"

for path in / "$product" /cart /login /order/SAL-ORD-1999-00001; do
	[ -n "$path" ] || continue
	fetch_page "$path" && collect_assets
done

# Each file once, a few at a time: the server is small and the link is slow.
sort -u "$work/urls" -o "$work/urls"
total="$(wc -l < "$work/urls" | tr -d ' ')"
xargs -P 6 -I {} curl -s -o /dev/null -w '%{http_code} {}\n' --max-time 60 "$BASE{}" < "$work/urls" > "$work/results"
while read -r code url; do
	[ "$code" = "200" ] || fail "$url answered $code"
done < "$work/results"

if [ "$total" -lt 5 ]; then
	fail "found only $total asset and photo URLs, which looks wrong"
fi

if [ "$failures" -gt 0 ]; then
	echo "Smoke checks: $failures FAILED"
	exit 1
fi
echo "Smoke checks passed ($total files fetched)."
