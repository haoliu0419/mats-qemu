#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Builds QEMU's libraries from the unpacked tarballs, each a shared library
# only, into $PREFIX: zlib, libffi, pcre2, then glib. On macOS and Windows,
# glib takes libintl from proxy-libintl, built as its subproject from the
# pinned tarball placed in its package cache; on Linux it takes glibc's.
# Meson never downloads (--wrap-mode=nodownload), so a dependency missing
# from the prefix fails the build instead of arriving from elsewhere.
source "$(dirname "$0")/common.sh"

SRC="$WORK/src"
BLD="$WORK/build"
mkdir -p "$BLD"

cmake_lib() {
    local name="$1"; shift
    # Never reuse compiler/options cached by a previous local build.
    rm -rf "$BLD/$name"
    cmake -S "$SRC/$name" -B "$BLD/$name" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$NATIVE_PREFIX" \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_INSTALL_NAME_DIR="$NATIVE_PREFIX/lib" \
        "$@"
    cmake --build "$BLD/$name" -j "$JOBS"
    cmake --install "$BLD/$name"
}

echo "== zlib"
cmake_lib zlib -DZLIB_BUILD_STATIC=OFF -DZLIB_BUILD_TESTING=OFF

echo "== libffi"
rm -rf "$BLD/libffi" && mkdir -p "$BLD/libffi"
(cd "$BLD/libffi" && "$SRC/libffi/configure" --prefix="$NATIVE_PREFIX" --libdir="$NATIVE_PREFIX/lib" \
    --enable-shared --disable-static --disable-docs --disable-multi-os-directory \
    && make -j "$JOBS" && make install)

echo "== pcre2"
cmake_lib pcre2 -DBUILD_SHARED_LIBS=ON -DBUILD_STATIC_LIBS=OFF \
    -DPCRE2_BUILD_PCRE2_8=ON -DPCRE2_BUILD_PCRE2GREP=OFF -DPCRE2_BUILD_TESTS=OFF

echo "== glib"
glib_args=()
if [ "$PLATFORM" != linux-x64 ]; then
    # glib's wrap must name the pinned tarball's hash, or meson would
    # refuse it; then the subproject builds from that tarball alone.
    wrap="$SRC/glib/subprojects/proxy-libintl.wrap"
    grep -qx "source_filename = $(pin tarball proxy-libintl)" "$wrap" \
        || { echo "glib's proxy-libintl.wrap names another tarball than the pin" >&2; exit 1; }
    grep -qx "source_hash = $(pin get proxy-libintl sha256)" "$wrap" \
        || { echo "glib's proxy-libintl.wrap names another hash than the pin" >&2; exit 1; }
    mkdir -p "$SRC/glib/subprojects/packagecache"
    cp "$ROOT/sources/$(pin tarball proxy-libintl)" "$SRC/glib/subprojects/packagecache/"
    glib_args+=(--force-fallback-for=intl)
fi
rm -rf "$BLD/glib"
meson setup "$BLD/glib" "$SRC/glib" \
    --prefix="$NATIVE_PREFIX" --libdir=lib --buildtype=release \
    --wrap-mode=nodownload ${glib_args[@]+"${glib_args[@]}"} \
    -Ddefault_library=shared \
    -Dtests=false -Dinstalled_tests=false -Dintrospection=disabled -Dnls=disabled \
    -Dman-pages=disabled -Ddocumentation=false -Dsysprof=disabled \
    -Dlibmount=disabled -Dselinux=disabled -Dlibelf=disabled -Dglib_debug=disabled
meson compile -C "$BLD/glib" -j "$JOBS"
meson install -C "$BLD/glib"

echo "libraries built into $PREFIX"
