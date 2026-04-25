# Barrels - Customary dwarven Wine hoarding containers

Version 1: Alder

A tool for creating portable, self-contained Wine environments using [dwarfs](https://github.com/mhx/dwarfs) compressed filesystem images. It allows you to package Windows applications (primarily games) into portable containers that can be easily distributed and run on Linux systems.

## Overview

Barrels packages Wine and Wine Mono ([Kron4ek builds](https://github.com/Kron4ek/Wine-Builds)) into a single compressed archives using the dwarfs filesystem. The main executable (`barrels`) serves as both a Wine environment and a driver for creating and running application containers, allowing you to create portable Windows application packages that can run anywhere with just the driver and the application's `.dwarfs` container.

## Prerequisites

- Linux system
- dwarfs filesystem tools (`dwarfs`, `mkdwarfs`)
- fuse-overlayfs
- wget
- Basic build tools (make, tar)

## Building

To build the Wine environment driver:

```bash
make barrels
```

This will:
1. Download the required Wine and Wine Mono versions
2. Create a Wine prefix with necessary components
3. Package everything into a compressed dwarfs filesystem
4. Create a self-contained executable driver

## Usage

### Creating Application Containers

To create a new application container:

```bash
./barrels --create <app>.dwarfs
```

This will set up the necessary mounts and Wine prefix.
You now need to do the following:
1. Install your Windows application into the Wine prefix
2. Create an `entrypoint.sh` script (example provided in `example-entrypoint.sh`)
Once you're done, exit the shell, and the application container will be created.

### Editing Application Containers

To modify an existing application container:

```bash
./barrels --edit <app>.dwarfs
```

This will mount the existing container and allow you to make changes to it.
The original container will be backed up as `<app>.dwarfs.backup` before creating
the new container with your changes.

### Running Applications

To run a packaged application:

```bash
./barrels <app>.dwarfs [-- <args>]
```

The application container will be mounted and launched according to its entrypoint script.
Any arguments after `--` will be passed to the entrypoint script.

Alternatively, you can specify a command to run instead of the entrypoint:

```bash
./barrels <app>.dwarfs <command> [<args>...]
```

User data will be stored in `~/.local/share/dwarf-<app>`.

### Bundling Images into an AppImage

You can distribute an application as a self-contained AppImage by packaging the `wine.dwarfs` archive,
and the application's `.dwarfs` container and a driver AppRun script together.

**Required AppDir layout:**

```
MyApp.AppDir/
├── AppRun              # Startup script (see apprun.sh for the template)
├── myapp.desktop       # Standard .desktop entry (Icon= must match the png basename)
├── myapp.png           # Application icon (also symlinked / copied as .DirIcon)
├── myapp.dwarfs        # The application container created with --create
├── wine.dwarfs         # The Wine environment (output of `make barrels`)
└── bin/                # vendor dwarfs and fuse-overlayfs here
    ├── dwarfs
    └── fuse-overlayfs
```

**Steps:**

1. Build `wine.dwarfs` with `make barrels` and create `myapp.dwarfs` with `./barrels --create myapp.dwarfs`.
2. Create the AppDir and populate it as shown above. Copy `apprun.sh` to `MyApp.AppDir/AppRun` and make it executable.
   - Edit the `APP=` line in `AppRun` to point to your `.dwarfs` file (or use the auto-detection logic from `apprun.sh`).
   - Place `dwarfs` and `fuse-overlayfs` binaries in `AppDir/bin/`.
3. Create the `.desktop` file with at minimum `Type`, `Name`, `Icon`, and `Categories` keys.
    ```
      [Desktop Entry]
      Type=Application
      Name=MyApp
      Icon=myapp
      Categories=Game;
    ```
4. Symlink or copy the icon as `.DirIcon` inside the AppDir.
5. Pack the AppDir into an AppImage using [`appimagetool`](https://github.com/AppImage/AppImageKit):

```bash
appimagetool MyApp.AppDir MyApp.AppImage
```
