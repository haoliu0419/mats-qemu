# SPDX-License-Identifier: GPL-2.0-only
"""Stages one platform's archive from the build prefix and checks it.

  collect.py <platform> <build number>

Walks qemu-system-arm's shared-library dependencies, and theirs, and copies
each one the build made into the stage beside the executable. Every other
dependency must be the operating system's own: one found anywhere else, or
a system copy of a library this build makes (zlib, libffi, pcre2, libintl,
glib), fails the stage by name, since its source would not be in the
sources bundle. Then:
  - on macOS, every reference to the prefix becomes @loader_path/<name>
    and each file is signed ad hoc again (the app signs them for release),
    and a file whose minimum macOS is above pins.json's macos_minimum, or
    that names none, fails the stage;
  - on Linux, each file's RUNPATH becomes $ORIGIN;
  - on Windows, the DLLs already resolve beside the executable, every DLL
    that is neither Windows' nor the prefix's is named, for every file
    that imports one, before the stage fails, and winpthreads, which
    build-qemu.sh links into the executable, gets its licence and its
    MSYS2 package's version;
and the stage gains licenses/<component>/, NOTICE and manifest.json, and is
archived as dist/mats-qemu-<version>-<n>-<platform>.tar.xz (.zip on
Windows). Prints the archive's path and sha256.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pins as pins_mod  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Libraries this build makes: a system copy of one is refused, so the
# shipped bytes are always the ones built from the pinned source.
OWN = re.compile(r"(^|/)(lib)?(z|zlib1?|ffi|pcre2-8|intl|glib-2\.0|gio-2\.0|gobject-2\.0|gmodule-2\.0|gthread-2\.0)([-.]|$)",
                 re.IGNORECASE)

LINUX_SYSTEM = {
    "libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1",
    "libresolv.so.2", "ld-linux-x86-64.so.2", "libgcc_s.so.1",
}


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fail(msg):
    sys.exit(f"collect: {msg}")


class Platform:
    def __init__(self, name, prefix):
        self.name = name
        self.prefix = prefix
        # Windows: each import that is neither Windows' nor the prefix's.
        self.foreign = []
        self.exe = "qemu-system-arm.exe" if name == "windows-x64" else "qemu-system-arm"

    def executable(self):
        # bin/, or the prefix itself where a Windows install puts it.
        for folder in (os.path.join(self.prefix, "bin"), self.prefix):
            if os.path.exists(os.path.join(folder, self.exe)):
                return os.path.join(folder, self.exe)
        return os.path.join(self.prefix, "bin", self.exe)

    # Returns (bundled, system): the dependencies of `path` the prefix holds,
    # as paths, and the system's, as names. Fails on any other.
    def deps(self, path):
        if self.name == "macos-arm64":
            return self.deps_macos(path)
        if self.name == "linux-x64":
            return self.deps_linux(path)
        return self.deps_windows(path)

    def deps_macos(self, path):
        own_id = run("otool", "-D", path).splitlines()[1:]
        bundled, system = [], []
        for line in run("otool", "-L", path).splitlines()[1:]:
            ref = line.strip().split(" (")[0]
            if ref in own_id:
                continue
            if ref.startswith("@rpath/") or ref.startswith("@loader_path/"):
                local = os.path.join(self.prefix, "lib", os.path.basename(ref))
                if not os.path.exists(local):
                    fail(f"{path} needs {ref}, which the prefix does not hold")
                bundled.append(local)
            elif ref.startswith(self.prefix + "/"):
                # Kept under the name it is referenced by; the copy follows
                # a symlink to the versioned file.
                bundled.append(ref)
            elif ref.startswith("/usr/lib/") or ref.startswith("/System/Library/"):
                if OWN.search(os.path.basename(ref)):
                    fail(f"{path} links the system's {ref}, not the one this build makes")
                system.append(ref)
            else:
                fail(f"{path} links {ref}, outside the system and the prefix")
        return bundled, system

    def deps_linux(self, path):
        bundled, system = [], []
        for line in run("readelf", "-d", path).splitlines():
            m = re.search(r"\(NEEDED\)\s+Shared library: \[(.+)\]", line)
            if not m:
                continue
            name = m.group(1)
            local = os.path.join(self.prefix, "lib", name)
            if os.path.exists(local):
                bundled.append(local)
            elif name in LINUX_SYSTEM:
                system.append(name)
            else:
                fail(f"{path} needs {name}, neither glibc's nor the prefix's")
        return bundled, system

    def deps_windows(self, path):
        sysdir = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")
        bundled, system = [], []
        for line in run("objdump", "-p", path).splitlines():
            m = re.search(r"DLL Name: (\S+)", line)
            if not m:
                continue
            name = m.group(1)
            local = os.path.join(self.prefix, "bin", name)
            if os.path.exists(local):
                bundled.append(local)
            elif name.lower().startswith("api-ms-win-") or os.path.exists(os.path.join(sysdir, name)):
                if OWN.search(name):
                    fail(f"{path} links the system's {name}, not the one this build makes")
                system.append(name)
            else:
                self.foreign.append(f"{os.path.basename(path)} imports {name}")
        return bundled, system


def closure(plat):
    todo = [plat.executable()]
    seen, system = {}, set()
    while todo:
        path = todo.pop()
        name = os.path.basename(path)
        if name in seen:
            continue
        seen[name] = path
        bundled, sys_deps = plat.deps(path)
        system.update(sys_deps)
        todo.extend(bundled)
    if plat.foreign:
        fail("DLLs neither Windows' nor the prefix's (MSYS2 runtime DLLs?): " + "; ".join(sorted(plat.foreign)))
    return seen, sorted(system)


def windows_static_runtime(stage):
    """winpthreads, linked into qemu-system-arm.exe (build-qemu.sh): the
    MSYS2 package that holds the archive it was linked from, its version
    and licence, and its licence files copied into licenses/winpthreads/."""
    lib = os.path.normpath(run("gcc", "-print-file-name=libwinpthread.a").strip())
    if not os.path.isfile(lib):
        fail(f"gcc names no libwinpthread.a ({lib})")
    package = run("pacman", "-Qqo", run("cygpath", "-u", lib).strip()).strip()
    version = run("pacman", "-Q", package).split()[1]
    licence = None
    for line in run("pacman", "-Qi", package).splitlines():
        if line.startswith("Licenses"):
            licence = line.split(":", 1)[1].strip()
    files = [f for f in run("pacman", "-Qlq", package).splitlines() if "/share/licenses/" in f and not f.endswith("/")]
    if not licence or not files:
        fail(f"{package} names no licence, or holds no licence file")
    dest = os.path.join(stage, "licenses", "winpthreads")
    os.makedirs(dest, exist_ok=True)
    for f in files:
        shutil.copy2(run("cygpath", "-m", f).strip(), os.path.join(dest, os.path.basename(f)))
    return {"winpthreads": {"package": package, "version": version, "license": licence,
                            "linked": "statically, into qemu-system-arm.exe"}}


def rewrite_macos(stage, files, prefix):
    for name in files:
        path = os.path.join(stage, name)
        os.chmod(path, 0o755)
        if name.endswith(".dylib"):
            run("install_name_tool", "-id", f"@loader_path/{name}", path)
        for line in run("otool", "-L", path).splitlines()[1:]:
            ref = line.strip().split(" (")[0]
            if ref.startswith(prefix + "/") or ref.startswith("@rpath/"):
                run("install_name_tool", "-change", ref, f"@loader_path/{os.path.basename(ref)}", path)
        run("codesign", "--force", "--sign", "-", path)
    for name in files:
        left = [l for l in run("otool", "-L", os.path.join(stage, name)).splitlines()[1:] if prefix in l]
        if left:
            fail(f"{name} still names the prefix: {left}")


def check_no_weak_imports(stage, files):
    """No shipped file may import a system function weakly: one that is
    weak there is newer than the macOS minimum, and NULL on an older one."""
    bundled = {os.path.splitext(f)[0] for f in files}
    for name in files:
        weak = []
        for line in run("nm", "-m", os.path.join(stage, name)).splitlines():
            m = re.search(r"\(undefined\) weak external \S+ \(from (\S+)\)", line)
            if m and m.group(1) not in bundled:
                weak.append(line.strip())
        if weak:
            fail(f"{name} imports weakly, so it needs a newer macOS than the minimum: {weak}")


def version_tuple(text):
    return tuple(int(x) for x in text.split("."))


def check_macos_minimum(stage, files, floor):
    """Each file's minimum macOS, from its LC_BUILD_VERSION (minos) or
    LC_VERSION_MIN_MACOSX (version) load command, must be at most `floor`."""
    for name in files:
        lines = [l.strip() for l in run("otool", "-l", os.path.join(stage, name)).splitlines()]
        found = None
        for i, line in enumerate(lines):
            if line == "cmd LC_BUILD_VERSION" or line == "cmd LC_VERSION_MIN_MACOSX":
                key = "minos" if line.endswith("LC_BUILD_VERSION") else "version"
                for follow in lines[i + 1:i + 6]:
                    if follow.startswith(key + " "):
                        found = follow.split()[1]
                        break
        if found is None:
            fail(f"{name} names no minimum macOS")
        if version_tuple(found) > version_tuple(floor):
            fail(f"{name} needs macOS {found}, above the archive's minimum {floor}")


def rewrite_linux(stage, files):
    for name in files:
        path = os.path.join(stage, name)
        os.chmod(path, 0o755)
        run("patchelf", "--set-rpath", "$ORIGIN", path)


def licenses(stage, plat):
    out = os.path.join(stage, "licenses")
    for comp in pins_mod.BUILD_ORDER:
        info = PINS["components"][comp]
        if plat.name not in info["platforms"]:
            continue
        dest = os.path.join(out, comp)
        os.makedirs(dest, exist_ok=True)
        src = os.path.join(WORK, "src", comp)
        for lic in info["license_files"]:
            if not os.path.exists(os.path.join(src, lic)):
                fail(f"{comp}'s tarball has no {lic}")
            shutil.copy2(os.path.join(src, lic), os.path.join(dest, lic))


def write_notice(stage, plat, version, build_number, static_runtime):
    """The repository's NOTICE, then this archive's components at their
    exact versions and the release and bundle that hold their source, and
    any toolchain library linked in statically."""
    with open(os.path.join(ROOT, "NOTICE"), encoding="utf-8") as f:
        text = f.read().rstrip() + "\n\n"
    tag = f"qemu-{version}-{build_number}"
    text += f"This archive: {plat.name}, release {tag}.\n"
    for comp in pins_mod.BUILD_ORDER:
        info = PINS["components"][comp]
        if plat.name in info["platforms"]:
            text += f"  {comp} {info['version']}, {info['license']}, from {info['urls'][0]}\n"
    for name, info in static_runtime.items():
        text += (f"  {name} {info['version']} (MSYS2's {info['package']}), {info['license']},\n"
                 f"    linked {info['linked']}; its licence is in licenses/{name}/\n")
    text += (f"Its source: mats-qemu-{version}-{build_number}-sources.tar.xz at\n"
             f"https://github.com/haoliu0419/mats-qemu/releases/tag/{tag}\n")
    with open(os.path.join(stage, "NOTICE"), "w", encoding="utf-8") as f:
        f.write(text)


def archive(stage, base, plat):
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    top = os.path.basename(stage)
    if plat.name == "windows-x64":
        out = os.path.join(dist, base + ".zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for d, _, fs in os.walk(stage):
                for f in sorted(fs):
                    full = os.path.join(d, f)
                    z.write(full, os.path.join(top, os.path.relpath(full, stage)))
    else:
        out = os.path.join(dist, base + ".tar.xz")
        with tarfile.open(out, "w:xz") as t:
            t.add(stage, arcname=top)
    return out


def fetched_sources(platform):
    """Each component's tarball, its sha256 and the URL download.sh fetched
    it from (sources/<tarball>.url)."""
    found = {}
    for comp in pins_mod.BUILD_ORDER:
        info = PINS["components"][comp]
        if platform not in info["platforms"]:
            continue
        tarball = pins_mod.tarball_name(info)
        try:
            with open(os.path.join(ROOT, "sources", tarball + ".url"), encoding="utf-8") as f:
                url = f.read().strip()
        except FileNotFoundError:
            fail(f"sources/{tarball}.url is missing: run fetch.sh first")
        found[comp] = {"tarball": tarball, "sha256": info["sha256"], "url": url}
    return found


def applied_patches(platform):
    """Each patch fetch.sh applied for this platform's components, by its
    path under patches/, with its sha256."""
    found = {}
    for comp in pins_mod.BUILD_ORDER:
        if platform not in PINS["components"][comp]["platforms"]:
            continue
        folder = os.path.join(ROOT, "patches", comp)
        if not os.path.isdir(folder):
            continue
        for name in sorted(f for f in os.listdir(folder) if f.endswith(".patch")):
            found[f"{comp}/{name}"] = sha256_of(os.path.join(folder, name))
    return found


def tool_versions(platform):
    """The build tools' versions: meson is pinned, Python is the one
    ci-build.sh checked (this script runs under it), and the rest come with
    the platform (Xcode's clang, Ubuntu's GCC, MSYS2's GCC)."""
    cc = "gcc" if platform == "windows-x64" else "cc"
    import platform as py_platform
    import importlib.metadata
    found = {"python": py_platform.python_version(),
             "setuptools": importlib.metadata.version("setuptools")}
    for name, args in (("meson", ["meson", "--version"]), ("ninja", ["ninja", "--version"]),
                       ("cmake", ["cmake", "--version"]), ("cc", [cc, "--version"])):
        found[name] = run(*args).splitlines()[0].strip()
    return found


def main(argv):
    global PINS, WORK
    if len(argv) != 3 or not argv[2].isdigit():
        sys.exit(__doc__)
    platform, build_number = argv[1], argv[2]
    PINS = pins_mod.load()
    WORK = os.environ.get("MATS_QEMU_WORK", os.path.join(ROOT, "work", platform))
    if platform == "windows-x64":
        # MSYS2's shell may hand a POSIX path; this Python reads Windows ones.
        WORK = run("cygpath", "-m", WORK).strip()
    prefix = os.path.join(WORK, "prefix").replace("\\", "/")
    plat = Platform(platform, prefix)
    if not os.path.exists(plat.executable()):
        fail(f"no {plat.executable()}: run build-qemu.sh first")

    version = PINS["components"]["qemu"]["version"]
    base = f"mats-qemu-{version}-{build_number}-{platform}"
    stage = os.path.join(WORK, "stage", base)
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)

    files, system = closure(plat)
    for name, path in files.items():
        shutil.copy2(path, os.path.join(stage, name), follow_symlinks=True)
    if platform == "macos-arm64":
        rewrite_macos(stage, sorted(files), prefix)
        check_macos_minimum(stage, sorted(files), PINS["macos_minimum"])
        check_no_weak_imports(stage, sorted(files))
    elif platform == "linux-x64":
        rewrite_linux(stage, sorted(files))

    licenses(stage, plat)
    static_runtime = windows_static_runtime(stage) if platform == "windows-x64" else {}
    write_notice(stage, plat, version, build_number, static_runtime)
    with open(os.path.join(WORK, "configure-line.txt"), encoding="utf-8") as f:
        configure = f.read().strip()
    manifest = {
        "qemu": version,
        "build": int(build_number),
        "platform": platform,
        "configure": configure,
        "components": {c: PINS["components"][c]["version"] for c in pins_mod.BUILD_ORDER
                       if platform in PINS["components"][c]["platforms"]},
        "build_tools": tool_versions(platform),
        "sources": fetched_sources(platform),
        **({"static_runtime": static_runtime} if static_runtime else {}),
        "patches": applied_patches(platform),
        # The macOS minimum, and the SDK the build compiled against, which
        # decides what a configure check could find.
        **({"macos_minimum": PINS["macos_minimum"], "macos_sdk": run("xcrun", "--show-sdk-version").strip()}
           if platform == "macos-arm64" else {}),
        "system_libraries": system,
        "files": {name: sha256_of(os.path.join(stage, name)) for name in sorted(files)},
    }
    with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    out = archive(stage, base, plat)
    print(f"{out} {sha256_of(out)}")
    print(f"stage: {stage}")


if __name__ == "__main__":
    main(sys.argv)
