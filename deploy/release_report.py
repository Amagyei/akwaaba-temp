#!/usr/bin/env python3
"""What a release changes in the database, compared with what live runs.

Removing a field or changing its type can lose data on `bench migrate`, so those
are called out. Usage: release_report.py [from-commit]. Defaults to live's commit
in apps.lock.json. Standard library only.
"""

import json
import subprocess
import sys


def git(*args):
	return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def doctypes(commit):
	found = {}
	for path in git("ls-tree", "-r", "--name-only", commit, "akwaaba_theme/").splitlines():
		parts = path.split("/")
		if path.endswith(".json") and "doctype" in parts and parts[-1] == parts[-2] + ".json":
			try:
				doc = json.loads(git("show", f"{commit}:{path}"))
			except ValueError:
				continue
			found[doc.get("name") or parts[-2]] = {f["fieldname"]: f["fieldtype"] for f in doc.get("fields", [])}
	return found


def main():
	with open("deploy/apps.lock.json") as f:
		base = sys.argv[1] if len(sys.argv) > 1 else json.load(f)["akwaaba_theme_live"]
	old, new = doctypes(base), doctypes("HEAD")
	risky, safe = [], []

	for name in sorted(set(old) - set(new)):
		risky.append(f"DocType removed: {name}. Migrate leaves its table, but nothing uses it now.")
	for name in sorted(set(new) - set(old)):
		safe.append(f"New DocType: {name}")
	for name in sorted(set(old) & set(new)):
		for field in sorted(set(old[name]) - set(new[name])):
			risky.append(f"Field removed: {name}.{field}")
		for field in sorted(set(old[name]) & set(new[name])):
			if old[name][field] != new[name][field]:
				risky.append(f"Field type changed: {name}.{field}, {old[name][field]} → {new[name][field]}")
		for field in sorted(set(new[name]) - set(old[name])):
			safe.append(f"New field: {name}.{field}")

	patches = [p for p in git("diff", base, "HEAD", "--", "akwaaba_theme/patches.txt").splitlines() if p.startswith("+akwaaba")]
	print(f"## Release report, compared with {base[:7]}\n")
	print("**Needs a look before deploying:**" if risky else "Nothing here can lose data.")
	for line in risky:
		print(f"- {line}")
	if patches:
		print("\n**Patches that will run:**")
		for line in patches:
			print(f"- {line[1:]}")
	if safe:
		print("\n**Adds:**")
		for line in safe:
			print(f"- {line}")


if __name__ == "__main__":
	main()
