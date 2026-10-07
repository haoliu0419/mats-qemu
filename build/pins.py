# SPDX-License-Identifier: GPL-2.0-only
"""Reads pins.json for the build scripts, so no shell parses JSON.

  pins.py components <platform>        the components that platform builds, in build order
  pins.py get <component> <field>      one text field of a component (version, sha256, license)
  pins.py urls <component>             the URLs the component's tarball is fetched from, in the
                                       order to try them, one per line
  pins.py tarball <component>          the file name every one of those URLs ends in
  pins.py kind <component>             "tarball" (built from its source) or "msys2" (MSYS2's
                                       build installed from pinned packages; its source package
                                       is pinned and bundled, not built)
  pins.py packages <component>         an msys2 component's binary packages, one per line, the
                                       runtime package (the one with the DLL) first
  pins.py package-urls <c> <package>   that package's URLs, in the order to try them
  pins.py package-file <c> <package>   the file name they end in
  pins.py package-sha256 <c> <package> its pinned sha256
  pins.py license-files <component>    the component's licence files, one per line
  pins.py tool <name>                  a build tool's pinned version
  pins.py requirements                 the build tools as a pip requirements file, each
                                       pinned to its wheel's sha256 (pip --require-hashes)
  pins.py macos-minimum                the oldest macOS the archive runs on
  pins.py get-top <key>                a top-level text value (python_minimum, macos_minimum)
  pins.py sha256 <file>                the file's sha256
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
# zlib, and builds dtc's libfdt itself, as its subproject. On Windows,
# winpthreads (MSYS2's, installed from its pinned packages) is in place
# before glib and QEMU link it.
BUILD_ORDER = ["zlib", "libffi", "pcre2", "proxy-libintl", "winpthreads", "glib", "dtc", "qemu"]
KINDS = ("tarball", "msys2")

PINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pins.json")


def load():
    with open(PINS, encoding="utf-8") as f:
        pins = json.load(f)
    names = set(pins["components"])
    if names != set(BUILD_ORDER):
        sys.exit(f"pins.json names {sorted(names)}, but the build order is {BUILD_ORDER}")
    # Each URL is another place to fetch the same file, which the sha256
    # decides: they must all name it, so the tarball has one name.
    for name, info in pins["components"].items():
        urls = info.get("urls")
        if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and u.startswith("https://") for u in urls):
            sys.exit(f"pins.json: {name}'s urls must be a list of https URLs")
        files = {u.rsplit("/", 1)[1] for u in urls}
        if len(files) != 1:
            sys.exit(f"pins.json: {name}'s urls end in different file names {sorted(files)}")
        if info.get("kind", "tarball") not in KINDS:
            sys.exit(f"pins.json: {name}'s kind must be one of {KINDS}")
        if (info.get("kind") == "msys2") != bool(info.get("packages")):
            sys.exit(f"pins.json: {name} must name its packages exactly when its kind is msys2")
        for pkg, pinfo in info.get("packages", {}).items():
            purls = pinfo.get("urls")
            if not isinstance(purls, list) or not purls or not all(u.startswith("https://") for u in purls) \
                    or len({u.rsplit("/", 1)[1] for u in purls}) != 1 or not pinfo.get("sha256"):
                sys.exit(f"pins.json: {name}'s package {pkg} needs https urls ending in one file name, and a sha256")
    # The licence texts of a toolchain library linked into the Windows
    # archive (collect.py), each file's sha256 pinned.
    for lib, texts in pins.get("toolchain_licenses", {}).items():
        if not isinstance(texts, dict) or not texts or not all(
                isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h) for h in texts.values()):
            sys.exit(f"pins.json: toolchain_licenses' {lib} must map each licence file to its sha256")
    return pins


def tarball_name(info):
    return info["urls"][0].rsplit("/", 1)[1]


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
    # Lines end in \n on every platform: a Windows Python (MSYS2's UCRT64 one)
    # would write \r\n, and a shell loop over the output would read "zlib\r".
    sys.stdout.reconfigure(newline="\n")
    argv = [a.strip() for a in argv]
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
    elif cmd == "urls" and len(argv) == 3:
        for url in component(pins, argv[2])["urls"]:
            print(url)
    elif cmd == "tarball" and len(argv) == 3:
        print(tarball_name(component(pins, argv[2])))
    elif cmd == "kind" and len(argv) == 3:
        print(component(pins, argv[2]).get("kind", "tarball"))
    elif cmd == "packages" and len(argv) == 3:
        for pkg in component(pins, argv[2]).get("packages", {}):
            print(pkg)
    elif cmd in ("package-urls", "package-file", "package-sha256") and len(argv) == 4:
        pkg = component(pins, argv[2]).get("packages", {}).get(argv[3])
        if pkg is None:
            sys.exit(f"pins.json: {argv[2]} has no package {argv[3]}")
        if cmd == "package-urls":
            for url in pkg["urls"]:
                print(url)
        elif cmd == "package-file":
            print(pkg["urls"][0].rsplit("/", 1)[1])
        else:
            print(pkg["sha256"])
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
    elif cmd == "sha256" and len(argv) == 3:
        print(sha256_of(argv[2]))
    elif cmd == "verify" and len(argv) == 4:
        want = component(pins, argv[2])["sha256"]
        got = sha256_of(argv[3])
        if got != want:
            sys.exit(f"{argv[3]}: sha256 {got}, but pins.json pins {want} for {argv[2]}")
    elif cmd == "verify-bundle" and len(argv) == 3:
        want = {tarball_name(component(pins, n)): n for n in BUILD_ORDER}
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
