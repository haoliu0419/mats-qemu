# SPDX-License-Identifier: GPL-2.0-only
"""unpack.py keeps every member inside the folder it unpacks into: a path or
link target with a part Windows reads as a separator or a drive fails, by
name, on every platform, and an ordinary tree unpacks as before.

Run from the repository with Python 3.10 or newer:
python3 -m unittest discover -s build/tests -v
The tarballs are written by the test; unpack.py runs as the build runs it,
against zlib's entry in pins.json, which lists no unresolved links.
"""
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class UnpackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mats-qemu-unpack-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.dest = self.base / "out" / "zlib"

    def tarball(self, files=(), links=()):
        """A tarball under one top folder: `files` as (path, text), `links`
        as (path, target), each path relative to the top folder."""
        path = self.base / "zlib.tar.gz"
        with tarfile.open(path, "w:gz") as t:
            top = tarfile.TarInfo("zlib-1.3")
            top.type = tarfile.DIRTYPE
            t.addfile(top)
            for name, text in files:
                data = text.encode()
                info = tarfile.TarInfo(f"zlib-1.3/{name}")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            for name, target in links:
                info = tarfile.TarInfo(f"zlib-1.3/{name}")
                info.type = tarfile.SYMTYPE
                info.linkname = target
                t.addfile(info)
        return path

    def unpack(self, tarball):
        return subprocess.run([sys.executable, str(ROOT / "build" / "unpack.py"), "zlib", str(tarball), str(self.dest)],
                              capture_output=True, text=True, timeout=5)

    def assert_refused(self, tarball, *named):
        result = self.unpack(tarball)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        for name in named:
            self.assertIn(name, result.stderr)
        self.assertFalse((self.base / "evil").exists())

    def test_an_ordinary_tree_unpacks_with_its_link_copied(self):
        result = self.unpack(self.tarball(files=[("README", "zlib\n"), ("sub/a.c", "int a;\n")], links=[("README.link", "README")]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / "sub" / "a.c").read_text(), "int a;\n")
        self.assertEqual((self.dest / "README.link").read_text(), "zlib\n")
        self.assertIn("zlib: 2 files, 1 links copied", result.stdout)

    def test_a_path_with_a_backslash_part_is_refused(self):
        self.assert_refused(self.tarball(files=[("a\\..\\..\\..\\evil", "x")]), "a\\\\..\\\\..\\\\..\\\\evil")

    def test_a_path_with_a_drive_part_is_refused(self):
        self.assert_refused(self.tarball(files=[("C:/evil", "x")]), "'C:'")

    def test_a_link_whose_target_has_a_backslash_part_is_refused(self):
        self.assert_refused(self.tarball(files=[("README", "zlib\n")], links=[("link", "..\\..\\evil")]),
                            "has a part")

    def test_a_drive_relative_part_is_refused(self):
        # "c:evil" is drive c's current folder on Windows, not a name.
        self.assert_refused(self.tarball(files=[("docs/c:evil", "x")]), "'c:evil'")

    def test_a_name_with_a_colon_past_its_first_letter_unpacks(self):
        result = self.unpack(self.tarball(files=[("docs/note:2.txt", "colon\n")]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / "docs" / "note:2.txt").read_text(), "colon\n")


if __name__ == "__main__":
    unittest.main()
