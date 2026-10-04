#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Puts each component the platform builds in sources/ (download.sh, which
# keeps only a tarball whose sha256 is the pinned one), unpacks it into
# $WORK/src/<component> (unpack.py, the same way on every platform, each
# link a copy of its target), and applies patches/<component>/*.patch in
# name order.
source "$(dirname "$0")/common.sh"

SOURCES="$ROOT/sources"
mkdir -p "$SOURCES" "$WORK/src"

for name in $(pin components "$PLATFORM"); do
    tarball="$(pin tarball "$name")"
    bash "$ROOT/build/download.sh" "$name"
    pin verify "$name" "$SOURCES/$tarball"

    dest="$WORK/src/$name"
    rm -rf "$dest"
    mkdir -p "$dest"
    python3 "$ROOT/build/unpack.py" "$name" "$SOURCES/$tarball" "$dest" | tr -d '\r'

    if [ -d "$ROOT/patches/$name" ]; then
        find "$ROOT/patches/$name" -maxdepth 1 -name '*.patch' | LC_ALL=C sort | while read -r p; do
            echo "applying $(basename "$p") to $name"
            patch -d "$dest" -p1 --forward < "$p"
        done
    fi
    echo "$name $(pin get "$name" version): checked and unpacked"
done
