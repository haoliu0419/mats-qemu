# SPDX-License-Identifier: GPL-2.0-only
"""Reads pins.json for the build scripts, so no shell parses JSON.

  pins.py components <platform>        the components that platform builds, in build order
  pins.py get <component> <field>      one field of a component (version, url, sha256, license)
  pins.py tarball <component>          the file name the component's url ends in
  pins.py license-files <component>    the component's licence files, one per line
  pins.py tool <name>                  a build tool's pinned version
  pins.py requirements                 the build tools as a pip requirements file, each
                                       pinned to its wheel's sha256 (pip --require-hashes)
  pins.py macos-minimum                the oldest macOS the archive runs on
  pins.py get-top <key>                a top-level text value (python_minimum, macos_minimum)
  pins.py verify <component> <file>    exits 1 unless the file's sha256 is the pinned one
  pins.py verify-bundle <folder>       exits 1 unless the folder holds every pinned tarball,
                                       each with its pinned sha256, and nothing else
"""
import hashlib
import json
import os
import sys

# Each library is built before what links it: glib takes zlib, libffi,
# pcre2 and (on macOS and Windows) proxy-libintl, and QEMU takes glib and
# zlib, and builds dtc's libfdt itself, as its subproject.
BUILD_ORDER = ["zlib", "libffi", "pcre2", "proxy-libintl", "glib", "dtc", "qemu"]

PINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pins.json")


def load():
    with open(PINS, encoding="utf-8") as f:
        pins = json.load(f)
    names = set(pins["components"])
    if names != set(BUILD_ORDER):
        sys.exit(f"pins.json names {sorted(names)}, but the build order is {BUILD_ORDER}")
    return pins


def component(pins, name):
    if name not in pins["components"]:
        sys.exit(f"pins.json has no component {name}")
    return pins["components"][name]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    pins = load()
    cmd = argv[1]
    if cmd == "components" and len(argv) == 3:
        for name in BUILD_ORDER:
            if argv[2] in pins["components"][name]["platforms"]:
                print(name)
    elif cmd == "get" and len(argv) == 4:
        value = component(pins, argv[2]).get(argv[3])
        if value is None or isinstance(value, (list, dict)):
            sys.exit(f"{argv[2]} has no text field {argv[3]}")
        print(value)
    elif cmd == "tarball" and len(argv) == 3:
        print(component(pins, argv[2])["url"].rsplit("/", 1)[1])
    elif cmd == "license-files" and len(argv) == 3:
        for name in component(pins, argv[2])["license_files"]:
            print(name)
    elif cmd == "tool" and len(argv) == 3:
        if argv[2] not in pins["build_tools"]:
            sys.exit(f"pins.json pins no build tool {argv[2]}")
        print(pins["build_tools"][argv[2]]["version"])
    elif cmd == "requirements" and len(argv) == 2:
        for name, tool in pins["build_tools"].items():
            print(f"{name}=={tool['version']} --hash=sha256:{tool['sha256']}")
    elif cmd == "get-top" and len(argv) == 3:
        value = pins.get(argv[2])
        if not isinstance(value, str):
            sys.exit(f"pins.json has no top-level text value {argv[2]}")
        print(value)
    elif cmd == "macos-minimum" and len(argv) == 2:
        print(pins["macos_minimum"])
    elif cmd == "verify" and len(argv) == 4:
        want = component(pins, argv[2])["sha256"]
        got = sha256_of(argv[3])
        if got != want:
            sys.exit(f"{argv[3]}: sha256 {got}, but pins.json pins {want} for {argv[2]}")
    elif cmd == "verify-bundle" and len(argv) == 3:
        want = {component(pins, n)["url"].rsplit("/", 1)[1]: n for n in BUILD_ORDER}
        have = set(os.listdir(argv[2]))
        missing = sorted(set(want) - have)
        extra = sorted(have - set(want))
        if missing or extra:
            sys.exit(f"{argv[2]}: missing {missing}, unpinned {extra}")
        for tarball, name in sorted(want.items()):
            got = sha256_of(os.path.join(argv[2], tarball))
            if got != component(pins, name)["sha256"]:
                sys.exit(f"{argv[2]}/{tarball}: sha256 {got}, not the pin")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
