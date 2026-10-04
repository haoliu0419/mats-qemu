# SPDX-License-Identifier: GPL-2.0-only
"""Unpacks a pinned tarball the same way on every platform.

  unpack.py <component> <tarball> <folder>

The tarball's top folder is dropped, as tar's --strip-components=1 does.
Each link in it becomes a copy of what it points to, file or folder: MSYS2's
tar makes a link by copying its target, and fails when the target comes
later in the tarball (glib's COPYING does), so every platform gets copies
and builds the same tree. A link whose target is outside the tarball, or
not in it, cannot be copied and is left out; each such link must be listed
in the component's unresolved_links in pins.json, and each listed one must
be such a link, so the list stays true for the pinned tarball. A member
that is not a folder, a file or a link, or whose path is absolute or climbs
out with "..", fails the unpack.
"""
import os
import posixpath
import shutil
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pins as pins_mod  # noqa: E402


def fail(msg):
    sys.exit(f"unpack: {msg}")


def escapes(rel):
    return rel.startswith("/") or rel == ".." or rel.startswith("../")


def under(path, folder):
    return folder == "." or path == folder or path.startswith(folder + "/")


def main(argv):
    sys.stdout.reconfigure(newline="\n")
    argv = [a.strip() for a in argv]
    if len(argv) != 4:
        sys.exit(__doc__)
    name, tarball, dest = argv[1], argv[2], argv[3]
    listed = set(pins_mod.component(pins_mod.load(), name).get("unresolved_links", []))

    def local(rel):
        return os.path.join(dest, *rel.split("/"))

    top = None
    files = 0
    links = []  # (path, target), both relative to the top folder; target None when outside it
    with tarfile.open(tarball, "r:*") as t:
        for m in t:
            path = posixpath.normpath(m.name)
            if escapes(path):
                fail(f"{tarball}: {m.name} is outside the tarball's folder")
            first, _, rel = path.partition("/")
            if top is None:
                top = first
            elif first != top:
                fail(f"{tarball}: {m.name} is outside its top folder {top}")
            if not rel:
                continue
            if m.isdir():
                os.makedirs(local(rel), exist_ok=True)
            elif m.isreg():
                os.makedirs(os.path.dirname(local(rel)), exist_ok=True)
                with t.extractfile(m) as src, open(local(rel), "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(local(rel), 0o755 if m.mode & 0o111 else 0o644)
                # Autotools compares times: a generated file older than its
                # input would be regenerated, with tools the build lacks.
                os.utime(local(rel), (m.mtime, m.mtime))
                files += 1
            elif m.issym():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), m.linkname))
                links.append((rel, None if m.linkname.startswith("/") or escapes(target) else target))
            elif m.islnk():
                first, _, target = posixpath.normpath(m.linkname).partition("/")
                links.append((rel, target if first == top and target else None))
            else:
                fail(f"{tarball}: {m.name} is neither a folder, a file nor a link")

    # A link to a link waits for that one's copy, and a link to a folder for
    # the copies of the links inside it. When a round copies none, a link
    # whose target is missing and no other link could make is left out, so
    # a folder holding it can be copied without it; when there is none, the
    # rest wait on each other and are left out too.
    unresolved = [rel for rel, target in links if target is None or under(rel, target)]
    pending = [(rel, target) for rel, target in links if target is not None and not under(rel, target)]
    copied = 0
    while pending:
        waiting = []
        for rel, target in pending:
            src = local(target)
            inner = any(under(other, target) for other, _ in pending if other != rel)
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(local(rel)), exist_ok=True)
                shutil.copy2(src, local(rel))
            elif os.path.isdir(src) and not inner:
                shutil.copytree(src, local(rel))
            else:
                waiting.append((rel, target))
                continue
            copied += 1
        if len(waiting) == len(pending):
            stuck = [(rel, target) for rel, target in waiting
                     if not os.path.exists(local(target))
                     and not any(under(target, other) for other, _ in waiting if other != rel)]
            if not stuck:
                break
            unresolved += [rel for rel, _ in stuck]
            waiting = [link for link in waiting if link not in stuck]
        pending = waiting
    unresolved = sorted(unresolved + [rel for rel, _ in pending])

    new = [rel for rel in unresolved if rel not in listed]
    if new:
        fail(f"{name}: these links point outside the tarball or to nothing in it, and pins.json "
             f"does not list them in its unresolved_links: {new}")
    stale = sorted(listed - set(unresolved))
    if stale:
        fail(f"{name}: pins.json lists {stale} in its unresolved_links, but the tarball has no such link "
             f"or resolves it")
    print(f"{name}: {files} files, {copied} links copied"
          + (f", {len(unresolved)} left out as pins.json lists" if unresolved else ""))


if __name__ == "__main__":
    main(sys.argv)
