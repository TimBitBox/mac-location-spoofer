#!/usr/bin/env python3
"""Experimental, same-device macOS Core Location preference controller."""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import time

DOMAINS = ('com.apple.locationd', 'com.apple.locationd.notbackedup')
KEYS = ('SimulatedLocationLatitude', 'SimulatedLocationLongitude', 'SimulatedLocationAccuracy')
STATE = Path('/var/db/mac-location-lab')


def run(args, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=20)


def preference(domain, operation, key, value=None):
    uid = run(['/usr/bin/id', '-u', '_locationd']).stdout.strip()
    args = ['/bin/launchctl', 'asuser', uid, '/usr/bin/sudo', '-H', '-u', '_locationd',
            '/usr/bin/defaults', '-currentHost', operation, domain, key]
    if value is not None:
        args += ['-float', str(value)]
    result = run(args, check=False)
    if result.returncode:
        if operation in ('read', 'delete') and 'does not exist' in result.stderr.lower():
            return None
        raise RuntimeError('Core Location preference access failed; no success is assumed.')
    if operation == 'read':
        number = float(result.stdout.strip())
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


def save_backup(data):
    descriptor = os.open(STATE / 'original.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(data, output)
        output.flush()
        os.fsync(output.fileno())


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
    run(['/usr/bin/killall', '-u', 'root', 'cfprefsd'], check=False)
    time.sleep(2)
    old = run(['/usr/bin/pgrep', '-x', 'locationd']).stdout.split()
    run(['/bin/launchctl', 'kill', 'SIGTERM', 'system/com.apple.locationd'])
    for _ in range(50):
        current = run(['/usr/bin/pgrep', '-x', 'locationd'], check=False).stdout.split()
        if current and not set(old).intersection(current):
            run(['/usr/bin/notifyutil', '-p', 'com.apple.locationd/Prefs'])
            return
        time.sleep(.1)
    raise RuntimeError('Core Location did not restart in time; reset remains available.')


def refresh_findmy():
    uid = os.environ.get('SUDO_UID', '')
    if not uid.isdecimal() or int(uid) < 501:
        raise RuntimeError('Find My refresh requires sudo from a logged-in desktop user.')
    console = run(['/usr/bin/stat', '-f', '%u', '/dev/console']).stdout.strip()
    if uid != console:
        raise RuntimeError('Find My refresh requires the current console user.')
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
    sub.add_parser('reset', help='restore preferences saved before the first set')
    sub.add_parser('doctor', help='show compatibility and service checks without coordinates')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if platform.system() != 'Darwin':
        raise RuntimeError('This tool requires macOS.')
    if args.command == 'doctor':
        print('macOS:', platform.mac_ver()[0], 'architecture:', platform.machine())
        print(run(['/usr/bin/csrutil', 'status'], check=False).stdout.strip())
        print('Core Location running:', run(['/usr/bin/pgrep', '-x', 'locationd'], check=False).returncode == 0)
        print('Private preference support is experimental; these checks do not prove spoofing works.')
        return
    if os.geteuid() != 0:
        raise RuntimeError('Run set/reset through sudo; no password is stored by this tool.')
    values = validate_location(args.latitude, args.longitude, args.accuracy) if args.command == 'set' else None
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
            restore(read_backup())
            restart_location()
            backup.unlink()
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
            restart_location()
        except Exception:
            try:
                restore(before)
                restart_location()
            except Exception:
                print('Automatic rollback failed; run reset. Original backup retained.', file=sys.stderr)
            raise
        print('Simulation preferences applied and Core Location restarted. Verify the fix in Maps.')
        if args.refresh_findmy:
            refresh_findmy()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        # Never print subprocess arguments: they can contain coordinates.
        print(str(error) if isinstance(error, (RuntimeError, ValueError)) else
              'System operation failed. Check permissions and use reset if needed.', file=sys.stderr)
        sys.exit(1)
