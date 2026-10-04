#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Puts each component the platform builds in sources/ (download.sh, which
# keeps only a tarball whose sha256 is the pinned one), unpacks it into
# $WORK/src/<component> (unpack.py, the same way on every platform, each
# link a copy of its target), and applies patches/<component>/*.patch in
# name order. An msys2 component (Windows' winpthreads) is MSYS2's own
# build instead: its pinned packages are installed over the toolchain's,
# required at exactly the pinned version, and its DLL and licence taken from
# them as pacman recorded each file (msys2_files.py); its pinned source
# package is fetched for the sources bundle, not built.
source "$(dirname "$0")/common.sh"

SOURCES="$ROOT/sources"
mkdir -p "$SOURCES" "$WORK/src"

for name in $(pin components "$PLATFORM"); do
    tarball="$(pin tarball "$name")"
    bash "$ROOT/build/download.sh" "$name"
    pin verify "$name" "$SOURCES/$tarball"

    if [ "$(pin kind "$name")" = msys2 ]; then
        version="$(pin get "$name" version)"
        names=($(pin packages "$name"))
        packages=()
        for pkg in "${names[@]}"; do
            bash "$ROOT/build/download.sh" "$name" "$pkg"
            packages+=("$SOURCES/$(pin package-file "$name" "$pkg")")
        done
        pacman -U --noconfirm "${packages[@]}"
        rm -rf "$WORK/src/$name"
        for pkg in "${names[@]}"; do
            got="$(pacman -Q "$pkg" | tr -d '\r' | awk '{print $2}')"
            [ "$got" = "$version" ] || { echo "$pkg is $got after installing the pinned $version" >&2; exit 1; }
        done
        # The DLL into the prefix and the licence where collect.py reads a
        # component's licence files, from the runtime package, which
        # pins.json lists first; the others carry headers and libraries.
        python3 "$ROOT/build/msys2_files.py" "${names[0]}" "$version" \
            "$(cygpath -m "$PREFIX/bin")" "$(cygpath -m "$WORK/src/$name")" | tr -d '\r'
        echo "$name $version: installed from its pinned packages"
        continue
    fi

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
