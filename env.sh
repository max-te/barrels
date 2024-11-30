# shellcheck shell=bash

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

export LD_LIBRARY_PATH="${HERE}/lib:${LD_LIBRARY_PATH:-}" \
    PATH="${HERE}/bin:${PATH}" \
    WINEPREFIX="${HERE}/prefix" \
    WINEARCH="win64" \
    WINEDLLOVERRIDES="mscoree,mshtml="
