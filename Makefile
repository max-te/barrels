SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:

WINE_VERSION = 9.22
WINE_MONO_VERSION = 9.4.0
WINE_FLAVOR = staging-tkg-amd64-wow64

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
	cp env.sh wine/

wine/prefix: wine/.sentinel wine/share/wine/mono/.sentinel wine/env.sh
	source wine/env.sh
	wine wineboot.exe --init
	wineserver --wait

wine.dwarfs: wine/prefix
	mkdwarfs -o wine.dwarfs -i wine

barrels: wine.dwarfs embed.sh
	cat embed.sh wine.dwarfs > barrels
	chmod +x barrels

unmount:
	mountpoint -q mnt/wine && fusermount -u mnt/wine
	rm -rf mnt

clean: unmount
	rm -rf wine overlay wine.dwarfs barrels

.PHONY: clean unmount

