#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# One platform's whole build, as the workflow runs it and as a person can
# run it locally:
#   ci-build.sh <platform> <build number>
# Installs the pinned build tools (meson, and setuptools, wheel and the
# packaging wheel needs) in a virtual environment made from $PYTHON
# (python3 when unset), which must be at least pins.json's python_minimum
# (pinned meson's own floor). The environment then comes first on PATH and
# becomes $PYTHON, which QEMU's configure takes: its own environment nests
# in this one and finds setuptools and wheel there, which its tooling group
# requires before it installs the qemu.qmp wheel its tarball carries, and
# which Python 3.12 and later do not bundle. Then it fetches and
# checks the sources, builds the libraries and QEMU, stages and checks the
# archive (collect.py), and runs smoke.py on the archive unpacked into a
# fresh folder, so what is tested is what ships.
set -euo pipefail

PLATFORM="${1:-}"
N="${2:-}"
case "$PLATFORM" in
    macos-arm64|windows-x64|linux-x64) ;;
    *) echo "usage: $0 macos-arm64|windows-x64|linux-x64 <build number>" >&2; exit 2 ;;
esac
case "$N" in ''|0*|*[!0-9]*) echo "usage: $0 <platform> <positive build number without leading zeroes>" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
# Any \r a Windows Python writes is dropped (pins.py writes none itself).
pin() { "$PY" "$ROOT/build/pins.py" "$@" | tr -d '\r'; }
WORK="${MATS_QEMU_WORK:-$ROOT/work/$PLATFORM}"
mkdir -p "$WORK"
# Installation does not remove files a new recipe stops producing. Every
# run owns a fresh prefix; only the checked tarballs in sources/ are reused.
rm -rf "$WORK/prefix"
mkdir -p "$WORK/prefix"

TOOLS="$WORK/tools"
rm -rf "$TOOLS"
floor="$(pin get-top python_minimum)"
"$PY" -c 'import sys; want = tuple(int(x) for x in sys.argv[1].split(".")); sys.exit(sys.version_info[:len(want)] < want)' "$floor" \
    || { echo "$PY is Python $("$PY" -c 'import platform; print(platform.python_version())'); meson $(pin tool meson) needs $floor or newer: set PYTHON" >&2; exit 1; }
"$PY" -m venv "$TOOLS"
bin="$TOOLS/bin"
[ -d "$bin" ] || bin="$TOOLS/Scripts"
# Wheels only, each checked against its pinned sha256, as the tarballs are.
pin requirements > "$TOOLS/requirements.txt"
"$bin/python" -m pip install --quiet --require-hashes --only-binary :all: -r "$TOOLS/requirements.txt"
export PATH="$bin:$PATH"
if [ -e "$bin/python3" ] || [ -e "$bin/python3.exe" ]; then export PYTHON="$bin/python3"; else export PYTHON="$bin/python"; fi
[ "$(meson --version)" = "$(pin tool meson)" ] \
    || { echo "meson $(meson --version) is on PATH, not the pinned $(pin tool meson)" >&2; exit 1; }

bash "$ROOT/build/fetch.sh" "$PLATFORM"
bash "$ROOT/build/build-libs.sh" "$PLATFORM"
bash "$ROOT/build/build-qemu.sh" "$PLATFORM"
# The tools' Python runs collect.py, which reads setuptools' version from
# it; MSYS2's own python3 has none.
collected="$("$PYTHON" "$ROOT/build/collect.py" "$PLATFORM" "$N" | tr -d '\r')"
archive="${collected%%$'\n'*}"
# The final token is the sha256; the archive path itself may contain spaces.
archive="${archive% *}"
# collect.py prints native paths; MSYS2's tools take POSIX ones.
if [ "$PLATFORM" = windows-x64 ]; then archive="$(cygpath -u "$archive")"; fi
[ -n "$archive" ] && [ -f "$archive" ] || { echo "collect.py named no archive" >&2; exit 1; }

UNPACKED="$WORK/unpacked"
rm -rf "$UNPACKED" && mkdir -p "$UNPACKED"
case "$archive" in
    *.zip) (cd "$UNPACKED" && unzip -q "$archive") ;;
    *) tar -xf "$archive" -C "$UNPACKED" ;;
esac
folder="$UNPACKED/$(basename "${archive%.zip}" .tar.xz)"
if [ "$PLATFORM" = windows-x64 ]; then folder="$(cygpath -m "$folder")"; fi
"$PYTHON" "$ROOT/build/smoke.py" "$folder" "$(pin get qemu version)"
echo "built and checked: $archive"
