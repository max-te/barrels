#!/usr/bin/env bash
set -e

if ! command -v dwarfs > /dev/null; then
    echo "dwarfs not found"
    echo "Find it at https://github.com/mhx/dwarfs"
    exit 1
fi

if ! command -v fuse-overlayfs > /dev/null; then
    echo "fuse-overlayfs not found"
    exit 1
fi

try_unmount() {
    mountpoint -q "$1" || return
    echo "Unmounting $1"
    while ! fusermount -u "$1"; do
        mountpoint -q "$1" || return
        sleep 3
        echo "Try agin"
    done
}

launch() {
    APP="$1"
    APPNAME=$(basename -s.dwarfs "$APP")
    USERDATA=$HOME/.local/share/dwarf-"$APPNAME"

    TMPDIR=${XDG_RUNTIME_DIR:-/tmp}
    TEMP=$(mktemp -d -p "$TMPDIR" -t dwarf-"$APPNAME"-XXXXXX)
    echo "Mounting on $TEMP"

    # shellcheck disable=SC2317,SC2329
    cleanup_launch() {
        try_unmount "$TEMP/mnt/combined"
        try_unmount "$TEMP/mnt/wine"
        try_unmount "$TEMP/mnt/app"
        rmdir "$TEMP/mnt/combined" "$TEMP/mnt/wine" "$TEMP/mnt/app"
        rm -r "$TEMP"
    }
    trap cleanup_launch EXIT

    mkdir -p "$TEMP/mnt/app" "$TEMP/mnt/wine" "$TEMP/mnt/combined" "$USERDATA/work" "$USERDATA/data"

    dwarfs "$0" -o offset=auto,noatime "$TEMP/mnt/wine"
    dwarfs "$APP" -o offset=auto,noatime "$TEMP/mnt/app"
    fuse-overlayfs -o "lowerdir=$TEMP/mnt/app:$TEMP/mnt/wine,upperdir=$USERDATA/data,workdir=$USERDATA/work,squash_to_uid=$(id -u),squash_to_gid=$(id -g)" "$TEMP/mnt/combined"

    # shellcheck disable=SC1091
    source "$TEMP/mnt/combined/env.sh"
    if [[ -n "$2" && "$2" != "--" ]]; then
        "${@:2}"
        exit $?
    else
        "$TEMP/mnt/combined/entrypoint.sh" "${@:3}"
        exit $?
    fi
}

printhelp() {
    echo "Usage: $0 <app.dwarfs>"
    echo "or:    $0 --create <app.dwarfs>"
    echo "or:    $0 --edit <app.dwarfs>"
    exit 1
}

if [ -z "$1" ]; then
    printhelp
fi

if [ "$1" == "--edit" ]; then
    if [ -z "$2" ] || [[ "$2" == -* ]]; then
        printhelp
    fi
    APP=$2
    APPNAME=$(basename -s.dwarfs "$APP" )

    if [ ! -f "$APP" ]; then
        echo "Error: $APP does not exist"
        exit 1
    fi

    TEMP=$(mktemp -d -p "$PWD" -t tmp.dwarf-"$APPNAME"-XXXXXX)
    echo "Mounting on $TEMP"

    # shellcheck disable=SC2317,SC2329
    cleanup_edit() {
        try_unmount "$TEMP/mnt/combined"
        try_unmount "$TEMP/mnt/final"
        try_unmount "$TEMP/mnt/wine"
        try_unmount "$TEMP/mnt/app"
        rm -r "$TEMP"
    }
    trap cleanup_edit EXIT

    mkdir -p "$TEMP/edit" "$TEMP/final-work" "$TEMP/mnt/"{wine,app,combined,final} "$TEMP/work"

    dwarfs "$0" -o offset=auto,noatime "$TEMP/mnt/wine"
    dwarfs "$APP" -o offset=auto,noatime "$TEMP/mnt/app"
    fuse-overlayfs -o "lowerdir=$TEMP/mnt/app:$TEMP/mnt/wine,upperdir=$TEMP/edit,workdir=$TEMP/work,squash_to_uid=$(id -u),squash_to_gid=$(id -g)" "$TEMP/mnt/combined"

    # shellcheck disable=SC1091
    source "$TEMP/mnt/combined/env.sh"
    echo "Prepared mounts for editing $APPNAME."
    echo "You are now dropped into a bash shell where you can modify your app using wine."
    echo "The existing app image is mounted read-only, and your changes will be saved to a new image."
    echo "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."

    if ! (env --chdir="$TEMP/mnt/combined" PS1="[$APPNAME] \s-\v$ " /bin/bash --norc -i); then
        exit 1
    fi

    try_unmount "$TEMP/mnt/combined"
    echo "Creating new image with your changes..."

    # Mount a new overlay combining the app image and edits
    fuse-overlayfs -o "lowerdir=$TEMP/mnt/app,upperdir=$TEMP/edit,workdir=$TEMP/final-work" "$TEMP/mnt/final"

    # Create new image at temporary location
    mkdwarfs -o "${APP}.new" -i "$TEMP/mnt/final" --set-owner=1000 --set-group=1000

    # Clean up mounts
    try_unmount "$TEMP/mnt/final"
    try_unmount "$TEMP/mnt/wine"
    try_unmount "$TEMP/mnt/app"
    rm -r "$TEMP"

    # Move files into place only after everything is unmounted
    mv "$APP" "${APP}.backup"
    mv "${APP}.new" "$APP"

    echo "New image created successfully. Original image backed up as ${APP}.backup"
elif [ "$1" == "--create" ]; then
    if [ -z "$2" ] || [[ "$2" == -* ]]; then
        printhelp
    fi
    APP=$2
    APPNAME=$(basename -s.dwarfs "$APP")

    TEMP=$(mktemp -d -p "$PWD" -t tmp.dwarf-"$APPNAME"-XXXXXX)
    echo "Mounting on $TEMP"

    # shellcheck disable=SC2317,SC2329
    cleanup_create() {
        try_unmount "$TEMP/mnt/combined"
        try_unmount "$TEMP/mnt/wine"
        rm -r "$TEMP"
    }
    trap cleanup_create EXIT

    mkdir -p "$TEMP/app" "$TEMP/mnt/wine" "$TEMP/mnt/combined" "$TEMP/work"

    dwarfs "$0" -o offset=auto,noatime "$TEMP/mnt/wine"
    fuse-overlayfs -o "lowerdir=$TEMP/mnt/wine,upperdir=$TEMP/app,workdir=$TEMP/work,squash_to_uid=$(id -u),squash_to_gid=$(id -g)" "$TEMP/mnt/combined"

    # shellcheck disable=SC1091
    source "$TEMP/mnt/combined/env.sh"
    echo "Prepared mounts for setting up $APPNAME."
    echo "You are now dropped into a bash shell where you can set up your app using wine."
    echo "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."

    while [ ! -f "$TEMP/mnt/combined/entrypoint.sh" ]; do
        if ! (env --chdir="$TEMP/mnt/combined" PS1="[$APPNAME] \s-\v$ " /bin/bash --norc -i); then
            exit 1
        fi
        echo "Checking for entrypoint.sh to be created in $TEMP/mnt/combined"
    done
    try_unmount "$TEMP/mnt/combined"
    try_unmount "$TEMP/mnt/wine"

    mkdwarfs -o "$APP" -i "$TEMP/app" --set-owner=1000 --set-group=1000
    rm -r "$TEMP"
else
    launch "$@"
fi

# Guard against embedded binary data
exit 0
