# SPDX-License-Identifier: GPL-2.0-only
"""Offline regressions for the local build driver's input and scratch boundaries.

Run from the repository with Python 3.10 or newer (the producer's floor):
python3 -m unittest discover -s build/tests -v
The real shell drivers run; only compiler, venv/pip and QEMU-build boundaries
are replaced. No downloads or upstream compilation occur. Each subprocess
has a five-second cap, in addition to the caller's gate deadline.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mats-qemu-driver-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def write(self, path, text, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if executable:
            path.chmod(0o755)
        return path

    def fixture(self, dirname="checkout"):
        repo = self.base / dirname
        build = repo / "build"
        build.mkdir(parents=True)
        for name in ("ci-build.sh", "common.sh", "pins.py", "build-libs.sh", "sources-bundle.sh", "collect.py"):
            shutil.copy2(ROOT / "build" / name, build / name)
        shutil.copy2(ROOT / "pins.json", repo / "pins.json")
        work = repo / "work" / "linux-x64"
        tools_bin = self.base / "commands"
        tools_bin.mkdir(exist_ok=True)
        wrapper = self.write(tools_bin / "python3", f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["DRIVER_TOOL_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
if args[:2] == ["-m", "venv"]:
    folder = pathlib.Path(args[2]) / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("python", "python3"):
        (folder / name).symlink_to(pathlib.Path(__file__).resolve())
    meson = folder / "meson"
    meson.write_text("#!/bin/sh\n" + "printf '%s\\n' " + os.environ["DRIVER_MESON_VERSION"] + "\n")
    meson.chmod(0o755)
elif args[:2] == ["-m", "pip"]:
    if not {"--require-hashes", "--only-binary", ":all:"}.issubset(args):
        sys.exit("fixture expected hashed wheel installation")
else:
    os.execv(sys.executable, [sys.executable, *args])
''', executable=True)
        version = json.loads((repo / "pins.json").read_text())["build_tools"]["meson"]["version"]
        env = dict(os.environ, PYTHON=str(wrapper), MATS_QEMU_WORK=str(work),
                   PATH=str(tools_bin) + os.pathsep + os.environ["PATH"],
                   DRIVER_TOOL_LOG=str(self.base / "tool-calls.jsonl"), DRIVER_MESON_VERSION=version,
                   DRIVER_SMOKE_ARGS=str(self.base / "smoke-args.json"))
        # These phases are outside the driver's responsibility and would
        # otherwise fetch/build QEMU. Keep their observable output minimal.
        for phase in ("fetch.sh", "build-libs.sh", "build-qemu.sh"):
            self.write(build / phase, "#!/bin/sh\nset -eu\n", executable=True)
        archive = repo / "dist" / "mats-qemu-test-linux-x64.tar.xz"
        payload = self.write(repo / "payload" / "mats-qemu-test-linux-x64" / "qemu-system-arm", "fixture")
        archive.parent.mkdir(parents=True)
        with tarfile.open(archive, "w:xz") as out:
            out.add(payload.parent, arcname=payload.parent.name)
        self.write(build / "collect.py", "from pathlib import Path\n"
                   "root = Path(__file__).resolve().parents[1]\n"
                   "print(str(root / 'dist' / 'mats-qemu-test-linux-x64.tar.xz') + ' ' + 'a' * 64)\n"
                   "print('stage: fixture')\n")
        self.write(build / "smoke.py", "import json, os, sys\n"
                   "from pathlib import Path\n"
                   "assert (Path(sys.argv[1]) / 'qemu-system-arm').is_file()\n"
                   "Path(os.environ['DRIVER_SMOKE_ARGS']).write_text(json.dumps(sys.argv[1:]))\n")
        return repo, work, env

    def run_driver(self, repo, env, platform="linux-x64", number="1", script="ci-build.sh"):
        args = ["bash", str(repo / "build" / script)]
        if script == "ci-build.sh":
            args.append(platform)
        args.append(number)
        return subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True, timeout=5)

    def test_space_in_checkout_path_reaches_archive_smoke(self):
        repo, _, env = self.fixture("checkout with spaces")
        result = self.run_driver(repo, env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads(Path(env["DRIVER_SMOKE_ARGS"]).read_text())
        self.assertEqual(args[0], str(repo / "work" / "linux-x64" / "unpacked" / "mats-qemu-test-linux-x64"))

    def test_invalid_platform_preserves_tools_without_invoking_python(self):
        repo, work, env = self.fixture()
        sentinel = self.write(work / "tools" / "keep", "existing tools")
        result = self.run_driver(repo, env, platform="linux-typo")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(sentinel.is_file(), "an invalid platform removed the existing tools")
        self.assertFalse(Path(env["DRIVER_TOOL_LOG"]).exists(), "an invalid platform invoked dependency preparation")

    def test_noncanonical_numbers_preserve_tools_without_invoking_python(self):
        repo, work, env = self.fixture()
        for number in ("0", "01", "", "-1", "١"):
            with self.subTest(number=number):
                sentinel = self.write(work / "tools" / "keep", "existing tools")
                Path(env["DRIVER_TOOL_LOG"]).unlink(missing_ok=True)
                result = self.run_driver(repo, env, number=number)
                self.assertNotEqual(result.returncode, 0, "a noncanonical build identity reached smoke")
                self.assertTrue(sentinel.is_file(), "a refused number removed the existing tools")
                self.assertFalse(Path(env["DRIVER_TOOL_LOG"]).exists(), "a refused number invoked dependency preparation")

    def test_source_bundle_rejects_noncanonical_numbers_before_fetching(self):
        repo, _, env = self.fixture()
        for number in ("0", "01"):
            with self.subTest(number=number):
                Path(env["DRIVER_TOOL_LOG"]).unlink(missing_ok=True)
                result = self.run_driver(repo, env, number=number, script="sources-bundle.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(Path(env["DRIVER_TOOL_LOG"]).exists(), "a refused number reached pins/source preparation")
                self.assertFalse((repo / "work" / "bundle").exists())

    def test_standalone_collector_rejects_noncanonical_numbers_as_usage_errors(self):
        repo, work, env = self.fixture()
        shutil.copy2(ROOT / "build" / "collect.py", repo / "build" / "collect.py")
        for number in ("0", "01", "١"):
            with self.subTest(number=number):
                result = subprocess.run([sys.executable, str(repo / "build" / "collect.py"), "linux-x64", number],
                                        cwd=repo, env=env, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("collect.py <platform> <build number>", result.stderr)
                self.assertFalse((work / "stage").exists())

    def test_standalone_collector_rejects_unknown_platform_as_usage_error(self):
        repo, _, env = self.fixture()
        shutil.copy2(ROOT / "build" / "collect.py", repo / "build" / "collect.py")
        result = subprocess.run([sys.executable, str(repo / "build" / "collect.py"), "linux-typo", "1"],
                                cwd=repo, env=env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("collect.py <platform> <build number>", result.stderr)

    def test_fresh_prefix_discards_old_outputs_but_keeps_verified_source_cache(self):
        repo, work, env = self.fixture()
        obsolete = self.write(work / "prefix" / "bin" / "obsolete-runtime.dll", "old build")
        cached = self.write(repo / "sources" / "cached.tar.xz", "verified cache fixture")
        result = self.run_driver(repo, env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(obsolete.exists(), "a new build retained old installed files")
        self.assertEqual(cached.read_text(), "verified cache fixture")

    def test_cmake_components_configure_without_old_compiler_cache(self):
        repo, work, env = self.fixture()
        shutil.copy2(ROOT / "build" / "build-libs.sh", repo / "build" / "build-libs.sh")
        self.write(work / "src" / "libffi" / "configure", "#!/bin/sh\nexit 0\n", executable=True)
        for component in ("zlib", "pcre2"):
            self.write(work / "build" / component / "CMakeCache.txt", "previous compiler")
        commands = Path(env["PATH"].split(os.pathsep)[0])
        env["DRIVER_CMAKE_LOG"] = str(self.base / "cmake-configures.jsonl")
        self.write(commands / "cmake", f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
args = sys.argv[1:]
if "-S" in args:
    name = pathlib.Path(args[args.index("-S") + 1]).name
    folder = pathlib.Path(args[args.index("-B") + 1])
    cache = folder / "CMakeCache.txt"
    with open(os.environ["DRIVER_CMAKE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"component": name, "old_cache": cache.exists()}) + "\n")
    folder.mkdir(parents=True, exist_ok=True)
    cache.write_text("new configure")
''', executable=True)
        for name in ("make", "meson"):
            self.write(commands / name, "#!/bin/sh\nexit 0\n", executable=True)
        result = subprocess.run(["bash", str(repo / "build" / "build-libs.sh"), "linux-x64"],
                                cwd=repo, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(row) for row in Path(env["DRIVER_CMAKE_LOG"]).read_text().splitlines()]
        self.assertEqual(rows, [{"component": "zlib", "old_cache": False}, {"component": "pcre2", "old_cache": False}])


if __name__ == "__main__":
    unittest.main()
