# SPDX-License-Identifier: GPL-2.0-only
"""collect.py's checks on what an archive holds. Every staged file is built
for the platform's architecture. For the Windows archive's GCC runtime, it
takes libgcc's licence texts from the MSYS2 package that installed the
libgcc the build linked, checks each against pacman's record and the pin,
records the package in the manifest and the NOTICE, refuses a flag that
loads a GCC plugin, and checks the written archive holds both texts at the
pins.

Run from the repository with Python 3.10 or newer:
python3 -m unittest discover -s build/tests -v
No MSYS2 is needed: pacman's database is a folder the test writes, and
every command collect.py runs is answered by the test.
"""
import gzip
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "build"))
import collect  # noqa: E402
import pins as pins_mod  # noqa: E402

PACKAGE, VERSION = "mingw-w64-ucrt-x86_64-gcc", "16.2.0-4"
LIBGCC = "/ucrt64/lib/gcc/x86_64-w64-mingw32/16.2.0/libgcc.a"
TEXTS = {"COPYING3": b"the GPL-3.0 as the package ships it\n",
         "COPYING.RUNTIME": b"the GCC Runtime Library Exception 3.1 as the package ships it\n"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mats-qemu-toolchain-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "msys64"
        self.stage = Path(tmp.name) / "stage"
        self.stage.mkdir()
        pins = pins_mod.load()
        pins["toolchain_licenses"] = {"libgcc": {name: sha256(data) for name, data in TEXTS.items()}}
        self.addCleanup(setattr, collect, "PINS", getattr(collect, "PINS", None))
        collect.PINS = pins

    def install(self, texts=TEXTS, recorded=None):
        """The GCC package as pacman installed it: each text under
        share/licenses/gcc/, and an mtree recording `recorded` (the texts'
        own sha256 unless given)."""
        recorded = recorded or {name: sha256(data) for name, data in texts.items()}
        lines = ["#mtree", "/set type=file uid=0 gid=0 mode=644", f".{LIBGCC} sha256digest={'0' * 64}"]
        for name, data in texts.items():
            path = self.root / "ucrt64" / "share" / "licenses" / "gcc" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            lines.append(f"./ucrt64/share/licenses/gcc/{name} size={len(data)} sha256digest={recorded[name]}")
        db = self.root / "var" / "lib" / "pacman" / "local" / f"{PACKAGE}-{VERSION}"
        db.mkdir(parents=True, exist_ok=True)
        with gzip.open(db / "mtree", "wt", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def run_cmd(self, *args):
        answers = {
            ("gcc", "-print-libgcc-file-name"): "C:/msys64" + LIBGCC + "\n",
            ("cygpath", "-u", "C:/msys64" + LIBGCC): LIBGCC + "\n",
            ("pacman", "-Qqo", LIBGCC): PACKAGE + "\n",
            ("pacman", "-Q", PACKAGE): f"{PACKAGE} {VERSION}\n",
            ("gcc", "--version"): "gcc.exe (Rev4, Built by MSYS2 project) 16.2.0\nCopyright\n",
            ("gcc", "-dumpfullversion"): "16.2.0\n",
        }
        if args in answers:
            return answers[args]
        if args[:2] == ("cygpath", "-m"):
            return str(self.root) + args[2] + "\n"
        raise AssertionError(f"collect.py ran {args}, which this test does not answer")

    def collect_toolchain(self):
        return collect.windows_toolchain_libraries(str(self.stage), self.run_cmd)

    def assert_fails(self, pattern):
        with self.assertRaises(SystemExit) as ctx:
            self.collect_toolchain()
        self.assertRegex(str(ctx.exception), pattern)

    def test_texts_come_from_the_package_that_installed_the_linked_libgcc_and_the_manifest_names_it(self):
        self.install()
        toolchain = self.collect_toolchain()
        for name, data in TEXTS.items():
            self.assertEqual((self.stage / "licenses" / "libgcc" / name).read_bytes(), data)
        self.assertEqual(toolchain, {"libgcc": {
            "version": "gcc.exe (Rev4, Built by MSYS2 project) 16.2.0",
            "gcc": "16.2.0",
            "package": PACKAGE,
            "package_version": VERSION,
            "file": LIBGCC,
            "license": "GPL-3.0-or-later WITH GCC-exception-3.1",
            "license_files": {name: sha256(data) for name, data in TEXTS.items()},
            "plugins": "none",
            "linked": "statically, into each file (-static-libgcc)",
        }})

    def test_a_text_unlike_pacmans_record_fails_naming_it(self):
        self.install(recorded={"COPYING3": sha256(TEXTS["COPYING3"]), "COPYING.RUNTIME": "1" * 64})
        self.assert_fails(r"ucrt64/share/licenses/gcc/COPYING\.RUNTIME differs from the sha256 pacman recorded")

    def test_a_text_unlike_the_pin_fails_naming_it(self):
        changed = {**TEXTS, "COPYING3": b"a changed text\n"}
        self.install(texts=changed)
        self.assert_fails(r"ucrt64/share/licenses/gcc/COPYING3 is not the text pins\.json pins")

    def test_a_package_without_a_text_fails_naming_it(self):
        self.install(texts={"COPYING3": TEXTS["COPYING3"]})
        self.assert_fails(r"ships no share/licenses/gcc/COPYING\.RUNTIME")

    def test_the_notice_names_libgcc_with_its_gcc_and_package(self):
        self.install()
        toolchain = self.collect_toolchain()
        plat = collect.Platform("windows-x64", "C:/prefix")
        collect.write_notice(str(self.stage), plat, "11.1.2", "4", toolchain)
        notice = (self.stage / "NOTICE").read_text(encoding="utf-8")
        self.assertIn(f"  libgcc (gcc 16.2.0, {PACKAGE} {VERSION}), GPL-3.0-or-later WITH GCC-exception-3.1,"
                      " linked statically, into each file (-static-libgcc)\n", notice)
        self.assertIn("Eligible Compilation\n                 Process", notice)


class PluginTests(unittest.TestCase):
    def test_a_flag_that_loads_a_plugin_fails_naming_it(self):
        with self.assertRaises(SystemExit) as ctx:
            collect.check_no_plugins({"CFLAGS": "-O2", "LDFLAGS": "-static-libgcc -fplugin=./annobin.so",
                                      "configure": "configure --extra-cflags=-fplugin-arg-x"})
        self.assertIn("LDFLAGS: -fplugin=./annobin.so", str(ctx.exception))
        self.assertIn("configure: --extra-cflags=-fplugin-arg-x", str(ctx.exception))

    def test_the_build_flags_load_no_plugin(self):
        collect.check_no_plugins({"CFLAGS": "", "CXXFLAGS": "", "LDFLAGS": "-static-libgcc",
                                  "configure": "configure --target-list=arm-softmmu --prefix=<prefix>"})


class RunTests(unittest.TestCase):
    """A command that fails names itself with its exit code and its stderr,
    so a failure on a runner shows its cause in the log."""

    def test_a_failing_command_carries_its_exit_code_and_stderr(self):
        with self.assertRaises(SystemExit) as ctx:
            collect.run(sys.executable, "-c", "import sys; sys.stderr.write('the cause'); sys.exit(3)")
        self.assertIn("exited 3: the cause", str(ctx.exception))

    def test_a_command_that_is_not_there_says_so(self):
        with self.assertRaises(SystemExit) as ctx:
            collect.run("mats-qemu-no-such-tool")
        self.assertIn("mats-qemu-no-such-tool was not found", str(ctx.exception))

    def test_pacmans_reader_carries_the_cause_too(self):
        import msys2_db
        with self.assertRaises(SystemExit) as ctx:
            msys2_db.default_run(sys.executable, "-c", "import sys; sys.stderr.write('no package'); sys.exit(1)")
        self.assertIn("exited 1: no package", str(ctx.exception))


class BuildFlagsTests(unittest.TestCase):
    """The Windows build's flags are what build-libs.sh and build-qemu.sh
    recorded as they built (common.sh record_flags): no shell is started to
    work them out again."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mats-qemu-flags-")
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)

    def record(self, name, ldflags="-static-libgcc"):
        (self.work / f"build-flags-{name}.txt").write_text(f"CFLAGS=\nCXXFLAGS=\nLDFLAGS={ldflags}\n", encoding="utf-8")

    def test_the_flags_each_build_recorded_are_read(self):
        self.record("libs")
        self.record("qemu")
        self.assertEqual(collect.windows_build_flags(str(self.work)), {
            "build-libs CFLAGS": "", "build-libs CXXFLAGS": "", "build-libs LDFLAGS": "-static-libgcc",
            "build-qemu CFLAGS": "", "build-qemu CXXFLAGS": "", "build-qemu LDFLAGS": "-static-libgcc",
        })

    def test_a_build_that_recorded_nothing_fails_naming_its_record(self):
        self.record("libs")
        with self.assertRaises(SystemExit) as ctx:
            collect.windows_build_flags(str(self.work))
        self.assertIn("build-flags-qemu.txt is missing", str(ctx.exception))

    def test_a_plugin_a_build_ran_with_fails_naming_the_build(self):
        self.record("libs", "-static-libgcc -fplugin=./annobin.so")
        self.record("qemu")
        with self.assertRaises(SystemExit) as ctx:
            collect.check_no_plugins(collect.windows_build_flags(str(self.work)))
        self.assertIn("build-libs LDFLAGS: -fplugin=./annobin.so", str(ctx.exception))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mats-qemu-archive-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        pins = pins_mod.load()
        pins["toolchain_licenses"] = {"libgcc": {name: sha256(data) for name, data in TEXTS.items()}}
        self.addCleanup(setattr, collect, "PINS", getattr(collect, "PINS", None))
        collect.PINS = pins
        self.toolchain = {"libgcc": {"license_files": {name: sha256(data) for name, data in TEXTS.items()}}}

    def archive(self, texts):
        out = self.dir / "mats-qemu-11.1.2-4-windows-x64.zip"
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("mats-qemu-11.1.2-4-windows-x64/qemu-system-arm.exe", b"exe")
            for name, data in texts.items():
                z.writestr(f"mats-qemu-11.1.2-4-windows-x64/licenses/libgcc/{name}", data)
        return str(out)

    def check(self, texts):
        collect.check_archive_licences(self.archive(texts), "mats-qemu-11.1.2-4-windows-x64", self.toolchain)

    def test_an_archive_with_both_texts_at_the_pins_passes(self):
        self.check(TEXTS)

    def test_an_archive_missing_a_text_fails_naming_it(self):
        with self.assertRaises(SystemExit) as ctx:
            self.check({"COPYING3": TEXTS["COPYING3"]})
        self.assertIn("has no mats-qemu-11.1.2-4-windows-x64/licenses/libgcc/COPYING.RUNTIME", str(ctx.exception))

    def test_an_archive_whose_text_changed_fails_naming_it(self):
        with self.assertRaises(SystemExit) as ctx:
            self.check({**TEXTS, "COPYING.RUNTIME": b"another text\n"})
        self.assertIn("licenses/libgcc/COPYING.RUNTIME has sha256", str(ctx.exception))


def objdump(fmt, arch):
    return f"\nfile:     file format {fmt}\narchitecture: {arch}, flags 0x00000150:\nstart address 0x0\n"


class ArchitectureTests(unittest.TestCase):
    def check(self, platform, answers):
        def run_cmd(*args):
            return answers[os.path.basename(args[-1])]
        return collect.check_architecture("/stage", sorted(answers), platform, run_cmd)

    def test_files_of_the_platforms_architecture_pass_and_name_it_for_the_manifest(self):
        self.assertEqual(self.check("macos-arm64", {"qemu-system-arm": "arm64\n", "libglib-2.0.0.dylib": "arm64\n"}), "arm64")
        self.assertEqual(self.check("linux-x64", {"qemu-system-arm": objdump("elf64-x86-64", "i386:x86-64")}), "x86-64")
        self.assertEqual(self.check("windows-x64", {"qemu-system-arm.exe": objdump("pei-x86-64", "i386:x86-64"),
                                                    "libglib-2.0-0.dll": objdump("pei-x86-64", "i386:x86-64")}), "x86-64")

    def assert_fails(self, platform, answers, *named):
        with self.assertRaises(SystemExit) as ctx:
            self.check(platform, answers)
        for name in named:
            self.assertIn(name, str(ctx.exception))

    def test_a_file_of_another_architecture_fails_naming_it(self):
        self.assert_fails("macos-arm64", {"qemu-system-arm": "x86_64\n", "libz.1.dylib": "arm64\n"},
                          "qemu-system-arm (x86_64)")
        self.assert_fails("linux-x64", {"libz.so.1": objdump("elf32-i386", "i386")}, "libz.so.1 (elf32-i386, i386)")
        self.assert_fails("windows-x64", {"qemu-system-arm.exe": objdump("pei-i386", "i386")},
                          "qemu-system-arm.exe (pei-i386, i386)")

    def test_a_universal_file_fails_on_macos(self):
        self.assert_fails("macos-arm64", {"qemu-system-arm": "x86_64 arm64\n"}, "qemu-system-arm (x86_64 arm64)")

    def test_a_linux_file_in_the_windows_archive_fails(self):
        self.assert_fails("windows-x64", {"libz.dll": objdump("elf64-x86-64", "i386:x86-64")},
                          "libz.dll (elf64-x86-64, i386:x86-64)")


class PinTests(unittest.TestCase):
    def test_pins_json_pins_gccs_two_texts(self):
        self.assertEqual(pins_mod.load()["toolchain_licenses"], {"libgcc": {
            "COPYING3": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
            "COPYING.RUNTIME": "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74",
        }})


if __name__ == "__main__":
    unittest.main()
