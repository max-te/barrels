#!/usr/bin/env bash

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${HERE}/env.sh"

cd "${HERE}/prefix/drive_c" || exit
wine explorer "C:"
wine wineboot.exe --end-session
wineserver --wait
