#!/bin/sh
set -eu
test "$(id -u)" = 0 || { echo 'Run with sudo.' >&2; exit 1; }
test "$(uname -s)" = Darwin || { echo 'macOS required.' >&2; exit 1; }
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required"'
destination=/usr/local/bin/mac-location-lab
if test -e "$destination" || test -L "$destination"; then
  echo 'Destination already exists; uninstall the previous copy first.' >&2
  exit 1
fi
/usr/bin/install -d -m 0755 /usr/local/bin
/usr/bin/install -o root -g wheel -m 0755 "$project_dir/mac_location_lab.py" "$destination"
echo 'Installed mac-location-lab. Location preferences have not been changed.'
