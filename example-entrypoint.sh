#!/usr/bin/env bash

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${HERE}/env.sh"

wine explorer "C:"
wine wineboot.exe --end-session
wineserver --wait
