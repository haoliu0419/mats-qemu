# SPDX-License-Identifier: GPL-2.0-only
"""Checks a staged or unpacked archive runs on its own.

  smoke.py <folder holding qemu-system-arm[.exe]> <qemu version>

  - `-version` names the pinned version;
  - `-machine help` lists mps2-an521;
  - `-chardev help` lists the socket and file backends;
  - the mps2-an521 machine starts, paused (-S) on a placeholder image, with
    UART0 a socket server on 127.0.0.1 and UART1 a file, the way the app
    starts it; the socket accepts a connection; and QEMU stops when asked.
Run from the folder alone, with no build prefix on any search path, so a
library the archive lacks fails here.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time


def fail(msg):
    sys.exit(f"smoke: {msg}")


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    folder, version = argv[1], argv[2]
    exe = os.path.join(folder, "qemu-system-arm.exe" if os.name == "nt" else "qemu-system-arm")
    if not os.path.exists(exe):
        fail(f"no {exe}")
    env = {k: v for k, v in os.environ.items() if k not in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH")}
    if os.name == "nt":
        # Windows also loads DLLs from PATH, which in an MSYS2 shell holds
        # MSYS2's own glib: only the system folders stay.
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        env["PATH"] = os.pathsep.join([os.path.join(root, "System32"), root])

    def out(*args):
        r = subprocess.run([exe, *args], capture_output=True, text=True, env=env, timeout=30)
        if r.returncode != 0:
            fail(f"{' '.join(args)} exited {r.returncode}: {r.stderr.strip()}")
        return r.stdout

    if f"version {version}" not in out("-version"):
        fail(f"-version does not name {version}")
    if "mps2-an521" not in out("-machine", "help").split():
        fail("-machine help does not list mps2-an521")
    # QEMU's arm target has no default machine, and lists backends only for one.
    backends = out("-M", "mps2-an521", "-chardev", "help").split()
    for b in ("socket", "file"):
        if b not in backends:
            fail(f"-chardev help does not list {b}")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "placeholder.bin")
        with open(image, "wb") as f:
            f.write(bytes(1024))
        uart1 = os.path.join(tmp, "uart1.bin")
        p = subprocess.Popen([exe, "-M", "mps2-an521", "-S", "-nographic", "-monitor", "none",
                              "-kernel", image,
                              "-serial", f"tcp:127.0.0.1:{port},server=on,wait=off",
                              "-serial", f"file:{uart1}"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        try:
            deadline = time.time() + 10
            while True:
                if p.poll() is not None:
                    fail(f"QEMU exited {p.returncode} at start: {p.stdout.read().decode(errors='replace').strip()}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    if time.time() > deadline:
                        fail(f"nothing listened on 127.0.0.1:{port} within 10 s")
                    time.sleep(0.2)
        finally:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
    print(f"smoke: {exe} {version}: mps2-an521 starts, UART0 listens on 127.0.0.1, it stops when asked")


if __name__ == "__main__":
    main(sys.argv)
