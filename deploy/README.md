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

## Server steps that need root

Run these from a root session, and keep that session open until each is checked. They're written out here so nothing has to be worked out on the day.

**fail2ban.** Check your address first with `echo $SSH_CLIENT`, and put it after `ignoreip`.

```bash
apt-get update && apt-get install -y fail2ban
```

```bash
cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 YOUR.ADDRESS.HERE
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = ssh
backend = auto
logpath = /var/log/auth.log
EOF
```

```bash
fail2ban-client -t && systemctl enable fail2ban && systemctl restart fail2ban && fail2ban-client status sshd
```

**Limited restart rule.** `visudo -cf` refuses a broken file, so a typo can't lock out `sudo`.

```bash
cat > /tmp/akwaaba-restart <<'EOF'
akwaaba ALL=(root) NOPASSWD: /usr/bin/supervisorctl status *, /usr/bin/supervisorctl restart frappe-bench-web\: frappe-bench-workers\:
EOF
visudo -cf /tmp/akwaaba-restart && install -m 440 /tmp/akwaaba-restart /etc/sudoers.d/akwaaba-restart && rm /tmp/akwaaba-restart
```

**More swap**, before any `bench build`. The server has 2 GB of memory and 1 GB of swap. This adds 2 GB more.

```bash
fallocate -l 2G /swapfile2 && chmod 600 /swapfile2 && mkswap /swapfile2 && swapon /swapfile2 && echo '/swapfile2 none swap sw 0 0' >> /etc/fstab && free -h
```

**Root password login off.** Test a new key login as `akwaaba` from a second window before closing the root session.

```bash
echo 'PermitRootLogin prohibit-password' > /etc/ssh/sshd_config.d/10-no-root-password.conf && sshd -t && systemctl reload ssh
```
