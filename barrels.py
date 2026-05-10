import argparse
import json
import logging
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from posix import major, minor
from stat import S_IFCHR, S_IFMT

logging.basicConfig()
logger = logging.getLogger("barrels")
logger.setLevel(logging.INFO)

try:
    c_bold = subprocess.check_output(["tput", "bold"]).decode()
    c_red = subprocess.check_output(["tput", "setaf", "1"]).decode()
    c_reset = subprocess.check_output(["tput", "sgr0"]).decode()
except subprocess.CalledProcessError:
    c_bold = ""
    c_red = ""
    c_reset = ""


def die(msg: str, code: int = 1):
    logger.fatal(msg, stack_info=True, stacklevel=2)
    print(c_red + c_bold + "FATAL: " + c_reset + msg, file=sys.stderr)
    sys.exit(code)


_dwarfs = shutil.which("dwarfs") or die(
    "`dwarfs` not found. Get it from https://github.com/mhx/dwarfs"
)
_mkdwarfs = shutil.which("mkdwarfs") or die(
    "`mkdwarfs` not found. Get it from https://github.com/mhx/dwarfs"
)
_overlayfs = shutil.which("fuse-overlayfs") or die("`fuse-overlayfs` not found")
_mountpoint = shutil.which("mountpoint") or die("command `mountpoint` not found")
_fusermount = shutil.which("fusermount") or die("command `fusermount` not found")


def create_minimal_png(filepath: Path):
    # Create a simple 1x1 transparent PNG (smallest valid PNG)
    # This is the minimal PNG structure that will satisfy appimagetool
    png_data = bytes(
        [
            0x89,
            0x50,
            0x4E,
            0x47,
            0x0D,
            0x0A,
            0x1A,
            0x0A,  # PNG signature
            0x00,
            0x00,
            0x00,
            0x0D,  # IHDR chunk size
            0x49,
            0x48,
            0x44,
            0x52,  # IHDR
            0x00,
            0x00,
            0x00,
            0x01,  # width: 1
            0x00,
            0x00,
            0x00,
            0x01,  # height: 1
            0x08,
            0x06,  # bit depth: 8, color type: 6 (RGBA)
            0x00,
            0x00,
            0x00,  # compression, filter, interlace
            0x1F,
            0x15,
            0xC4,
            0x89,  # CRC
            0x00,
            0x00,
            0x00,
            0x0A,  # IDAT chunk size
            0x49,
            0x44,
            0x41,
            0x54,  # IDAT
            0x78,
            0x9C,
            0x62,
            0x00,
            0x00,
            0x00,
            0x02,
            0x00,
            0x01,  # compressed data
            0xE5,
            0x27,
            0xDE,
            0xFC,  # CRC
            0x00,
            0x00,
            0x00,
            0x00,  # IEND chunk size
            0x49,
            0x45,
            0x4E,
            0x44,  # IEND
            0xAE,
            0x42,
            0x60,
            0x82,  # CRC
        ]
    )
    _ = filepath.write_bytes(png_data)
    logger.debug(f"Created minimal PNG placeholder at {filepath}")


def is_mountpoint(path: Path):
    r = subprocess.run([_mountpoint, "-q", path])
    return r.returncode == 0


def try_unmount(mountpoint: Path):
    logger.debug(f"Unmounting {mountpoint}")
    for _ in range(20):
        if not is_mountpoint(mountpoint):
            return
        r = subprocess.run([_fusermount, "-u", mountpoint])
        if r.returncode == 0:
            return
        logger.warning(f"Unmounting {mountpoint} failed, waiting and trying again")
        time.sleep(3)
    else:
        logger.error(f"Ultimately failed to unmount {mountpoint}")


def optsstr(opts: Mapping[str, str | int | bool | None]) -> str:
    return ",".join(
        k if v is True else f"{k}={v}"
        for (k, v) in opts.items()
        if (v is not False and v is not None)
    )


@contextmanager
def mount_dwarfs(source: Path, mountpoint: Path):
    opts = {"offset": "auto", "noatime": True}
    os.makedirs(mountpoint, exist_ok=True)
    _ = subprocess.run([_dwarfs, source, "-o", optsstr(opts), mountpoint], check=True)
    try:
        yield mountpoint
    finally:
        try_unmount(mountpoint)


@contextmanager
def mount_overlay(
    lower_dirs: list[Path],
    upperdir: Path,
    workdir: Path,
    mountpoint: Path,
    squash_uid: int | None = None,
    squash_gid: int | None = None,
):
    opts = {
        "lowerdir": ":".join(str(d) for d in reversed(lower_dirs)),
        "upperdir": str(upperdir),
        "workdir": str(workdir),
        "squash_to_uid": squash_uid,
        "squash_to_gid": squash_gid,
    }

    os.makedirs(mountpoint, exist_ok=True)
    _ = subprocess.run([_overlayfs, "-o", optsstr(opts), mountpoint], check=True)
    try:
        yield mountpoint
    finally:
        try_unmount(mountpoint)


def eval_env_sh(path: Path):
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f"source {shlex.quote(str(path))} && "
            + "python -c 'import os, json, sys; json.dump(dict(os.environ), sys.stdout)'",
        ],
        capture_output=True,
        text=True,
    )
    env: dict[str, str] = json.loads(result.stdout)
    return env


INIT_ENTRYPOINT_FUNC = r"""
init_entrypoint() {
    local filepath="$1"

    if [[ -z "$filepath" ]]; then
        echo "Usage: init_entrypoint <path-to-exe>"
        return 1
    fi
    if [[ ! -f "$filepath" ]]; then
        echo "Error: file not found: $filepath"
        return 1
    fi
    if [[ ! "$filepath" =~ \.exe$ ]]; then
        echo "Error: file must end with .exe: $filepath"
        return 1
    fi

    if [[ -z "$BARRELS_COMBINED" ]]; then
        echo "Error: BARRELS_COMBINED environment variable not set"
        return 1
    fi

    # Resolve the filepath to absolute path
    local abs_exe
    abs_exe=$(realpath "$filepath")

    # Get the absolute path of combined directory
    local abs_combined
    abs_combined=$(realpath "$BARRELS_COMBINED")

    # Split the filepath into directory and filename
    local abs_dir
    abs_dir=$(dirname "$abs_exe")
    local exe
    exe=$(basename "$abs_exe")
    
    # Calculate relative path from combined to exe's directory
    local rel_dir
    rel_dir=$(realpath --relative-to="$abs_combined" "$abs_dir")
    
    [[ "$rel_dir" == "." ]] && rel_dir=""

    {
        printf '%s\n' \
            '#!/usr/bin/env bash' \
            '' \
            'HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)' \
            'source "${HERE}/env.sh"' \
            ''
        if [[ -n "$rel_dir" ]]; then
            printf 'cd "${HERE}/%s"\n' "$rel_dir"
        fi
        printf '%s\n' \
            "wine ${exe} \"\$@\"" \
            'wine wineboot.exe --end-session' \
            'wineserver --wait'
    } > "$BARRELS_COMBINED/entrypoint.sh"

    if [[ ! -z "$EDITOR" ]]; then
        "$EDITOR" "$BARRELS_COMBINED/entrypoint.sh"
    fi
    chmod +x "$BARRELS_COMBINED/entrypoint.sh"
    echo "Created entrypoint.sh at: $BARRELS_COMBINED/entrypoint.sh"
}
export -f init_entrypoint
"""


def run_interactive_shell(
    cwd: Path, appname: str, env: dict[str, str], init_commands: str = ""
):
    if not sys.stdin.isatty():
        die("stdin is not a tty")

    with tempfile.NamedTemporaryFile(
        mode="w", prefix="barrels-rc-", delete_on_close=False, suffix=".sh"
    ) as rc_file:
        _ = rc_file.write(init_commands)
        rc_file.close()
        return subprocess.run(
            ["/bin/bash", "--init-file", rc_file.name, "-i"],
            cwd=cwd,
            env={**env, "PS1": rf"\n[{c_bold}{appname}{c_reset}] \s-\v$ "},
            stdin=sys.stdin,
        )


def run_mkdwarfs(input_dir: Path, output_file: Path | str):
    if Path(output_file).exists():
        die(f"'{output_file}' already exists")
    _ = subprocess.run(
        [
            _mkdwarfs,
            "-i",
            input_dir,
            "-o",
            output_file,
            "--set-owner=1000",
            "--set-group=1000",
        ],
        check=True,
    )


def cp_reflink(src: str | Path, dest: Path):
    _ = subprocess.run(
        ["cp", "--preserve=all", "--reflink=auto", src, dest], check=True
    )


def launch(barrels_path: Path, app: Path, extra_args: list[str]):
    if not app.is_file():
        die(f"App file '{app}' does not exist")

    appname = app.stem or die(f"{app} has no basename")
    old_userdata = Path.home() / ".local" / "share" / f"dwarf-{appname}"
    userdata = Path.home() / ".local" / "share" / "barrels" / appname
    if old_userdata.exists():
        if not userdata.exists():
            userdata.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.move(old_userdata, userdata)

    logger.info("Userdata directory: %s", userdata)
    tmpdir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))

    with ExitStack() as mounts:
        temp = Path(
            mounts.enter_context(
                tempfile.TemporaryDirectory(prefix=f"dwarf-{appname}-", dir=tmpdir)
            )
        )
        logger.debug(f"Mounting on {temp}")

        os.makedirs(datadir := userdata / "data", exist_ok=True)

        workdir = Path(
            mounts.enter_context(
                tempfile.TemporaryDirectory(dir=userdata, prefix="work")
            )
        )

        winemnt = mounts.enter_context(mount_dwarfs(barrels_path, temp / "wine"))
        appmnt = mounts.enter_context(mount_dwarfs(app, temp / "app"))
        combined = mounts.enter_context(
            mount_overlay(
                [winemnt, appmnt],
                datadir,
                workdir,
                temp / "combined",
                squash_uid=os.getuid(),
                squash_gid=os.getgid(),
            )
        )
        logger.info("Mount directory: %s", combined)

        env = eval_env_sh(combined / "env.sh")

        if extra_args and extra_args[0] == "--":
            result = subprocess.run(extra_args[1:], env=env)
        else:
            result = subprocess.run(
                [combined / "entrypoint.sh"] + extra_args,
                env=env,
            )
        sys.exit(result.returncode)


def edit_app(barrels_path: Path, app: Path):
    appname = app.stem
    if not app.is_file():
        die(f"App file '{app}' does not exist")

    with tempfile.TemporaryDirectory(
        prefix=f"tmp.dwarf-{appname}-", dir=os.getcwd()
    ) as temp:
        temp = Path(temp)
        logger.debug(f"Mounting on {temp}")

        os.makedirs(diffsdir := temp / "edit", exist_ok=True)
        os.makedirs(finalworkdir := temp / "final-work", exist_ok=True)
        os.makedirs(workdir := temp / "work", exist_ok=True)

        with ExitStack() as mounts:
            winemnt = mounts.enter_context(mount_dwarfs(barrels_path, temp / "wine"))
            appmnt = mounts.enter_context(mount_dwarfs(app, temp / "app"))

            with mount_overlay(
                [winemnt, appmnt],
                diffsdir,
                workdir,
                temp / "combined",
                squash_uid=os.getuid(),
                squash_gid=os.getgid(),
            ) as combined:
                env = eval_env_sh(combined / "env.sh")

                print(
                    c_bold
                    + f"Prepared mounts for editing {appname} in {combined}.\n"
                    + "You are now dropped into a bash shell where you can modify your app using wine.\n"
                    + "The existing app image is mounted read-only, and your changes will be saved to a new image.\n"
                    + "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."
                    + c_reset
                )

                r = run_interactive_shell(combined, appname, env)
                if r.returncode != 0:
                    die("Shell exited with error", r.returncode)

            print(c_bold + "\nFile changes:" + c_reset)
            for [dirpath, _dirnames, filenames] in os.walk(diffsdir, topdown=True):
                dirpath = Path(dirpath)
                for filename in filenames:
                    filepath = dirpath / filename
                    filestat = filepath.stat(follow_symlinks=False)
                    is_whiteout = (
                        S_IFMT(filestat.st_mode) == S_IFCHR
                        and major(filestat.st_rdev) == 0
                        and minor(filestat.st_rdev) == 0
                    ) or filename.startswith(".wh.")

                    print(
                        c_red + "DELETE" + c_reset if is_whiteout else "CHANGE",
                        filepath.relative_to(diffsdir),
                    )
            print()

            logger.info("Creating new image with your changes...")

            with mount_overlay(
                [appmnt],
                diffsdir,
                finalworkdir,
                temp / "final",
            ) as newappdir:
                newapp = app.with_name(f"{app.name}.new")
                run_mkdwarfs(newappdir, newapp)

    appbackup = app.with_name(f"{app.name}.backup")
    _ = shutil.move(app, appbackup)
    _ = shutil.move(newapp, app)
    print(
        f"New image {app} created successfully. Original image backed up as {appbackup}"
    )


def create_app(script_path: Path, app: Path):
    appname = app.stem

    with tempfile.TemporaryDirectory(
        prefix=f"tmp.dwarf-{appname}-", dir=os.getcwd()
    ) as temp:
        temp = Path(temp)
        print(f"Mounting on {temp}")

        os.makedirs(appdir := temp / "app", exist_ok=True)
        os.makedirs(workdir := temp / "work", exist_ok=True)

        uid = os.getuid()
        gid = os.getgid()

        with ExitStack() as mounts:
            winemnt = mounts.enter_context(mount_dwarfs(script_path, temp / "wine"))
            combined = mounts.enter_context(
                mount_overlay(
                    [winemnt],
                    appdir,
                    workdir,
                    temp / "combined",
                    squash_uid=uid,
                    squash_gid=gid,
                )
            )

            env = eval_env_sh(combined / "env.sh")

            print(
                c_bold
                + f"Prepared mounts for setting up {appname} in {combined}.\n"
                + "You are now dropped into a bash shell where you can set up your app using wine and "
                + "an entrypoint.sh file. \n"
                + "You can create a base entrypoint.sh with init_entrypoint <path_to_exe>.\n"
                + "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."
                + c_reset
            )

            entrypoint = combined / "entrypoint.sh"
            env_with_combined = {**env, "BARRELS_COMBINED": str(combined)}
            while not entrypoint.is_file():
                r = run_interactive_shell(
                    combined,
                    appname,
                    env_with_combined,
                    init_commands=INIT_ENTRYPOINT_FUNC,
                )
                if r.returncode != 0:
                    die("Shell exited with error", r.returncode)
                if not entrypoint.is_file():
                    print(
                        f"{c_bold}{c_red}\nMissing entrypoint.sh in {combined}{c_reset}\n"
                        + f"{c_bold}You are now dropped back into the bash shell where you can set up your app using wine and "
                        + "an entrypoint.sh file. \n"
                        + "You can create a base entrypoint.sh with init_entrypoint <path_to_exe>.\n"
                        + "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."
                        + c_reset
                    )

        run_mkdwarfs(appdir, app)
        print(f"New image {app} created successfully.")


APPRUN_TEMPLATE = r"""#!/usr/bin/env bash
set -e

cd "$APPDIR"
export PATH="$APPDIR/bin:$PATH"

APPNAME=$(basename -s.desktop ./*.desktop)
APP="$APPNAME.dwarfs"
USERDATA="$HOME/.local/share/barrels/$APPNAME"

if [ ! -f "$APP" ]; then
    echo "Error: $APP does not exist"
    exit 1
fi

TMPDIR=${XDG_RUNTIME_DIR:-/tmp}
TEMP=$(mktemp -d -p "$TMPDIR" -t barrels-"$APPNAME"-XXXXXX)
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
fuse-overlayfs -o "lowerdir=$TEMP/mnt/app:$TEMP/mnt/wine,upperdir=$USERDATA/data,workdir=$USERDATA/work,squash_to_uid=$(id -u),squash_to_gid=$(id -g)" "$TEMP/mnt/combined"

# shellcheck disable=SC1091
source "$TEMP/mnt/combined/env.sh"
if [[ "$2" == "--" ]]; then
    "${@:3}"
    exit $?
else
    "$TEMP/mnt/combined/entrypoint.sh" "${@:2}"
    exit $?
fi
"""


def create_appimage(barrels_path: Path, app: Path):
    """Create an AppDir structure for bundling into an AppImage."""
    appname = app.stem
    app_display_name = appname.title()
    appdir = Path(f"{app_display_name}.AppDir")

    if not app.is_file():
        die(f"App file '{app}' does not exist")
    if not barrels_path.is_file():
        die(f"Wine archive '{barrels_path}' does not exist")
    if appdir.exists():
        die(
            f"'{appdir}' already exists. Remove it first or choose a different app name"
        )

    logger.info(f"Creating AppDir structure in {appdir}")

    # Create AppDir and subdirectories
    appdir.mkdir()
    (appdir / "bin").mkdir()

    try:
        logger.info("Copying wine.dwarfs...")
        cp_reflink(barrels_path, appdir / "wine.dwarfs")

        logger.info(f"Copying {app.name}...")
        cp_reflink(
            app,
            appdir / f"{appname}.dwarfs",
        )

        logger.info("Creating AppRun script...")
        apprun_content = APPRUN_TEMPLATE.replace("APPNAME=", f'APPNAME="{appname}" # ')

        apprun_file = appdir / "AppRun"
        _ = apprun_file.write_text(apprun_content)
        apprun_file.chmod(0o755)

        # Create .desktop file
        logger.info(f"Creating {appname}.desktop...")
        desktop_content = f"""[Desktop Entry]
Type=Application
Name={app_display_name}
Icon={appname}
Exec=AppRun %U
Categories=Game;
"""
        _ = (appdir / f"{appname}.desktop").write_text(desktop_content)

        logger.info("Creating icon placeholder...")
        create_minimal_png(appdir / f"{appname}.png")

        # Copy binaries, TODO: use the statically linked versions from Github Releases instead?
        logger.info("Copying dwarfs binary...")
        cp_reflink(_dwarfs, appdir / "bin" / "dwarfs")

        logger.info("Copying fuse-overlayfs binary...")
        cp_reflink(_overlayfs, appdir / "bin" / "fuse-overlayfs")

        # Success message with instructions
        print()
        print(c_bold + "AppDir created successfully!" + c_reset)
        print(f"\nAppDir location: {appdir.absolute()}")
        print()
        print("Next steps:")
        print(
            f"1. (Optional) Replace placeholder {appdir}/{appname}.png with your custom icon"
        )
        print(f"2. (Optional) Customize {appdir}/{appname}.desktop")
        print("3. Build the AppImage using appimagetool:")
        print(f"   appimagetool {appdir} {app_display_name}.AppImage")
        print()

    except Exception as e:
        logger.error(f"Error creating AppDir: {e}")
        try:
            shutil.rmtree(appdir)
        except Exception as cleanup_err:
            logger.error(f"Failed to clean up {appdir}: {cleanup_err}")
        raise


@dataclass(init=False)
class Args:
    wine: Path | None
    app: Path | None
    edit: Path | None
    create: Path | None
    appimage: Path | None
    extra: list[str]


def main():
    is_embedded = sys.argv[0] == "-c"
    if is_embedded:
        logger.debug("Running in embedded mode")
        wine_path = Path(os.path.abspath(sys.argv.pop(1)))
    else:
        logger.debug("Running in detached mode")
        wine_path = Path(__file__).parent / "wine.dwarfs"
    if not wine_path.is_file():
        die(f"{wine_path} is not a file")

    parser = argparse.ArgumentParser(
        prog=wine_path.name if is_embedded else sys.argv[0],
        description="Run, edit, or create Dwarfs-based Wine applications",
        epilog="""\
launch modes:
  %(prog)s app.dwarfs                           launch via entrypoint.sh
  %(prog)s app.dwarfs args...                   launch via entrypoint.sh with args
  %(prog)s app.dwarfs -- command ...            run a custom command in the app environment

app image modes:
  %(prog)s --edit app.dwarfs                    edit an existing app image
  %(prog)s --create app.dwarfs                  create a new app image from scratch
  %(prog)s --appimage app.dwarfs                create an AppDir for AppImage bundling
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument(
        "app",
        nargs="?",
        type=Path,
        help="launch an app (default mode)",
    )
    _ = mode.add_argument(
        "--edit", metavar="APP", type=Path, help="edit an existing app image"
    )
    _ = mode.add_argument(
        "--create", metavar="APP", type=Path, help="create a new app image from scratch"
    )
    _ = mode.add_argument(
        "--appimage",
        metavar="APP",
        type=Path,
        help="create an AppDir for AppImage bundling",
    )

    _ = parser.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help="extra arguments for launch mode, see below",
    )

    args = parser.parse_args(namespace=Args())

    wine_path = (
        wine_path or args.wine or die("Could not get barrels or wine.dwarfs path")
    )

    if args.edit:
        edit_app(wine_path, args.edit)
    elif args.create:
        create_app(wine_path, args.create)
    elif args.appimage:
        create_appimage(wine_path, args.appimage)
    elif args.app:
        launch(wine_path, args.app, args.extra)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
