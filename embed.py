import argparse
from inspect import walktree
import os
import sys
import shutil
import subprocess
import tempfile
import time
import logging
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

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
        ["/bin/bash", "-c", f"source '{path}' && env"],
        capture_output=True,
        text=True,
    )
    env: dict[str, str] = {}
    for line in result.stdout.strip().split("\n"):
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value
    return env


def run_interactive_shell(cwd: Path, appname: str, env: dict[str, str]):
    if not sys.stdin.isatty():
        die("stdin is not a tty")
    return subprocess.run(
        ["/bin/bash", "--norc", "-i"],
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


def launch(barrels_path: Path, app: Path, extra_args: list[str]):
    if not app.is_file():
        die(f"App file '{app}' does not exist")

    appname = app.stem
    userdata = Path.home() / ".local" / "share" / f"dwarf-{appname}"
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

            print(c_bold + "\nChanged files:" + c_reset)
            for [dirpath, _dirnames, filenames] in os.walk(diffsdir, topdown=True):
                dirpath = Path(dirpath).relative_to(diffsdir)
                for filename in filenames:
                    print(">", dirpath / filename)
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
                + "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."
                + c_reset
            )

            entrypoint = combined / "entrypoint.sh"
            while not entrypoint.is_file():
                r = run_interactive_shell(combined, appname, env)
                if r.returncode != 0:
                    die("Shell exited with error", r.returncode)
                if not entrypoint.is_file():
                    print(
                        f"{c_bold}{c_red}\nMissing entrypoint.sh in {combined}{c_reset}\n"
                        + f"{c_bold}You are now dropped back into the bash shell where you can set up your app using wine and "
                        + "an entrypoint.sh file. \n"
                        + "Exit the shell with CTRL+D when you're done, or with exit 1 if something went wrong."
                        + c_reset
                    )

        run_mkdwarfs(appdir, app)
        print(f"New image {app} created successfully.")


@dataclass(init=False)
class Args:
    wine: Path | None
    app: Path | None
    edit: Path | None
    create: Path | None
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
        description="Run, edit, or create Dwarfs-based Wine applications",
        epilog="""\
launch modes:
  %(prog)s app.dwarfs                           launch via entrypoint.sh
  %(prog)s app.dwarfs args...                   launch via entrypoint.sh with args
  %(prog)s app.dwarfs -- command ...            run a custom command in the app environment
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
    elif args.app:
        launch(wine_path, args.app, args.extra)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
