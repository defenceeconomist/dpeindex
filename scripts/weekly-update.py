#!/usr/bin/env python3
"""Standalone launchd supervisor; installed on the internal disk with no dependencies."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import smtplib
import signal
import ssl
import subprocess
import time
import traceback
from email.message import EmailMessage

SUPPORT = Path.home() / 'Library/Application Support/dpe-update'
LOGS = Path.home() / 'Library/Logs/dpe-update'
SERVICE = 'com.lukeheley.dpe-update.smtp'
ADDRESS = 'lajh87@me.com'


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def command(args, *, capture=False, cwd=None, timeout=180):
    with subprocess.Popen(args, cwd=cwd, text=True, start_new_session=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
    return stdout.strip() if capture else None


def send_email(subject, body):
    # Never print, persist, or pass the password in command-line arguments.
    password = command(['/usr/bin/security', 'find-generic-password', '-s', SERVICE,
                        '-a', ADDRESS, '-w'], capture=True, timeout=15)
    message = EmailMessage()
    message['From'] = ADDRESS
    message['To'] = ADDRESS
    message['Subject'] = subject
    message.set_content(body)
    with smtplib.SMTP('smtp.mail.me.com', 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(ADDRESS, password)
        smtp.send_message(message)


def queue_email(state, subject, body):
    outbox = read(state / 'outbox.json', [])
    outbox.append({'subject': subject, 'body': body})
    save(state / 'outbox.json', outbox)


def flush_email(state):
    outbox = read(state / 'outbox.json', [])
    while outbox:
        for attempt in range(3):
            try:
                send_email(**outbox[0])
                break
            except Exception as error:
                print(f'Email attempt {attempt + 1} failed: {type(error).__name__}', flush=True)
                if attempt == 2:
                    raise RuntimeError('Email delivery failed; notification retained in outbox.json') from None
                time.sleep(5 * (attempt + 1))
        outbox.pop(0)
        save(state / 'outbox.json', outbox)


def confirm_pages(config, commit):
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        build = json.loads(command([config['gh'], 'api',
                            'repos/defenceeconomist/dpeindex/pages/builds/latest'], capture=True))
        if build.get('commit') == commit:
            if build.get('status') == 'built':
                return
            if build.get('status') == 'errored':
                raise RuntimeError(f'GitHub Pages deployment failed: {build.get("error")}')
        time.sleep(20)
    raise TimeoutError(f'GitHub Pages did not confirm deployment of {commit} within 15 minutes')


def publish(config, state, pending):
    # Keep manual MCP refreshes from advancing the current release during publication.
    with (Path(config['root']) / 'data/local-artifacts/.mcp-update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('An MCP update is running; publication retained for retry') from None
        return publish_locked(config, state, pending)


def publish_locked(config, state, pending):
    root = Path(config['root'])
    release = Path(pending['workspace']) / 'release'
    command([config['python'], '-m', 'app.scheduled_update', 'verify', str(release)], cwd=root, timeout=600)
    command([config['node'], 'scripts/publish-static.mjs', str(release / 'site')], cwd=root, timeout=1800)
    commit = command([config['gh'], 'api', 'repos/defenceeconomist/dpeindex/git/ref/heads/gh-pages',
                      '--jq', '.object.sha'], capture=True)
    # Check that the remote tip contains this exact release before accepting deployment.
    remote_release = json.loads(command([config['gh'], 'api',
        'repos/defenceeconomist/dpeindex/contents/release.json?ref=' + commit,
        '-H', 'Accept: application/vnd.github.raw+json'], capture=True))
    if remote_release != read(release / 'site/release.json'):
        raise RuntimeError('Published branch changed before deployment confirmation')
    confirm_pages(config, commit)
    report = read(release / 'update-report.json')
    summary = {key: len(report['changes'][key]) for key in ('added', 'changed', 'removed')}
    save(state / 'published.json', {'release': str(release), 'commit': commit, 'changes': summary})
    # Record confirmed deployment before queuing the success notification.
    pending.update(deployed=True, commit=commit, changes=summary)
    save(state / 'pending.json', pending)
    return pending


def execute(config, state):
    root = Path(config['root'])
    if not root.is_dir():
        raise RuntimeError(f'Project unavailable; check the external SSD: {root}')
    pending = read(state / 'pending.json')
    if not pending:
        result_path = state / 'check.json'
        result_path.unlink(missing_ok=True)
        command([config['python'], '-m', 'app.scheduled_update', 'check', str(state)], cwd=root, timeout=86400)
        result = read(result_path)
        if result['status'] == 'unchanged':
            return result
        pending = read(state / 'pending.json')
    if not pending.get('deployed'):
        command([config['python'], '-m', 'app.scheduled_update', 'resume', str(state)], cwd=root, timeout=86400)
        pending = publish(config, state, pending)
    return {'status': 'published', **pending}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-email', action='store_true')
    args = parser.parse_args()
    SUPPORT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    os.chmod(SUPPORT, 0o700)
    os.chmod(LOGS, 0o700)
    config = read(SUPPORT / 'config.json')
    os.environ['PATH'] = config['path']
    if args.test_email:
        send_email('DPE scheduled update: email test', 'Email delivery is configured for Friday 09:00 UK checks.\n')
        print('Test email accepted by iCloud SMTP.')
        return 0
    with (SUPPORT / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Scheduled update already running; duplicate invocation skipped.')
            return 0
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        log_path = LOGS / (run_id + '.log')
        result = {'started_at': run_id, 'log': str(log_path)}
        # Redirect OS descriptors too, so child update and publishing output is retained.
        with log_path.open('a', buffering=1) as output:
            os.dup2(output.fileno(), 1)
            os.dup2(output.fileno(), 2)
            with redirect_stdout(output), redirect_stderr(output):
                try:
                    print(f'Starting Friday data check: {run_id}', flush=True)
                    flush_email(SUPPORT)
                    result.update(execute(config, SUPPORT))
                    if result['status'] == 'published':
                        queue_email(SUPPORT, 'DPE updated and published',
                            f"Changes: {result['changes']}\nCommit: {result['commit']}\n"
                            f"Website: https://defenceeconomist.github.io/dpeindex/\nLog: {log_path}\n")
                        (SUPPORT / 'pending.json').unlink()
                except Exception as error:
                    traceback.print_exc()
                    result.update(status='error', error=str(error))
                    queue_email(SUPPORT, 'DPE weekly update: error',
                        f'{type(error).__name__}: {error}\nLog: {log_path}\n'
                        'Pending work is retained for retry. Review blockers require manual correction.\n')
                try:
                    flush_email(SUPPORT)
                except Exception as error:
                    result['notification_error'] = str(error)
                result['finished_at'] = datetime.now(timezone.utc).isoformat()
                save(LOGS / (run_id + '.json'), result)
                save(SUPPORT / 'last-run.json', result)
                print(json.dumps(result), flush=True)
                for path in LOGS.iterdir():
                    if path.suffix in ('.log', '.json') and path.name[:8].isdigit() and path.stat().st_mtime < time.time() - 90 * 86400:
                        path.unlink()
        return int(result['status'] == 'error' or 'notification_error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
