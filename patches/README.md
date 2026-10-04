# Patches

One: `glib/0001-check-pipe2-and-dup3-against-their-header-on-macos.patch`.
GLib's configure checks each function it may use without its header, so
on macOS a function the SDK declares newer than the deployment target
(the macOS 27 SDK's `pipe2` and `dup3`) passes the check, links weak, and
is NULL on an older macOS. The patch checks those two against
`<unistd.h>` on macOS, where the SDK's availability attribute applies and
the build's `-Werror=unguarded-availability-new` makes the check fail.
Every other component builds from its unmodified upstream tarball.

- **Why:** the macOS 27 SDK declares `pipe2` and `dup3` available from
  macOS 27 on. GLib 2.88.3 sets `HAVE_PIPE2` from a check without the
  header (`cc.has_function(f)`, meson.build), so a build with that SDK for
  macOS 12 called `pipe2` unguarded, and QEMU crashed in
  `g_unix_open_pipe` at start on any older macOS.
- **When to drop it:** once the pinned GLib checks these functions against
  their header on macOS itself; the patch then no longer applies, and
  `fetch.sh` fails on it, which is the signal. Each archive's
  `manifest.json` names every patch applied, with its sha256.

A patch for a component goes in `patches/<component>/` (the component's
name in `pins.json`), as a `-p1` diff against the unpacked tarball, named
`NNNN-what-it-does.patch`. `build/fetch.sh` applies them in name order and
fails on any that does not apply. The sources bundle carries this folder,
so a patched build's source is still complete.
