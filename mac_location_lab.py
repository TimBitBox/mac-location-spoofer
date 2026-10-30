#!/usr/bin/env python3
"""Experimental, same-device macOS Core Location preference controller."""
import argparse
import json
import math
import os
import platform
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DOMAINS = ('com.apple.locationd', 'com.apple.locationd.notbackedup')
KEYS = ('SimulatedLocationLatitude', 'SimulatedLocationLongitude', 'SimulatedLocationAccuracy')
STATE = Path('/var/db/mac-location-lab')


def run(args, check=True, action=None):
    try:
        return subprocess.run(args, check=check, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        if action is None:
            raise
        if isinstance(error, subprocess.CalledProcessError):
            detail = f'exit {error.returncode}'
            if 'operation not permitted' in (error.stderr or '').lower():
                detail += '; operation not permitted'
        elif isinstance(error, subprocess.TimeoutExpired):
            detail = 'timed out'
        elif isinstance(error, OSError):
            detail = f'OS error {error.errno}'
        else:
            detail = 'system command failed'
        # Use controlled descriptions; command arguments/stderr can contain coordinates.
        raise RuntimeError(f'{action} failed ({detail}).') from None


def preference(domain, operation, key, value=None):
    uid = run(['/usr/bin/id', '-u', '_locationd']).stdout.strip()
    prefix = ['/bin/launchctl', 'asuser', uid, '/usr/bin/sudo', '-H', '-u', '_locationd',
              '/usr/bin/defaults', '-currentHost']
    args = prefix + [operation, domain, key]
    if value is not None:
        args += ['-float', str(value)]
    result = run(args, check=False)
    if result.returncode:
        # A failed deletion is harmless only when a read confirms the key is absent.
        if operation == 'delete' and preference(domain, 'read', key) is None:
            return None
        error = result.stderr.strip().lower()
        missing_key = f"error: could not find key '{key}' in domain '{domain}'.".lower()
        if operation == 'read' and (
                'does not exist' in error or error == missing_key):
            return None
        detail = f'exit {result.returncode}'
        for reason in ('operation not permitted', 'permission denied'):
            if reason in error:
                detail += '; ' + reason
                break
        raise RuntimeError(f'Core Location preference {operation} failed for {domain}/{key} ({detail}); '
                           'no success is assumed.')
    if operation == 'read':
        # defaults read formats float32 values for display and can lose significant digits.
        # Its plist export retains the numeric value needed for backup and verification.
        exported = run(prefix + ['export', domain, '-'],
                       action=f'Reading full-precision preference {domain}/{key}')
        try:
            data = plistlib.loads(exported.stdout.encode('utf-8'))
        except Exception:
            raise RuntimeError('Full-precision preference export is not a valid property list.') from None
        if not isinstance(data, dict) or key not in data:
            raise RuntimeError(f'Full-precision preference export is missing {domain}/{key}.')
        value = data[key]
        if type(value) not in (int, float):
            raise RuntimeError('Existing simulation preference is invalid.')
        try:
            number = float(value)
        except OverflowError:
            raise RuntimeError('Existing simulation preference is invalid.') from None
        if not math.isfinite(number):
            raise RuntimeError('Existing simulation preference is invalid.')
        return number


def validate_location(latitude, longitude, accuracy):
    for value, low, high in ((latitude, -90, 90), (longitude, -180, 180), (accuracy, 1, 100000)):
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Invalid latitude, longitude, or accuracy range.')
    return (latitude, longitude, accuracy)


def private_directory():
    STATE.mkdir(mode=0o700, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('State directory must be a root-owned directory with mode 0700.')


def snapshot():
    return {domain: {key: preference(domain, 'read', key) for key in KEYS} for domain in DOMAINS}


def verify_preferences(expected):
    current = snapshot()
    for domain, values in expected.items():
        for key, value in values.items():
            actual = current[domain][key]
            # Accept normal floating-point rounding from defaults' numeric storage.
            matches = actual is None if value is None else (
                actual is not None and math.isclose(actual, value, rel_tol=1e-7, abs_tol=1e-7))
            if not matches:
                raise RuntimeError(f'Preference read-back verification failed for {domain}/{key}; '
                                   'original backup retained. Use reset --defer-refresh for recovery.')


def save_backup(data):
    descriptor, temporary = tempfile.mkstemp(prefix='.original-', suffix='.json', dir=STATE)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        # Publish only the complete backup, without replacing an existing original.
        os.link(temporary, STATE / 'original.json')
    finally:
        os.unlink(temporary)


def read_backup():
    descriptor = os.open(STATE / 'original.json', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        info = os.fstat(source.fileno())
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 8192:
            raise RuntimeError('Backup ownership, permissions, or size is invalid.')
        data = json.load(source)
    if set(data) != set(DOMAINS):
        raise RuntimeError('Invalid backup domains.')
    for values in data.values():
        if set(values) != set(KEYS):
            raise RuntimeError('Invalid backup keys.')
        for value in values.values():
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise RuntimeError('Invalid backup value.')
    return data


def restore(data):
    for domain, values in data.items():
        for key, value in values.items():
            preference(domain, 'delete' if value is None else 'write', key, value)


def restart_location():
    # System cfprefsd otherwise retains the old current-host preferences.
    run(['/usr/bin/killall', '-u', 'root', 'cfprefsd'], check=False,
        action='Refreshing preference cache')
    time.sleep(2)
    old = run(['/usr/bin/pgrep', '-x', 'locationd'],
              action='Querying Core Location before restart').stdout.split()
    run(['/bin/launchctl', 'kill', 'SIGTERM', 'system/com.apple.locationd'],
        action='Requesting Core Location restart')
    for _ in range(50):
        current = run(['/usr/bin/pgrep', '-x', 'locationd'], check=False,
                      action='Querying Core Location after restart').stdout.split()
        if current and not set(old).intersection(current):
            run(['/usr/bin/notifyutil', '-p', 'com.apple.locationd/Prefs'],
                action='Posting Core Location preferences notification')
            return
        time.sleep(.1)
    raise RuntimeError('Core Location did not restart in time; reset remains available.')


def refresh_location(notify_only, defer_refresh=False):
    if defer_refresh:
        return
    if notify_only:
        run(['/usr/bin/notifyutil', '-p', 'com.apple.locationd/Prefs'],
            action='Posting Core Location preferences notification')
    else:
        restart_location()


def findmy_user():
    uid = os.environ.get('SUDO_UID', '')
    if not uid.isdecimal() or int(uid) < 501:
        raise RuntimeError('Find My refresh requires sudo from a logged-in desktop user.')
    console = run(['/usr/bin/stat', '-f', '%u', '/dev/console']).stdout.strip()
    if uid != console:
        raise RuntimeError('Find My refresh requires the current console user.')
    return uid


def refresh_findmy():
    uid = findmy_user()
    # Request a normal reporter restart. Do not force-kill an Apple service.
    run(['/bin/launchctl', 'kill', 'SIGTERM', 'system/com.apple.icloud.findmydeviced'], check=False)
    run(['/usr/bin/notifyutil', '-p', 'com.apple.locationd/Prefs'])
    run(['/bin/launchctl', 'asuser', uid, '/usr/bin/sudo', '-H', '-u', '#' + uid,
         '/usr/bin/open', '-g', '-b', 'com.apple.findmy'])
    print('Find My opened in the background. Select this Mac in Devices if the cloud pin is stale.')


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest='command', required=True)
    setter = sub.add_parser('set', help='apply a manual location to this Mac')
    setter.add_argument('--latitude', type=float, required=True)
    setter.add_argument('--longitude', type=float, required=True)
    setter.add_argument('--accuracy', type=float, default=10)
    setter.add_argument('--refresh-findmy', action='store_true')
    resetter = sub.add_parser('reset', help='restore preferences saved before the first set')
    for command in (setter, resetter):
        refresh_mode = command.add_mutually_exclusive_group()
        refresh_mode.add_argument('--notify-only', action='store_true',
                                  help='post a preference notification without restarting services; '
                                       'actual location refresh is experimental')
        refresh_mode.add_argument('--defer-refresh', action='store_true',
                                  help='save preferences for a later manual restart of macOS; '
                                       'do not restart services or post notifications')
    sub.add_parser('doctor', help='show compatibility and service checks without coordinates')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if platform.system() != 'Darwin':
        raise RuntimeError('This tool requires macOS.')
    if args.command == 'doctor':
        print('macOS:', platform.mac_ver()[0], 'architecture:', platform.machine())
        print(run(['/usr/bin/csrutil', 'status'], check=False).stdout.strip())
        process = run(['/usr/bin/pgrep', '-x', 'locationd'], check=False)
        status = {0: 'True', 1: 'False'}.get(process.returncode, 'unknown (process query failed)')
        print('Core Location running:', status)
        print('Private preference support is experimental; these checks do not prove spoofing works.')
        return
    if os.geteuid() != 0:
        raise RuntimeError('Run set/reset through sudo; no password is stored by this tool.')
    values = validate_location(args.latitude, args.longitude, args.accuracy) if args.command == 'set' else None
    if args.command == 'set' and args.refresh_findmy and (args.notify_only or args.defer_refresh):
        mode = '--defer-refresh' if args.defer_refresh else '--notify-only'
        raise ValueError(f'{mode} cannot be combined with --refresh-findmy, '
                         'which requests a service restart.')
    if args.command == 'set' and args.refresh_findmy:
        findmy_user()
    private_directory()
    import fcntl
    descriptor = os.open(STATE / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = STATE / 'original.json'
        if args.command == 'reset':
            if not backup.exists():
                print('No saved session to reset; preferences left unchanged.')
                return
            original = read_backup()
            restore(original)
            if args.defer_refresh:
                verify_preferences(original)
            try:
                refresh_location(args.notify_only, defer_refresh=args.defer_refresh)
            except Exception:
                print('Original simulation preferences restored, but Core Location refresh failed. '
                      'Original backup retained; run reset to retry recovery.', file=sys.stderr)
                raise
            backup.unlink()
            if args.defer_refresh:
                print('Original stored preferences restored and read back. If simulation was active, '
                      'manually restart macOS to complete recovery.')
            elif args.notify_only:
                print('Original simulation preferences restored; preference notification posted. '
                      'Verify the current location in Maps.')
            else:
                print('Original simulation preferences restored. Apps may need to refresh their location.')
            return
        before = snapshot()
        if not backup.exists():
            save_backup(before)
        else:
            read_backup()
        try:
            for domain in DOMAINS:
                for key, value in zip(KEYS, values):
                    preference(domain, 'write', key, value)
            if args.defer_refresh:
                verify_preferences({domain: dict(zip(KEYS, values)) for domain in DOMAINS})
            refresh_location(args.notify_only, defer_refresh=args.defer_refresh)
        except Exception:
            try:
                restore(before)
                if args.defer_refresh:
                    verify_preferences(before)
            except Exception:
                recovery_command = 'reset --defer-refresh' if args.defer_refresh else 'reset'
                print(f'Automatic rollback failed while restoring preferences; run {recovery_command}. '
                      'Original backup retained.', file=sys.stderr)
            else:
                try:
                    refresh_location(args.notify_only, defer_refresh=args.defer_refresh)
                except Exception:
                    print('Pre-command preferences restored, but Core Location refresh failed. '
                          'Original backup retained; run reset to retry recovery.', file=sys.stderr)
            raise
        if args.defer_refresh:
            print('Preferences saved and read back; activation pending a manual restart of macOS. '
                  'Actual location is unverified.')
        elif args.notify_only:
            print('Simulation preferences saved; preference notification posted. Verify the fix in Maps.')
        else:
            print('Simulation preferences applied and Core Location restarted. Verify the fix in Maps.')
        if args.refresh_findmy:
            try:
                refresh_findmy()
            except (RuntimeError, OSError, subprocess.SubprocessError):
                print('Find My refresh failed; simulation remains active. '
                      'Use reset to restore the original preferences.', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        # Never print subprocess arguments: they can contain coordinates.
        print(str(error) if isinstance(error, (RuntimeError, ValueError)) else
              'System operation failed. Check permissions and use reset if needed.', file=sys.stderr)
        sys.exit(1)
