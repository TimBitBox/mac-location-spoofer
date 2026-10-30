# Mac Location Spoofer

This project experiments with simulated Core Location **on the Mac running it**,
using private preferences. It does not spoof a connected iPhone or iPad.
Writing them successfully does not prove that an app's reported location changed.
No iPhone, connected device, proxy, account token, or Find My decryption key is needed.

This is a command-line developer experiment, not a supported Apple API or a
guaranteed Find My spoofing product. It uses private macOS preference keys.

## Requirements and tested scope

- macOS with Python 3.9 or newer available as `python3`.
- An administrator account for `set` and `reset`.
- Location Services enabled and location permission granted to the app used to verify the fix.
- No other location simulator service running concurrently.

The original preference method was observed working on an M1 Mac mini (2020)
running macOS 26.6.2 (25G83).

In a local macOS 27.0.1 arm64 test with SIP enabled, the normal `locationd`
restart request failed. Notification-only writes and restoration completed, but
Maps did not move to the requested location. After `set --defer-refresh` and a
normal manual restart of macOS, the user confirmed the requested location in Maps.
Static inspection also found coordinate preference reads in the simulation
controller's initialization path.

This confirms activation on that Mac. Restoration after a real reboot, a
fresh-machine installation, Intel compatibility, and Find My cloud propagation
remain unverified.

The tool does not modify SIP, AMFI, entitlements, privacy permissions, or boot
arguments. If macOS rejects the method, report that result rather than assuming
security protections must be disabled.

## Quick start

From a downloaded copy or clone of this repository:

```sh
python3 mac_location_lab.py doctor
sudo python3 mac_location_lab.py set --latitude 48.8584 --longitude 2.2945 --defer-refresh
```

The example is the Eiffel Tower. This command saves the preferences and reads all
six keys back for verification. It leaves activation pending; it does not restart
services, send notifications, or reboot the Mac.

When convenient, use the Apple menu to restart macOS normally. Then open Maps
and press the current-location focus button. Check whether the fix moved to
Paris; a successful command or readback is not proof that this worked.

Return to the previous configuration:

```sh
sudo python3 mac_location_lab.py reset --defer-refresh
```

The first `set` saves only the six simulation preferences it may change. Later
`set` commands preserve that original backup. `reset` restores existing values
and deletes keys that were originally absent.
Deferred reset also reads all six keys back and removes the backup only after
the stored values match the original. If simulation was active, restart macOS
normally again to refresh the running service and verify the real location in Maps.
The backup is written to a private temporary file and published only once complete,
without replacing an existing original backup. A failed backup write leaves the
location preferences unchanged and allows a later retry.

## Deferred refresh (experimental)

`--defer-refresh` prepares preferences for a later, manually controlled restart
of macOS. It avoids the service-control request that failed in the local macOS 27
test. Activation after a manual restart was confirmed in the local Maps test
described above. Verify activation on each target machine.

The deferred mode verifies the requested values after writing, the original values
after reset, and the pre-command values after automatic rollback. Numeric checks
use full-precision values selected from native plist export, because
`defaults read` can round a float32 value for display. Only the six simulation
preferences are retained in the backup. Comparisons allow normal floating-point
storage rounding. A failed verification reports the domain and key without
printing coordinates and retains the original backup.
The recovery command for this mode is `reset --defer-refresh`.

Set, reset, and rollback in this mode do not terminate services, post preference
notifications, open apps, install background jobs, or restart the computer.
`--defer-refresh` is mutually exclusive with `--notify-only` and cannot be combined
with `--refresh-findmy`.

Run reset before the validation reboot only if you want to cancel the prepared
simulation. When ending an active simulation, restoring the preferences updates
the stored configuration; the running location service may keep its cached
simulation until the next normal restart of macOS.

## Persistence and startup

The script exits after saving preferences; the system's `locationd` provides the
active simulated location. For a fixed location, leave the saved configuration in
place. No terminal session, Python background process, login item, or startup job
is needed. The local activation test confirmed that the preferences survived a
normal restart; private preference behavior may change across macOS versions.

Keep the original backup under `/var/db/mac-location-lab` for recovery. To change
coordinates, run `set --defer-refresh` again and restart macOS normally. To end
the simulation, use `reset --defer-refresh`, then restart and verify the current
location in Maps.

## Notification-only mode (experimental)

`set` and `reset` also accept `--notify-only`, retained for experiments. It did not
activate the requested location in the local macOS 27.0.1 test; use the deferred
workflow for activation after a manual restart.

```sh
sudo python3 mac_location_lab.py set --latitude 48.8584 --longitude 2.2945 --notify-only
sudo python3 mac_location_lab.py reset --notify-only
```

This mode writes or restores the same preferences and posts
`com.apple.locationd/Prefs`. It does not terminate `cfprefsd` or `locationd`,
including during automatic rollback. It uses the same original backup and lock.

A successful notification post does not prove that Core Location reloaded the
preferences. Use `reset --notify-only` to restore them even if Maps did not move;
saved preferences may persist across reboots. A successful reset removes the
backup after restoring the preferences and posting the notification.

`--notify-only` cannot be combined with `--refresh-findmy`, which requests a
reporter restart. End a notification-only session with `reset --notify-only`
before using the uninstaller.

## Optional installation

```sh
sudo sh scripts/install.sh
mac-location-lab doctor
sudo mac-location-lab set --latitude 48.8584 --longitude 2.2945 --accuracy 10 --defer-refresh
```

The installed command still requires `python3` on the command search path used
by sudo. If sudo cannot find Python, use the source command with an explicit
Python interpreter path instead. No daemon, login item, or passwordless sudo
rule is installed. Installation only adds the command to `/usr/local/bin`;
it is optional for the source workflow above.

Uninstall from the source directory:

```sh
sudo sh scripts/uninstall.sh
```

For a deferred session, run `reset --defer-refresh` and complete any required manual
restart before uninstalling. Uninstall runs the default `reset` first and aborts
if restoration fails. It removes only this project's installed command and lock;
a surviving original backup is never discarded to force an uninstall.

## Find My and other apps

```sh
sudo python3 mac_location_lab.py set --latitude 48.8584 --longitude 2.2945 --refresh-findmy
```

This opt-in step requests a normal restart of Apple's device reporter and opens
Find My in the background for the logged-in console user.
The console user is checked before changing any location preferences. If the
location change succeeds but the subsequent Find My refresh fails, the command
still succeeds and prints a warning that the simulation remains active. The
original backup is retained; use `reset` to restore it.

Core Location, an app's own location cache, and Apple's cloud device record are
different stages. Applying preferences does not prove a device report was sent
or that another device's Find My view refreshed. The original experiment observed
successful Find My reports, but this package cannot guarantee propagation,
refresh timing, or agreement across clients. Apps that use IP geolocation or
independent signals may display another location.

## What changes on the Mac

For the `_locationd` account, in the current-host preferences of
`com.apple.locationd` and `com.apple.locationd.notbackedup`:

- `SimulatedLocationLatitude`
- `SimulatedLocationLongitude`
- `SimulatedLocationAccuracy`

By default, the command restarts the root preference cache and `locationd`, then posts
`com.apple.locationd/Prefs`. This affects the system location service and may
affect Maps, weather, automatic timezone selection, and other location consumers.
With `--notify-only`, it posts the notification without restarting either service.
With `--defer-refresh`, it verifies the stored preferences and leaves service
refresh to a later manual restart of macOS; no service-control or notification
command runs.
Run on a test Mac where practical. A failed write/refresh attempts to restore
the pre-command values and keeps the original backup for recovery.

Local state is stored under `/var/db/mac-location-lab` with root-only access.
There is no location history, analytics, network client, or credential storage.
Coordinates passed as command-line arguments may be visible in shell history
and process listings. macOS and apps retain their own independent logs/caches;
this tool makes no claim to erase them.

## Troubleshooting

1. Run `doctor`. It reports OS, architecture, SIP status, and whether `locationd` is running without printing coordinates. If the process query fails, the service status is reported as unknown.
2. Verify Location Services and Maps permission in System Settings.
3. Stop any other simulator before testing; multiple writers can overwrite each other.
4. Check Maps' current-location control before checking a remote Find My client.
5. Use `reset --defer-refresh` to restore and verify stored preferences without controlling services. If simulation was active, complete recovery with a normal manual restart. If restoration fails, retain the private backup and report the error without uploading it.

Changing macOS private preferences is version-dependent. Please include the OS
build, architecture, command name, and sanitized error in an issue. Do not attach
credentials, device identifiers, location databases, or private state files.

## Development and release

```sh
python3 -m unittest discover -s tests -v
python3 mac_location_lab.py --help
```

The CLI recovery tests replace all system commands with simulated responses and
write state only to temporary directories. They do not change live location
preferences, control real services, or install background jobs.

## License

MIT. Not affiliated with or endorsed by Apple. Use only on devices you control
or administer with permission.
