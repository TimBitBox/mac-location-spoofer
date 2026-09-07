#!/bin/sh
set -eu
test "$(id -u)" = 0 || { echo 'Run with sudo.' >&2; exit 1; }
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 "$project_dir/mac_location_lab.py" reset
destination=/usr/local/bin/mac-location-lab
if test -e "$destination" || test -L "$destination"; then
  if ! cmp -s "$project_dir/mac_location_lab.py" "$destination"; then
    echo 'Installed command differs from this source; use its matching uninstaller.' >&2
    exit 1
  fi
  /bin/rm "$destination"
fi
if test -f /var/db/mac-location-lab/lock; then
  /bin/rm /var/db/mac-location-lab/lock
fi
/bin/rmdir /var/db/mac-location-lab 2>/dev/null || true
echo 'Uninstalled. Original simulation preferences were restored if a session existed.'
