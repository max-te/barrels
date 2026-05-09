SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:

WINE_VERSION = 11.8
WINE_MONO_VERSION = 11.1.0
WINE_FLAVOR = staging-tkg-amd64-wow64

EXECUTABLES = wget tar dwarfs shellcheck fuse-overlayfs
K := $(foreach exec,$(EXECUTABLES),\
        $(if $(shell which $(exec)),some string,$(error "No $(exec) in PATH")))

default: clean lint barrels

wine-%-${WINE_FLAVOR}.tar.xz:
	wget https://github.com/Kron4ek/Wine-Builds/releases/download/$*/wine-$*-${WINE_FLAVOR}.tar.xz

wine-mono-%-x86.tar.xz: 
	wget https://dl.winehq.org/wine/wine-mono/$*/wine-mono-$*-x86.tar.xz

wine/.sentinel: wine-${WINE_VERSION}-${WINE_FLAVOR}.tar.xz
	mkdir -p wine
	tar xvf $< -C wine --strip-components=1
	touch $@

wine/share/wine/mono/.sentinel: wine-mono-${WINE_MONO_VERSION}-x86.tar.xz
	mkdir -p wine/share/wine/mono
	tar xvf $< -C wine/share/wine/mono
	touch $@

wine/env.sh: env.sh
	shellcheck env.sh
	cp env.sh wine/

wine/prefix: wine/.sentinel wine/share/wine/mono/.sentinel wine/env.sh
	source wine/env.sh
	wine wineboot.exe --init
	wineserver --wait

wine.dwarfs: wine/prefix
	mkdwarfs -f -o wine.dwarfs -i wine

lint: *.sh
	shellcheck $^

barrels: wine.dwarfs embed.py
	shellcheck embed.sh
	echo '#!/usr/bin/env bash' > barrels
	echo 'exec python3 -c "$$(cat <<"BARRELSPYEOF"' >> barrels
	cat embed.py >> barrels
	echo '' >> barrels
	echo 'BARRELSPYEOF' >> barrels
	echo ')" "$$0" "$$@"' >> barrels
	echo 'exit 0' >> barrels
	cat wine.dwarfs >> barrels
	chmod +x barrels

unmount:
	mountpoint -q mnt/wine && fusermount -u mnt/wine
	rm -rf mnt

clean: unmount
	rm -rf wine overlay wine.dwarfs barrels

.PHONY: clean unmount lint default
