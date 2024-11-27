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

launch() {
    APP=$1
    APPNAME=$(basename $APP .dwarfs)
    USERDATA=$HOME/.local/share/dwarf-$APPNAME

    TMPDIR=${XDG_RUNTIME_DIR:-/tmp}
    TEMP=$(mktemp -d -p $TMPDIR -t dwarf-$APPNAME-XXXXXX)
    echo "Mounting on $TEMP"

    cleanup_launch() {
        mountpoint -q $TEMP/mnt/combined && fusermount -u $TEMP/mnt/combined
        mountpoint -q $TEMP/mnt/wine && fusermount -u $TEMP/mnt/wine
        mountpoint -q $TEMP/mnt/app && fusermount -u $TEMP/mnt/app
        rmdir $TEMP/mnt/combined $TEMP/mnt/wine $TEMP/mnt/app
        rm -r $TEMP
    }
    trap cleanup_launch EXIT

    mkdir -p $TEMP/mnt/app $TEMP/mnt/wine $TEMP/mnt/combined $USERDATA/work $USERDATA/data

    dwarfs $0 -o offset=auto,noatime $TEMP/mnt/wine
    dwarfs $APP -o offset=auto,noatime $TEMP/mnt/app
    fuse-overlayfs -o lowerdir=$TEMP/mnt/wine:$TEMP/mnt/app,upperdir=$USERDATA/data,workdir=$USERDATA/work,squash_to_uid=$(id -u),squash_to_gid=$(id -g) $TEMP/mnt/combined

    if [ -n "$2" ]; then
        "${@:2}"
        exit $?
    else
        $TEMP/mnt/combined/entrypoint.sh
        exit $?
    fi
}

if [ -z "$1" ]; then
    echo "Usage: $0 <app.dwarfs>"
    echo "or:    $0 --create <app.dwarfs>"
    exit 1
fi

if [ "$1" == "--create" ]; then
    if [ -z "$2" ]; then
        echo "Usage: $0 --create <app.dwarfs>"
        exit 1
    fi
    APP=$2
    APPNAME=$(basename $APP .dwarfs)

    TEMP=$(mktemp -d -p $PWD -t tmp.dwarf-$APPNAME-XXXXXX)
    echo "Mounting on $TEMP"

    cleanup_create() {
        mountpoint -q $TEMP/mnt/combined && fusermount -u $TEMP/mnt/combined
        mountpoint -q $TEMP/mnt/wine && fusermount -u $TEMP/mnt/wine
        rm -r $TEMP
    }
    trap cleanup_create EXIT

    mkdir -p $TEMP/app $TEMP/mnt/wine $TEMP/mnt/combined $TEMP/work

    dwarfs $0 -o offset=auto,noatime $TEMP/mnt/wine
    fuse-overlayfs -o lowerdir=$TEMP/mnt/wine,upperdir=$TEMP/app,workdir=$TEMP/work,squash_to_uid=$(id -u),squash_to_gid=$(id -g) $TEMP/mnt/combined

    source $TEMP/mnt/combined/env.sh
    echo "Prepared mounts for setting up $APPNAME."
    echo "You are now dropped into a bash shell where you can set up your app using wine."
    echo "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."

    while [ ! -f $TEMP/mnt/combined/entrypoint.sh ]; do
        pushd $TEMP/mnt/combined
        "$SHELL"
        if [ $? != 0 ]; then
            exit 1
        fi
        popd
        echo "Checking for entrypoint.sh to be created in $TEMP/mnt/combined"
    done
    fusermount -u $TEMP/mnt/combined
    fusermount -u $TEMP/mnt/wine

    mkdwarfs -o $APP -i $TEMP/app --set-owner=1000 --set-group=1000
else
    launch "$@"
fi

# Guard against embedded binary data
exit 0
: <<EOFEOFEOFEOFEOFEOF