#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Downloads each component the platform builds into sources/, refuses any
# tarball whose sha256 is not the pinned one, unpacks it into
# $WORK/src/<component>, and applies patches/<component>/*.patch in name
# order. A tarball already in sources/ is checked, not downloaded again.
source "$(dirname "$0")/common.sh"

SOURCES="$ROOT/sources"
mkdir -p "$SOURCES" "$WORK/src"

for name in $(pin components "$PLATFORM"); do
    tarball="$(pin tarball "$name")"
    if [ ! -f "$SOURCES/$tarball" ]; then
        curl -fsSL --retry 3 -o "$SOURCES/$tarball.part" "$(pin get "$name" url)"
        mv "$SOURCES/$tarball.part" "$SOURCES/$tarball"
    fi
    pin verify "$name" "$SOURCES/$tarball"

    dest="$WORK/src/$name"
    rm -rf "$dest"
    mkdir -p "$dest"
    tar -xf "$SOURCES/$tarball" -C "$dest" --strip-components=1

    if [ -d "$ROOT/patches/$name" ]; then
        find "$ROOT/patches/$name" -maxdepth 1 -name '*.patch' | LC_ALL=C sort | while read -r p; do
            echo "applying $(basename "$p") to $name"
            patch -d "$dest" -p1 --forward < "$p"
        done
    fi
    echo "$name $(pin get "$name" version): checked and unpacked"
done
