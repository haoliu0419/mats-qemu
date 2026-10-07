# SPDX-License-Identifier: GPL-2.0-only
"""Stages one platform's archive from the build prefix and checks it.

  collect.py <platform> <build number>

Walks qemu-system-arm's shared-library dependencies, and theirs, and copies
each one the build made into the stage beside the executable. Every other
dependency must be the operating system's own: one found anywhere else, or
a system copy of a library this build makes (zlib, libffi, pcre2, libintl,
glib), fails the stage by name, since its source would not be in the
sources bundle. Then each staged file is stripped of its debug information
and local symbols, which the archive has no use for and which are not kept
anywhere (`strip -S -x` on macOS, `strip --strip-unneeded` elsewhere; a
file with a debugging symbol left on macOS, or a .debug section on Linux and
Windows, fails the stage), and:
  - on macOS, every reference to the prefix becomes @loader_path/<name>
    and each file is signed ad hoc again (the app signs them for release),
    and a file whose minimum macOS is above pins.json's macos_minimum, or
    that names none, fails the stage;
  - on Linux, each file's RUNPATH becomes $ORIGIN;
  - on every platform, a file built for another architecture than the
    platform's (lipo on macOS, objdump elsewhere) fails the stage;
  - on Windows, the DLLs already resolve beside the executable (winpthreads'
    among them, which fetch.sh put in the prefix from its pinned package),
    every DLL that is neither Windows' nor the prefix's is named, for every
    file that imports one, before the stage fails, a compiler or linker
    flag that loads a GCC plugin fails it, and GCC's runtime library,
    linked into each file, is recorded with GCC's version and the MSYS2
    package that installed it, its two licence texts taken from that
    package and checked against pins.json;
and the stage gains licenses/<component>/, NOTICE and manifest.json, and is
archived as dist/mats-qemu-<version>-<n>-<platform>.tar.xz (.zip on
Windows), whose licence texts for GCC's runtime are checked again as
written. Prints the archive's path and sha256.
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
OWN = re.compile(r"(^|/)(lib)?(z|zlib1?|ffi|pcre2-8|intl|winpthread|glib-2\.0|gio-2\.0|gobject-2\.0|gmodule-2\.0|gthread-2\.0)([-.]|$)",
                 re.IGNORECASE)

LINUX_SYSTEM = {
    "libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1",
    "libresolv.so.2", "ld-linux-x86-64.so.2", "libgcc_s.so.1",
}


def run(*args):
    """`args`' standard output. A command that cannot start, or exits other
    than 0, fails the stage naming it with its exit code and its standard
    error, so the cause is in the log."""
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        fail(f"{args[0]} was not found")
    except subprocess.CalledProcessError as e:
        fail(f"{' '.join(str(a) for a in args)} exited {e.returncode}: {(e.stderr or '').strip() or 'nothing on stderr'}")


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


# The architecture every staged file is built for, by platform.
ARCHITECTURES = {"macos-arm64": "arm64", "linux-x64": "x86-64", "windows-x64": "x86-64"}


def check_architecture(stage, files, platform, run_cmd=None):
    """Every staged file is built for the platform's architecture: on macOS
    lipo names exactly arm64 (a universal file fails too); elsewhere objdump
    names the 64-bit x86 file format (elf64-x86-64, or pei-x86-64 on
    Windows) and architecture i386:x86-64. A file of another one fails the
    stage, each named. Returns the architecture, for the manifest."""
    run_cmd = run_cmd or run
    wrong = []
    for name in files:
        path = os.path.join(stage, name)
        if platform == "macos-arm64":
            seen = " ".join(run_cmd("lipo", "-archs", path).split())
            ok = seen == "arm64"
        else:
            out = run_cmd("objdump", "-f", path)
            fmt = re.search(r"file format (\S+)", out)
            arch = re.search(r"architecture: ([^,\s]+)", out)
            seen = f"{fmt.group(1) if fmt else 'no file format'}, {arch.group(1) if arch else 'no architecture'}"
            want = "pei-x86-64" if platform == "windows-x64" else "elf64-x86-64"
            ok = bool(fmt and arch) and fmt.group(1) == want and arch.group(1) == "i386:x86-64"
        if not ok:
            wrong.append(f"{name} ({seen})")
    if wrong:
        fail(f"files not built for {platform}: " + "; ".join(wrong))
    return ARCHITECTURES[platform]


def windows_build_flags(work):
    """The compiler and linker flags the Windows build ran with, as
    build-libs.sh and build-qemu.sh recorded them under `work`
    (common.sh record_flags), by where: {"build-libs CFLAGS": ..., ...}. A
    record that is missing fails the stage by name."""
    flags = {}
    for name in ("libs", "qemu"):
        path = os.path.join(work, f"build-flags-{name}.txt")
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            fail(f"{path} is missing: build-{name}.sh records the flags it builds with there")
        for line in lines:
            key, sep, value = line.partition("=")
            if sep:
                flags[f"build-{name} {key}"] = value
    return flags


def check_no_plugins(flags):
    """GCC loads a plugin only when a flag names one (-fplugin=...). The
    NOTICE says the Windows archive is built by GCC with no plugins, the
    GCC Runtime Library Exception's Eligible Compilation Process, so any
    flag that loads one, in `flags` ({where: flags}), fails the stage."""
    found = [f"{where}: {flag}" for where, text in flags.items() for flag in text.split() if "-fplugin" in flag]
    if found:
        fail("a flag loads a GCC plugin, which the NOTICE says the Windows build does not: " + "; ".join(found))


def windows_toolchain_libraries(stage, run_cmd=None):
    """GCC's runtime library, which -static-libgcc links into each file of the
    Windows archive: GCC's version, the MSYS2 package that installed the
    libgcc the build linked, and the exception it ships under. Its source
    is not bundled, which the exception does not require. Its licence
    texts, GCC's COPYING3 (GPL-3.0) and COPYING.RUNTIME (the GCC Runtime
    Library Exception 3.1), are taken from that package into
    licenses/libgcc/, each checked against the sha256 pacman recorded for
    it and the one pins.json pins (toolchain_licenses), so a changed text
    fails the stage by name. Called once check_no_plugins has passed.
    It reads pacman's database through msys2_db.py, which only the Windows
    build needs, so collect.py runs without it elsewhere."""
    import msys2_db
    run_cmd = run_cmd or run
    libgcc = run_cmd("cygpath", "-u", run_cmd("gcc", "-print-libgcc-file-name").strip()).strip()
    package, version = msys2_db.owner(libgcc, run_cmd)
    files = msys2_db.package_files(package, version, run_cmd)
    if files is None:
        fail(f"{package} {version}, which installed {libgcc}, has no record in pacman's database")
    dest = os.path.join(stage, "licenses", "libgcc")
    os.makedirs(dest, exist_ok=True)
    taken = {}
    for name, want in PINS["toolchain_licenses"]["libgcc"].items():
        path = next((p for p in sorted(files) if p.split("/")[1:] == ["share", "licenses", "gcc", name]), None)
        if path is None:
            fail(f"{package} {version} ships no share/licenses/gcc/{name}")
        source = msys2_db.native("/" + path, run_cmd)
        got = msys2_db.sha256_of(source)
        if got != files[path].get("sha256digest"):
            fail(f"{path} differs from the sha256 pacman recorded for {package} {version}")
        if got != want:
            fail(f"{path} is not the text pins.json pins (toolchain_licenses): its sha256 is {got}, the pin {want}")
        shutil.copy2(source, os.path.join(dest, name))
        taken[name] = got
    return {"libgcc": {"version": run_cmd("gcc", "--version").splitlines()[0].strip(),
                       "gcc": run_cmd("gcc", "-dumpfullversion").strip(),
                       "package": package,
                       "package_version": version,
                       "file": libgcc,
                       "license": "GPL-3.0-or-later WITH GCC-exception-3.1",
                       "license_files": taken,
                       "plugins": "none",
                       "linked": "statically, into each file (-static-libgcc)"}}


def check_archive_licences(out, top, toolchain):
    """The Windows archive as written holds each toolchain library's licence
    texts under <top>/licenses/<library>/, at the sha256 the manifest
    records and pins.json pins."""
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        for lib, info in toolchain.items():
            for name, want in info["license_files"].items():
                member = f"{top}/licenses/{lib}/{name}"
                if member not in names:
                    fail(f"{os.path.basename(out)} has no {member}")
                got = hashlib.sha256(z.read(member)).hexdigest()
                if got != want or got != PINS["toolchain_licenses"][lib][name]:
                    fail(f"{os.path.basename(out)}'s {member} has sha256 {got}, not the pinned text's")


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


def strip_files(stage, files, platform):
    """Strips each staged file in place, before the macOS rewrite re-signs
    it and before patchelf; returns how, for the manifest. A shared library
    keeps the symbols it exports: -x and --strip-unneeded drop only what
    nothing links against. On macOS -S drops the debugging symbols, which
    point the debugger at the build machine's object files."""
    command = ["strip", "-S", "-x"] if platform == "macos-arm64" else ["strip", "--strip-unneeded"]
    for name in files:
        path = os.path.join(stage, name)
        os.chmod(path, 0o755)
        run(*command, path)
    how = {"command": " ".join(command)}
    if platform == "macos-arm64":
        for name in files:
            # nm -a marks a debugging symbol with "-" and names its kind
            # (SO, OSO, FUN...) before the name. OPT is the marker Apple's
            # linker puts in every image and strip keeps; it names no file.
            kinds = re.findall(r"^\S* +- \S+ \S+ +(\S+)", run("nm", "-a", os.path.join(stage, name)), re.M)
            if [k for k in kinds if k != "OPT"]:
                fail(f"{name} still has debugging symbols after {how['command']}: {sorted(set(kinds))}")
    else:
        how["version"] = run("strip", "--version").splitlines()[0].strip()
        for name in files:
            if ".debug" in run("objdump", "-h", os.path.join(stage, name)):
                fail(f"{name} still has a .debug section after {how['command']}")
    return how


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


def write_notice(stage, plat, version, build_number, toolchain):
    """The repository's NOTICE, then this archive's components at their
    exact versions and the release and bundle that hold their source, and
    the toolchain's own libraries linked in."""
    with open(os.path.join(ROOT, "NOTICE"), encoding="utf-8") as f:
        text = f.read().rstrip() + "\n\n"
    tag = f"qemu-{version}-{build_number}"
    text += f"This archive: {plat.name}, release {tag}.\n"
    for comp in pins_mod.BUILD_ORDER:
        info = PINS["components"][comp]
        if plat.name in info["platforms"]:
            text += f"  {comp} {info['version']}, {info['license']}, from {info['urls'][0]}\n"
    for name, info in toolchain.items():
        text += (f"  {name} (gcc {info['gcc']}, {info['package']} {info['package_version']}), {info['license']},"
                 f" linked {info['linked']}\n")
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


def fetched_url(name):
    """The URL download.sh fetched sources/<name> from (<name>.url)."""
    try:
        with open(os.path.join(ROOT, "sources", name + ".url"), encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        fail(f"sources/{name}.url is missing: run fetch.sh first")


def fetched_sources(platform):
    """Each component's tarball, its sha256 and the URL download.sh fetched
    it from, and for an msys2 component each binary package installed, the
    same way."""
    found = {}
    for comp in pins_mod.BUILD_ORDER:
        info = PINS["components"][comp]
        if platform not in info["platforms"]:
            continue
        tarball = pins_mod.tarball_name(info)
        found[comp] = {"tarball": tarball, "sha256": info["sha256"], "url": fetched_url(tarball)}
        packages = {}
        for pkg, pinfo in info.get("packages", {}).items():
            file = pinfo["urls"][0].rsplit("/", 1)[1]
            packages[pkg] = {"file": file, "sha256": pinfo["sha256"], "url": fetched_url(file)}
        if packages:
            found[comp]["packages"] = packages
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
    if (len(argv) != 3 or argv[1] not in ("macos-arm64", "windows-x64", "linux-x64")
            or not re.fullmatch(r"[1-9][0-9]*", argv[2])):
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
    stripped = strip_files(stage, sorted(files), platform)
    if platform == "macos-arm64":
        rewrite_macos(stage, sorted(files), prefix)
        check_macos_minimum(stage, sorted(files), PINS["macos_minimum"])
        check_no_weak_imports(stage, sorted(files))
    elif platform == "linux-x64":
        rewrite_linux(stage, sorted(files))
    architecture = check_architecture(stage, sorted(files), platform)

    with open(os.path.join(WORK, "configure-line.txt"), encoding="utf-8") as f:
        configure = f.read().strip()
    licenses(stage, plat)
    toolchain = {}
    if platform == "windows-x64":
        check_no_plugins({**windows_build_flags(WORK), "configure": configure})
        toolchain = windows_toolchain_libraries(stage)
    write_notice(stage, plat, version, build_number, toolchain)
    manifest = {
        "qemu": version,
        "build": int(build_number),
        "platform": platform,
        "architecture": architecture,
        "configure": configure,
        "components": {c: PINS["components"][c]["version"] for c in pins_mod.BUILD_ORDER
                       if platform in PINS["components"][c]["platforms"]},
        "build_tools": tool_versions(platform),
        "sources": fetched_sources(platform),
        **({"toolchain_libraries": toolchain} if toolchain else {}),
        "patches": applied_patches(platform),
        # The macOS minimum, and the SDK the build compiled against, which
        # decides what a configure check could find.
        **({"macos_minimum": PINS["macos_minimum"], "macos_sdk": run("xcrun", "--show-sdk-version").strip()}
           if platform == "macos-arm64" else {}),
        "stripped": stripped,
        "system_libraries": system,
        "files": {name: sha256_of(os.path.join(stage, name)) for name in sorted(files)},
    }
    with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    out = archive(stage, base, plat)
    if toolchain:
        check_archive_licences(out, base, toolchain)
    print(f"{out} {sha256_of(out)}")
    print(f"stage: {stage}")


if __name__ == "__main__":
    main(sys.argv)
