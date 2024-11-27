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

### Running Applications

To run a packaged application:

```bash
./barrels <app>.dwarfs
```

The application container will be mounted and launched according to its entrypoint script.

User data will be stored in `~/.local/share/dwarf-<app>`.
