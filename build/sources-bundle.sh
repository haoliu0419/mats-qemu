#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Writes dist/mats-qemu-<qemu version>-<n>-sources.tar.xz: every pinned
# tarball as downloaded, dtc's and proxy-libintl's included (the bundle's
# tarballs/ is checked to hold each one at its pin and nothing else, or no
# bundle is written, and the release with it), patches/,
# build/, pins.json, the workflow, README.md, LICENSE and NOTICE. That is
# the exact source of every library the archives of this build number carry
# (winpthreads' as MSYS2's source package for the build they ship), and
# how they were built; GCC's runtime library, which the Windows toolchain
# links into each file, is named in each archive's manifest and NOTICE, not
# bundled. Prints the bundle's path and sha256.
#   sources-bundle.sh <build number>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
N="${1:-}"
case "$N" in ''|*[!0-9]*) echo "usage: $0 <build number>" >&2; exit 2 ;; esac
# Any \r a Windows Python writes is dropped (pins.py writes none itself).
pin() { python3 "$ROOT/build/pins.py" "$@" | tr -d '\r'; }

VERSION="$(pin get qemu version)"
NAME="mats-qemu-$VERSION-$N-sources"
STAGE="$ROOT/work/bundle/$NAME"
rm -rf "$STAGE" && mkdir -p "$STAGE/tarballs" "$ROOT/dist" "$ROOT/sources"

for name in $(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import pins; print(" ".join(pins.BUILD_ORDER))' "$ROOT/build" | tr -d '\r'); do
    tarball="$(pin tarball "$name")"
    # Its progress goes to stderr: stdout is the bundle's line alone.
    bash "$ROOT/build/download.sh" "$name" >&2
    pin verify "$name" "$ROOT/sources/$tarball"
    cp "$ROOT/sources/$tarball" "$STAGE/tarballs/"
done
pin verify-bundle "$STAGE/tarballs"
cp -R "$ROOT/patches" "$ROOT/build" "$STAGE/"
find "$STAGE/build" -name '__pycache__' -prune -exec rm -rf {} +
mkdir -p "$STAGE/.github/workflows"
cp "$ROOT/.github/workflows/build.yml" "$STAGE/.github/workflows/"
cp "$ROOT/pins.json" "$ROOT/README.md" "$ROOT/LICENSE" "$ROOT/NOTICE" "$STAGE/"

OUT="$ROOT/dist/$NAME.tar.xz"
tar -C "$ROOT/work/bundle" -cJf "$OUT" "$NAME"
if command -v sha256sum >/dev/null; then sum="$(sha256sum "$OUT")"; else sum="$(shasum -a 256 "$OUT")"; fi
echo "$OUT ${sum%% *}"
