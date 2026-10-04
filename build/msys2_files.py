# SPDX-License-Identifier: GPL-2.0-only
"""Takes an installed MSYS2 package's DLLs and licences out of the toolchain
for the archive, each checked against the sha256 pacman recorded for it when
the pinned package was installed (the mtree in pacman's local database):

  msys2_files.py <package> <version> <DLL folder> <licence folder>

The package's bin/*.dll go into the DLL folder (the build's prefix, where
collect.py takes libraries from) and its share/licenses/<name>/ files into
the licence folder. It fails, by name, when the package is not installed at
exactly that version, ships no DLL, or a file differs from its record.
Runs in the Windows build's MSYS2 shell, under its native Python.
"""
import gzip
import hashlib
import os
import shutil
import subprocess
import sys


def fail(msg):
    sys.exit(f"msys2_files: {msg}")


def native(posix):
    return subprocess.run(["cygpath", "-m", posix], check=True, capture_output=True, text=True).stdout.strip()


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def mtree_files(text):
    """Each regular file the mtree lists, as {path: fields}, its /set
    defaults applied."""
    defaults, files = {}, {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        if parts[0] == "/set":
            defaults.update(fields)
            continue
        if not parts[0].startswith("./"):
            continue
        merged = {**defaults, **fields}
        if merged.get("type", "file") == "file":
            files[parts[0][2:]] = merged
    return files


def main(argv):
    argv = [a.strip() for a in argv]
    if len(argv) != 5:
        sys.exit(__doc__)
    package, version, dll_dir, licence_dir = argv[1:]
    db = native(f"/var/lib/pacman/local/{package}-{version}")
    if not os.path.isfile(os.path.join(db, "mtree")):
        fail(f"{package} {version} is not installed")
    with gzip.open(os.path.join(db, "mtree"), "rt", encoding="utf-8") as f:
        files = mtree_files(f.read())
    taken = []
    for path, fields in sorted(files.items()):
        parts = path.split("/")
        if len(parts) == 3 and parts[1] == "bin" and parts[2].lower().endswith(".dll"):
            dest = dll_dir
        elif len(parts) >= 5 and parts[1:3] == ["share", "licenses"]:
            dest = licence_dir
        else:
            continue
        source = native("/" + path)
        want = fields.get("sha256digest")
        if not want or sha256_of(source) != want:
            fail(f"{path} differs from the sha256 pacman recorded for {package} {version}")
        os.makedirs(dest, exist_ok=True)
        shutil.copy2(source, os.path.join(dest, parts[-1]))
        taken.append(parts[-1])
    if not any(n.lower().endswith(".dll") for n in taken):
        fail(f"{package} {version} ships no DLL")
    print(f"{package} {version}: {', '.join(taken)}, each as pacman recorded it")


if __name__ == "__main__":
    main(sys.argv)
