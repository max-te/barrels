#!/usr/bin/env bash

set -e

cd "$APPDIR"
export PATH="$APPDIR/bin:$PATH"

APPNAME=$(basename -s.desktop ./*.desktop)
APP="$APPNAME.dwarfs"
USERDATA=$HOME/.local/share/dwarf-"$APPNAME"

if [ ! -f "$APP" ]; then
    echo "Error: $APP does not exist"
    exit 1
fi

TMPDIR=${XDG_RUNTIME_DIR:-/tmp}
TEMP=$(mktemp -d -p "$TMPDIR" -t dwarf-"$APPNAME"-XXXXXX)
echo "Mounting on $TEMP"

# shellcheck disable=SC2317,SC2329
cleanup_launch() {
    mountpoint -q "$TEMP/mnt/combined" && fusermount -u "$TEMP/mnt/combined"
    mountpoint -q "$TEMP/mnt/wine" && fusermount -u "$TEMP/mnt/wine"
    mountpoint -q "$TEMP/mnt/app" && fusermount -u "$TEMP/mnt/app"
    rmdir "$TEMP/mnt/combined" "$TEMP/mnt/wine" "$TEMP/mnt/app"
    rm -r "$TEMP"
}
trap cleanup_launch EXIT

mkdir -p "$TEMP/mnt/app" "$TEMP/mnt/wine" "$TEMP/mnt/combined" "$USERDATA/work" "$USERDATA/data"

dwarfs "wine.dwarfs" -o offset=auto,noatime "$TEMP/mnt/wine"
dwarfs "$APP" -o offset=auto,noatime "$TEMP/mnt/app"
fuse-overlayfs -o "lowerdir=$TEMP/mnt/wine:$TEMP/mnt/app,upperdir=$USERDATA/data,workdir=$USERDATA/work,squash_to_uid=$(id -u),squash_to_gid=$(id -g)" "$TEMP/mnt/combined"

# shellcheck disable=SC1091
source "$TEMP/mnt/combined/env.sh"
if [[ -n "$2" && "$2" != "--" ]]; then
    "${@:2}"
    exit $?
else
    "$TEMP/mnt/combined/entrypoint.sh" "${@:3}"
    exit $?
fi
