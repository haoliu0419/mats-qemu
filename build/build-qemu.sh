#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Builds qemu-system-arm against the libraries in $PREFIX. Every optional
# feature is off (--without-default-features) and only TCG is on: no
# display, audio, network backend, slirp, VNC, USB, block format or pixman
# is built, and nothing is downloaded (--disable-download). The release
# tarball carries every subproject but dtc, whose wrap names a git
# revision (v1.6.1): the pinned dtc tarball of that release is unpacked in
# its place, and QEMU links its libfdt statically (--enable-fdt=internal),
# which the arm target needs. The mps2-an521 machine and the serial
# backends the app uses (socket and file) are core and stay; smoke.sh
# checks both. The configure line is written to $WORK/configure-line.txt
# for the manifest.
source "$(dirname "$0")/common.sh"

SRC="$WORK/src/qemu"
BLD="$WORK/build/qemu"
rm -rf "$BLD" && mkdir -p "$BLD"

grep -qx "revision = b6910bec11614980a21e46fbccc35934b671bd81" "$SRC/subprojects/dtc.wrap" \
    && [ "$(pin get dtc version)" = 1.6.1 ] \
    || { echo "QEMU's dtc.wrap names another revision than dtc v1.6.1's: pin the release it names" >&2; exit 1; }
rm -rf "$SRC/subprojects/dtc"
cp -R "$WORK/src/dtc" "$SRC/subprojects/dtc"

configure_args=(
    --prefix="$NATIVE_PREFIX"
    --target-list=arm-softmmu
    --without-default-features
    --enable-tcg
    --disable-download
    --disable-docs
    --disable-tools
    --disable-guest-agent
    --disable-pixman
    --enable-fdt=internal
    --disable-werror
)
# --without-default-features turns the stack protector off too; macOS and
# Linux get it back from their own C libraries. On Windows it would need
# MSYS2's libssp DLL, which the archive does not carry.
if [ "$PLATFORM" != windows-x64 ]; then
    configure_args+=(--enable-stack-protector)
fi
# The manifest's copy names the prefix by a placeholder: the build
# machine's path says nothing about the archive.
printf '%s\n' "configure ${configure_args[*]}" | sed "s#--prefix=$NATIVE_PREFIX#--prefix=<prefix>#" > "$WORK/configure-line.txt"

(cd "$BLD" && "$SRC/configure" "${configure_args[@]}" && make -j "$JOBS" && make install)

echo "QEMU built into $PREFIX"
