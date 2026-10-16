#!/usr/bin/env python3
"""Install the local Friday LaunchAgent; --enable after --setup-email succeeds."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = Path.home() / 'Library/Application Support/dpe-update'
LOGS = Path.home() / 'Library/Logs/dpe-update'
LABEL = 'com.lukeheley.dpe-update'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
ADDRESS = 'lajh87@me.com'
SERVICE = 'com.lukeheley.dpe-update.smtp'


def store_password(password):
    """Write directly through Security.framework, avoiding process arguments and files."""
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    pointer = ctypes.c_void_p
    size = ctypes.c_uint32
    security.SecKeychainFindGenericPassword.argtypes = [pointer, size, ctypes.c_char_p, size, ctypes.c_char_p, pointer, pointer, ctypes.POINTER(pointer)]
    security.SecKeychainItemModifyAttributesAndData.argtypes = [pointer, pointer, size, ctypes.c_char_p]
    security.SecKeychainAddGenericPassword.argtypes = [pointer, size, ctypes.c_char_p, size, ctypes.c_char_p, size, ctypes.c_char_p, pointer]
    core.CFRelease.argtypes = [pointer]
    service, account, secret = SERVICE.encode(), ADDRESS.encode(), password.encode()
    item = pointer()
    status = security.SecKeychainFindGenericPassword(None, len(service), service, len(account), account, None, None, ctypes.byref(item))
    if status == 0:
        try:
            status = security.SecKeychainItemModifyAttributesAndData(item, None, len(secret), secret)
        finally:
            core.CFRelease(item)
    elif status == -25300:
        status = security.SecKeychainAddGenericPassword(None, len(service), service, len(account), account, len(secret), secret, None)
    if status:
        raise RuntimeError(f'Keychain storage failed (status {status})')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup-email', action='store_true')
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    SUPPORT.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)
    venv = ROOT / 'data/topic-artifacts/host-venv'
    original = (venv / 'bin/python').resolve().parent.parent
    runtime = SUPPORT / 'python'
    if not runtime.exists():
        shutil.copytree(original, runtime, symlinks=True)
    python = runtime / 'bin/python3.12'
    # Preserve installed dependencies, replacing only the temporary base interpreter.
    target = venv / 'bin/python'
    if target.resolve() != python.resolve():
        target.unlink()
        target.symlink_to(python)
        cfg = venv / 'pyvenv.cfg'
        lines = cfg.read_text().splitlines()
        cfg.write_text('\n'.join('home = ' + str(runtime / 'bin') if line.startswith('home = ') else line for line in lines) + '\n')
    runner = SUPPORT / 'weekly-update.py'
    shutil.copy2(ROOT / 'scripts/weekly-update.py', runner)
    config = {'root': str(ROOT), 'python': str(venv / 'bin/python'),
              'node': shutil.which('node'), 'gh': shutil.which('gh'),
              'path': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}
    if not config['node'] or not config['gh']:
        raise RuntimeError('Node and GitHub CLI are required')
    (SUPPORT / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    definition = {'Label': LABEL, 'ProgramArguments': [str(python), '-u', str(runner)],
                  'StartCalendarInterval': {'Weekday': 5, 'Hour': 9, 'Minute': 0},
                  'EnvironmentVariables': {'PATH': config['path'], 'PYTHONUNBUFFERED': '1'},
                  'StandardOutPath': str(LOGS / 'launchd.out.log'),
                  'StandardErrorPath': str(LOGS / 'launchd.err.log'),
                  'RunAtLoad': False, 'ProcessType': 'Background'}
    # Stage off LaunchAgents until email setup succeeds, avoiding activation on login.
    staged = SUPPORT / (LABEL + '.plist')
    staged.write_bytes(plistlib.dumps(definition))
    subprocess.run(['/usr/bin/plutil', '-lint', str(staged)], check=True)
    if args.setup_email:
        response = subprocess.run(['/usr/bin/osascript', '-e',
            'text returned of (display dialog "Enter an iCloud app-specific password for lajh87@me.com. It will be saved only in macOS Keychain for DPE update emails." default answer "" with hidden answer buttons {"Cancel", "Save"} default button "Save" with title "DPE email setup")'],
            check=True, text=True, capture_output=True)
        password = response.stdout.strip()
        if not password:
            raise ValueError('No password entered')
        store_password(password)
        del password, response
    if args.setup_email or args.enable:
        subprocess.run([str(python), str(runner), '--test-email'], check=True)
    if args.enable:
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)
        shutil.copy2(staged, PLIST)
        subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(PLIST)], check=True)
        print('Enabled: Friday 09:00 local time. Logs: ' + str(LOGS))
    else:
        print('Installed and staged. Enable after email setup using --setup-email --enable.')


if __name__ == '__main__':
    main()
