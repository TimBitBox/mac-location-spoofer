# Mac Location Spoofer

Experimental location simulation **on the Mac running the tool**. Set a latitude
and longitude, restart Core Location, and let Mac apps request the simulated fix.
No iPhone, connected device, proxy, account token, or Find My decryption key is needed.

This is a command-line developer experiment, not a supported Apple API or a
guaranteed Find My spoofing product. It uses private macOS preference keys.

Unlike tools that run on macOS to spoof a connected iPhone,
this project changes the simulated Core Location reported by
the same Mac on which the tool is running.

## Requirements and tested scope

- macOS with Python 3.9 or newer available as `python3`.
- An administrator account for `set` and `reset`.
- Location Services enabled and location permission granted to the app used to verify the fix.
- No other location simulator service running concurrently.

The underlying preference method was observed working on an M1 Mac mini (2020) running
macOS 26.6.2 (25G83). A fresh-machine installation and end-to-end set/reset have **not** yet been verified. Neither Apple Silicon versus Intel compatibility nor other macOS versions have been established.

The tool does not modify SIP, AMFI, entitlements, privacy permissions, or boot
arguments. If macOS rejects the method, report that result rather than assuming
security protections must be disabled.

## Quick start

From a downloaded copy or clone of this repository:

```sh
python3 mac_location_lab.py doctor
sudo python3 mac_location_lab.py set --latitude 48.8584 --longitude 2.2945
```

The example is the Eiffel Tower. Open Apple Maps and press the current-location focus button. The location should have successfully been updated to Paris, France.

Return to the previous configuration:

```sh
sudo python3 mac_location_lab.py reset
```

The first `set` saves only the six simulation preferences it may change. Later
`set` commands preserve that original backup. `reset` restores existing values
and deletes keys that were originally absent.

## Optional installation

```sh
sudo sh scripts/install.sh
mac-location-lab doctor
sudo mac-location-lab set --latitude 48.8584 --longitude 2.2945 --accuracy 10
```

The installed command still requires `python3` on the command search path used
by sudo. If sudo cannot find Python, use the source command with an explicit
Python interpreter path instead. No daemon, login item, or passwordless sudo
rule is installed. Preferences might persist after the command exits and across
reboots; use `reset` to end the experiment.

Uninstall from the source directory:

```sh
sudo sh scripts/uninstall.sh
```

Uninstall runs `reset` first and aborts if restoration fails. It removes only
this project's installed command and lock; a surviving original backup is never
discarded to force an uninstall.

## Find My and other apps

```sh
sudo python3 mac_location_lab.py set --latitude 48.8584 --longitude 2.2945 --refresh-findmy
```

This opt-in step requests a normal restart of Apple's device reporter and opens
Find My in the background for the logged-in console user.

Core Location, an app's own location cache, and Apple's cloud device record are
different stages. Applying preferences does not prove a device report was sent
or that an another device's Find My view refreshed. The original experiment observed
successful Find My reports, but this package cannot guarantee propagation,
refresh timing, or agreement across clients. Apps that use IP geolocation or
independent signals may display another location.


## What changes on the Mac

For the `_locationd` account, in the current-host preferences of
`com.apple.locationd` and `com.apple.locationd.notbackedup`:

- `SimulatedLocationLatitude`
- `SimulatedLocationLongitude`
- `SimulatedLocationAccuracy`

The command restarts the root preference cache and `locationd`, then posts
`com.apple.locationd/Prefs`. This affects the system location service and may
affect Maps, weather, automatic timezone selection, and other location consumers.
Run on a test Mac where practical. A failed write/restart attempts to restore
the pre-command values and keeps the original backup for recovery.

Local state is stored under `/var/db/mac-location-lab` with root-only access.
There is no location history, analytics, network client, or credential storage.
Coordinates passed as command-line arguments may be visible in shell history
and process listings. macOS and apps retain their own independent logs/caches;
this tool makes no claim to erase them.

## Troubleshooting

1. Run `doctor`. It reports OS, architecture, SIP status, and whether `locationd` is running without printing coordinates.
2. Verify Location Services and Maps permission in System Settings.
3. Stop any other simulator before testing; multiple writers can overwrite each other.
4. Check Maps' current-location control before checking a remote Find My client.
5. Run `reset` if the simulation is unwanted. If it fails, retain the private backup and report the error without uploading it.

Changing macOS private preferences is version-dependent. Please include the OS
build, architecture, command name, and sanitized error in an issue. Do not attach
credentials, device identifiers, location databases, or private state files.

## Development and release

```sh
python3 -m unittest discover -s tests -v
python3 mac_location_lab.py --help
```

## License

MIT. Not affiliated with or endorsed by Apple. Use only on devices you control
or administer with permission.

## Warning

Do not post encryption keys, tokens, account identifiers, real personal coordinates, state
backups, or Find My databases in issues.
