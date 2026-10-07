# SPDX-License-Identifier: GPL-2.0-only
"""Reads an installed MSYS2 package's records in pacman's local database,
for msys2_files.py and collect.py: the files the package installed, each
with the sha256 pacman recorded for it (its mtree), and the package that
owns a file. Runs in the Windows build's MSYS2 shell, under its native
Python; `run` is how a caller runs a command (a test passes its own).
"""
import gzip
import hashlib
import os
import subprocess
import sys


def default_run(*args):
    """`args`' standard output; one that cannot start, or exits other than
    0, ends the script naming it with its exit code and standard error."""
    try:
        return subprocess.run(list(args), check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        sys.exit(f"msys2_db: {args[0]} was not found")
    except subprocess.CalledProcessError as e:
        sys.exit(f"msys2_db: {' '.join(str(a) for a in args)} exited {e.returncode}: {(e.stderr or '').strip() or 'nothing on stderr'}")


def native(posix, run=default_run):
    """An MSYS2 path as the native Python opens it."""
    return run("cygpath", "-m", posix).strip()


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


def package_files(package, version, run=default_run):
    """The files `package` at exactly `version` installed, as mtree_files
    gives them, or None when it is not installed at that version."""
    db = native(f"/var/lib/pacman/local/{package}-{version}", run)
    mtree = os.path.join(db, "mtree")
    if not os.path.isfile(mtree):
        return None
    with gzip.open(mtree, "rt", encoding="utf-8") as f:
        return mtree_files(f.read())


def owner(posix, run=default_run):
    """The package that installed the file at MSYS2 path `posix`, and its
    version, as pacman reports them (pacman fails on a file no package
    owns)."""
    name = run("pacman", "-Qqo", posix).strip()
    return name, run("pacman", "-Q", name).split()[1]
