# SPDX-License-Identifier: GPL-2.0-only
# Sourced by every build script with the platform as $1: macos-arm64,
# windows-x64 (an MSYS2 UCRT64 shell) or linux-x64. Sets:
#   ROOT     this repository;
#   WORK     the build's scratch tree (sources, build folders, the prefix);
#   PREFIX   where every library and QEMU install, the only place the
#            builds look for a dependency;
#   NATIVE_PREFIX  PREFIX as the native tools spell it (a Windows path
#            with forward slashes under MSYS2, PREFIX elsewhere);
#   JOBS     the parallel job count.
# and `pin`, which runs build/pins.py.
set -euo pipefail

PLATFORM="${1:-}"
case "$PLATFORM" in
    macos-arm64|windows-x64|linux-x64) ;;
    *) echo "usage: $0 macos-arm64|windows-x64|linux-x64" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${MATS_QEMU_WORK:-$ROOT/work/$PLATFORM}"
PREFIX="$WORK/prefix"
mkdir -p "$WORK" "$PREFIX"
if [ "$PLATFORM" = windows-x64 ]; then
    NATIVE_PREFIX="$(cygpath -m "$PREFIX")"
else
    NATIVE_PREFIX="$PREFIX"
fi
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"

pin() { python3 "$ROOT/build/pins.py" "$@"; }

# pkg-config sees the prefix alone: a library found anywhere else (Homebrew
# on the macOS runner, MSYS2's packages on Windows) would ship binaries
# whose source is not in the sources bundle.
export PKG_CONFIG_LIBDIR="$NATIVE_PREFIX/lib/pkgconfig"
unset PKG_CONFIG_PATH PKG_CONFIG_SYSROOT_DIR

case "$PLATFORM" in
    macos-arm64)
        # The oldest macOS the app runs on (pins.json, Electron's own
        # floor), which collect.py checks every shipped file against;
        # headerpad leaves room for collect.py's install-name rewrite.
        MACOSX_DEPLOYMENT_TARGET="$(pin macos-minimum)"
        export MACOSX_DEPLOYMENT_TARGET
        export LDFLAGS="-Wl,-headerpad_max_install_names"
        # A function the SDK has but that macOS minimum lacks (pipe2 and
        # dup3 are macOS 27's) links weak and is NULL on an older macOS: a
        # configure check that finds one would make the library call it
        # unguarded. As an error, such a check fails and the library does
        # without it; collect.py refuses any weak import that gets through.
        export CFLAGS="-Werror=unguarded-availability-new"
        export CXXFLAGS="$CFLAGS"
        ;;
    windows-x64)
        # The GCC runtime is linked in, so the archive needs no MSYS2 DLL.
        export LDFLAGS="-static-libgcc"
        ;;
    linux-x64)
        export LDFLAGS=""
        ;;
esac
