# mats-qemu

QEMU builds for the Mats virtual target: `qemu-system-arm` with the
`mps2-an521` machine, for macOS arm64 and Windows x64 (shipped inside the
Mats app) and Linux x64 (for Mats' CI). This repository holds what makes
them and publishes, with each build, the exact source of everything in it.

## What a release holds

A release is tagged `qemu-<QEMU version>-<n>`, where `n` counts builds of
that QEMU version. It holds:

- `mats-qemu-<version>-<n>-macos-arm64.tar.xz`,
  `mats-qemu-<version>-<n>-windows-x64.zip` and
  `mats-qemu-<version>-<n>-linux-x64.tar.xz`: `qemu-system-arm` with the
  shared libraries it loads beside it, `licenses/<component>/`, a `NOTICE`
  naming each component's exact version, and `manifest.json` (the
  configure line, the component and build-tool versions, each tarball's
  sha256 and the URL it was fetched from, the system libraries it links
  and each file's sha256);
- `mats-qemu-<version>-<n>-sources.tar.xz`: every component's upstream
  tarball as built, `patches/`, `build/`, `pins.json` and the workflow;
- `SHA256SUMS` over all four.

A published release never changes: the app pins each archive by sha256.
A rebuild is the next `n`.

## What is built

`pins.json` pins each component's release tarball by sha256, with the
URLs to fetch it from in order: the upstream's release asset first where
it publishes one (GitHub releases), then its own site or a second mirror.

| Component | Licence | Linked |
|---|---|---|
| QEMU | GPL-2.0-only | the executable |
| dtc's libfdt | GPL-2.0-or-later OR BSD-2-Clause | statically, into QEMU |
| GLib | LGPL-2.1-or-later | shared |
| PCRE2 | BSD-3-Clause WITH PCRE2-exception | shared |
| libffi | MIT | shared |
| zlib | Zlib | shared |
| proxy-libintl | LGPL-2.0-or-later | shared, macOS and Windows |
| winpthreads | MIT AND BSD-3-Clause-Clear | statically, into QEMU, Windows only |

- **QEMU** is configured with every optional feature off
  (`--without-default-features`) and TCG on, for `arm-softmmu` only, with
  no tools, docs, guest agent or pixman, and nothing downloaded
  (`--disable-download`). The `mps2-an521` machine needs no display, so no
  board that needs pixman is built.
- **dtc:** the QEMU tarball carries every subproject but dtc, which its
  wrap fetches by git revision (v1.6.1); the build unpacks the pinned dtc
  1.6.1 release tarball in its place.
- **libintl:** GLib needs one. Linux's comes with glibc; on macOS and
  Windows GLib builds proxy-libintl, a stub with no translations, from the
  pinned tarball (its own wrap's, checked against the pin).
- **The libraries** are built from their tarballs on every platform, never
  taken from Homebrew or MSYS2, so the sources bundle is exactly what was
  built. pkg-config sees only the build's prefix, Meson never downloads,
  and `build/collect.py` refuses an archive that links any library other
  than the operating system's and its own.
- **Build tools** come from each platform: Xcode's clang, Homebrew's
  pkgconf and ninja on macOS; Ubuntu's GCC, cmake and ninja; MSYS2 UCRT64's
  GCC, cmake, ninja and Python on Windows. They run the build and are not
  part of what ships. Meson 1.12 needs Python 3.10 or newer
  (`python_minimum` in `pins.json`); `ci-build.sh` uses `$PYTHON`, or
  `python3`, and stops at once on an older one. It installs the pinned
  meson, setuptools, wheel and packaging wheels, each checked against its
  sha256 in `pins.json` (`pip --require-hashes`), in a virtual environment
  that QEMU's configure then runs in: QEMU's own environment requires
  setuptools and wheel (its `pythondeps.toml` tooling group) to install the
  `qemu.qmp` wheel its tarball carries, Python 3.12 and later bundle
  neither, and wheel needs packaging. On Windows, GCC's runtime is linked statically
  (`-static-libgcc`) and QEMU's stack protector is off, since it would load
  MSYS2's `libssp` DLL; macOS and Linux keep the stack protector. MSYS2's
  winpthreads, which the executable would otherwise load as
  `libwinpthread-1.dll`, is linked into it whole (`build-qemu.sh`);
  `collect.py` copies its licence into `licenses/winpthreads/`, records its
  MSYS2 package and version in the manifest, and still refuses any MSYS2
  runtime DLL, naming every file that imports one. Meson is
  pinned in `pins.json` (QEMU uses the one its tarball carries). The
  manifest records every tool's version.
- **macOS 12.0** is the oldest macOS the archive runs on (`macos_minimum`
  in `pins.json`, Electron's own floor): the build targets it, `collect.py`
  fails on any shipped file whose minimum is above it, and the manifest
  records it. A newer SDK can declare functions that macOS 12 lacks (the
  macOS 27 SDK's `pipe2` and `dup3`): a configure check that finds one
  links it weak, and the library then calls a NULL pointer on an older
  macOS. The macOS build compiles with `-Werror=unguarded-availability-new`,
  GLib's check of `pipe2` and `dup3` reads their header on macOS (the one
  patch, `patches/glib/`), so the check fails, and `collect.py` refuses any
  shipped file that still imports a system function weakly. The manifest records the SDK
  version beside the minimum.

## Building

The workflow (`.github/workflows/build.yml`) runs only when dispatched,
with the build number:

1. **check:** refuses a build number already released.
2. **build,** per platform, `build/ci-build.sh <platform> <n>`:
   - `fetch.sh` fetches each tarball (`download.sh`), unpacks it
     (`unpack.py`) and applies `patches/`. `download.sh` tries the pin's
     URLs in order, each twice, keeps the first file whose sha256 is the
     pinned one and deletes any other, so a mirror that serves other bytes
     for a moment costs a retry; it fails only when no URL serves the
     pinned file, naming what each one served.
     `unpack.py` makes each link in a tarball a copy of its target on every
     platform, since MSYS2's tar copies a link's target and fails when that
     target comes later in the tarball (GLib's `COPYING` does). A link that
     points outside its tarball or to nothing in it is left out, and must be
     listed in the component's `unresolved_links` in `pins.json` (QEMU's
     one is an EDK II link to `/opt/X11/include`, in firmware sources the
     build does not use); a listed one that the tarball resolves, or lacks,
     fails the unpack too;
   - `build-libs.sh` builds zlib, libffi, PCRE2 and GLib into the prefix;
   - `build-qemu.sh` builds QEMU against them;
   - `collect.py` copies the executable and every library it loads from
     the prefix, makes them find each other beside it (`@loader_path` on
     macOS, `$ORIGIN` on Linux; Windows looks beside the executable),
     checks the macOS minimum, adds the licences, NOTICE and manifest, and
     archives the result;
   - `smoke.py` runs the archive unpacked into a fresh folder: the version,
     `mps2-an521` and the socket and file serial backends are present, and
     the machine starts with UART0 a socket server on 127.0.0.1 that
     accepts a connection.
3. **release:** builds the sources bundle, which must hold every pinned
   tarball at its pin and nothing else (`pins.py verify-bundle`), writes
   `SHA256SUMS`, and publishes the release with the workflow's own token.

The same script builds one platform locally:

```
PYTHON=/opt/homebrew/bin/python3 bash build/ci-build.sh macos-arm64 1
```

(`PYTHON` only where `python3` is older than 3.10, as Xcode's is.)

It writes under `work/`, `sources/` and `dist/`.

On macOS the archive's files are signed ad hoc. The Mats app signs them
with its own identity and the JIT entitlement QEMU's TCG needs under the
hardened runtime.

## Licence

The scripts and workflow here are under the GPL-2.0 (`LICENSE`), as QEMU
is. Each component keeps its own licence; see `NOTICE`.
