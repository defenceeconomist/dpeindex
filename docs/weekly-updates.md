# Weekly Mac mini updates

The local LaunchAgent `com.lukeheley.dpe-update` checks the complete RePEc catalogue
on Fridays at **09:00**, using the Mac's Europe/London timezone (GMT/BST).
It runs independently of Codex while the user is logged in. A sleeping Mac runs a
missed calendar job after waking; a powered-off Mac cannot check or send email.
The project SSD must be mounted to update the site.

Unchanged parsed records produce only a log entry. Changes trigger an incremental
refresh with the existing model, citation gates, integrity checks, and browser
tests. Removed records stop for manual review. A successful refresh is published
to `gh-pages`, and GitHub Pages must confirm that exact commit before a success
email is queued. No automatic retraining or bypassing review is performed.

Email is sent from and to `lajh87@me.com` for publication success or errors, using
iCloud SMTP, STARTTLS, and an app-specific password in macOS Keychain. No email
is sent for an unchanged catalogue. Email outages leave a durable outbox;
notifications are retried three times and again on the next invocation. If email
itself is unavailable, consult the logs. A failure delivering an older notification
stops the next check until notification delivery recovers.

## Installation

Run from the repository:

```sh
python3 scripts/install-weekly-update.py --setup-email --enable
```

This opens a secure password dialog, stores the password directly using macOS
Security.framework, sends a test email, and registers the LaunchAgent only after
SMTP accepts that email. Generate the password in your Apple Account's
[app-specific password settings](https://support.apple.com/en-gb/102654).
The password is never passed as a command-line argument or saved to a file.
If macOS requests Keychain access for the `security` utility, allow access so
future unattended runs can retrieve this credential.

The installer copies the existing Python 3.12 runtime out of its temporary
location into `~/Library/Application Support/dpe-update/python/` and reconnects
the existing model environment to that persistent runtime. The supervisor and
configuration also live on the internal disk so an unavailable SSD can be
reported. Run the installer again after changing supervisor code; use `--enable`
to reload the installed job with the existing credential (sends a test email).
Without `--enable`, installation is staged outside LaunchAgents.

## Inspect and run

Logs and per-run JSON summaries are in `~/Library/Logs/dpe-update/` and retained
for 90 days. `launchd.out.log` and `launchd.err.log` capture supervisor startup
problems. The latest structured result is
`~/Library/Application Support/dpe-update/last-run.json`.

```sh
launchctl print "gui/$(id -u)/com.lukeheley.dpe-update"
launchctl kickstart "gui/$(id -u)/com.lukeheley.dpe-update"
```

To run directly, including before enabling the schedule:

```sh
"$HOME/Library/Application Support/dpe-update/python/bin/python3.12" \
  "$HOME/Library/Application Support/dpe-update/weekly-update.py"
```

Use `--test-email` with that command to test mail delivery alone. Logs contain
pipeline output, failed command details, workspace paths, and publication output.
Concurrent scheduled invocations are skipped; update workers share the MCP lock.

## Recover and disable

`pending.json` in the support directory records a prepared workspace until
publication is confirmed and its notification is queued. Failed refreshes resume
verified stages on the next run. Failed publication retries the existing release
without rebuilding, even when RePEc has no new records. Review citation blockers
in the workspace's `review.json` and correct `inputs/decisions.json` before retrying.
If the current release has changed manually, or a completed artifact is damaged,
inspect the pending workspace and archive `pending.json` before starting a fresh
check; do not bypass the integrity checks.

`outbox.json` retains undelivered notifications; `published.json` records the last
confirmed scheduled publication. A crash immediately around SMTP delivery can
cause a duplicate email on retry, since SMTP has no transactional delivery receipt.

Disable and prevent reloading at next login:

```sh
launchctl bootout "gui/$(id -u)/com.lukeheley.dpe-update"
mv "$HOME/Library/LaunchAgents/com.lukeheley.dpe-update.plist" \
   "$HOME/Library/Application Support/dpe-update/com.lukeheley.dpe-update.plist"
```

Existing releases remain available for rollback using the normal static publisher.
