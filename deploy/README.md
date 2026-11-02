# Deploying

Live: `akwaaba.prod` on the RackNerd VPS, bench at `~/frappe-bench`, user `akwaaba`.

`deploy.sh <ref>` runs on the server: preflight → verified backup with files → maintenance mode → checkout → migrate the staging site, then live → clear caches → restart → maintenance off → smoke checks. If the smoke checks fail it goes back to the old code by itself. If a migrate fails, the site stays in maintenance mode for a person to decide.

Before the very first deploy, the server has no copy of these scripts:

```bash
cd ~/frappe-bench/apps/akwaaba_theme && git fetch upstream && git show upstream/staging:deploy/deploy.sh > /tmp/deploy.sh && bash /tmp/deploy.sh staging
```

| File | What it does |
|---|---|
| `deploy.sh <ref> [--check]` | Deploys a tag, branch or commit. `--check` only runs the preflight. |
| `rollback.sh [ref]` | Goes back to earlier code. It leaves the database alone; its header explains when a restore is right. |
| `smoke.sh [url]` | Read-only checks, safe to run from anywhere at any time. |
| `apps.lock.json` | The exact framework commits live runs. CI tests against them, and a deploy refuses to run if the server differs. |

It needs one sudoers rule for the bench user, added from root:

```
akwaaba ALL=(root) NOPASSWD: /usr/bin/supervisorctl status *, /usr/bin/supervisorctl restart frappe-bench-web\: frappe-bench-workers\:
```

Never run `bench update` on live. It would move every framework app and run their patches on the ledger.
