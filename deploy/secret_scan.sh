#!/usr/bin/env bash
# Fails if a tracked file holds something shaped like a real credential. The repo
# is public, so anything committed is published.
set -uo pipefail
pattern='AIza[0-9A-Za-z_-]{30,}|sk_(live|test)_[0-9a-zA-Z]{20,}|pk_(live|test)_[0-9a-zA-Z]{20,}|xkeysib-[0-9a-z]{20,}|ghp_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{30,}|BEGIN [A-Z ]*PRIVATE KEY'
if git grep -nIE "$pattern" -- . ':!deploy/secret_scan.sh'; then
	echo "Possible secrets above. Remove them, and replace any that were real."
	exit 1
fi
echo "No secrets found."
