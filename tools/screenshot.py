#!/usr/bin/env python3
"""Run a PRG in xemu-xmega65 headless and save a screenshot.

Usage: screenshot.py <prg> <png> [seconds]

Waits (through xemu's UART monitor socket) until the PRG's bytes are in
memory, lets it run for the given number of seconds (default 8), then stops
xemu, which writes its -screenshot on exit (and with DUMPMEM=<file> in the
environment, a memory dump). Based on the M65Compiler's
examples/capture_screens.py.
"""
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time


def monitor(sock, cmd):
    sock.sendall((cmd + "\r").encode())
    data = b""
    end = time.time() + 2
    while time.time() < end:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
        if data.rstrip().endswith(b"."):
            break
    return data.decode(errors="replace")


def loaded(sock, addr, want):
    for line in monitor(sock, "m%x" % addr).splitlines():
        line = line.strip()
        if line.startswith(":"):
            parts = line[1:].split(":", 1)
            if len(parts) == 2 and int(parts[0], 16) == addr:
                try:
                    return bytes.fromhex(parts[1].strip()[:2 * len(want)]) == want
                except ValueError:
                    return False
    return False


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    prg, png = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    delay = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else 8.0
    data = open(prg, "rb").read()
    addr = data[0] | data[1] << 8
    want = data[2:18]
    if os.path.exists(png):
        os.remove(png)
    tmp = tempfile.mkdtemp()
    sockname = os.path.join(tmp, "umon")
    xemu = subprocess.Popen(
        [os.environ.get("XEMU", "xemu-xmega65"), "-headless", "-besure", "-fastboot"]
        + (["-8", os.environ["DISK"]] if os.environ.get("DISK") else [])
        + [
         "-prgmode", "65", "-uartmon", sockname, "-screenshot", png, "-prg", prg]
        + (["-dumpmem", os.environ["DUMPMEM"]] if os.environ.get("DUMPMEM") else []),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        sock = None
        deadline = time.time() + 30
        while time.time() < deadline and sock is None:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(sockname)
                s.settimeout(0.3)
                sock = s
            except OSError:
                time.sleep(0.1)
        if sock is None:
            sys.exit("no xemu monitor")
        started = None
        while time.time() < deadline:
            if loaded(sock, addr, want):
                started = time.time()
                break
            time.sleep(0.05)
        if started is None:
            sys.exit("the program never appeared at $%04X" % addr)
        time.sleep(max(0, started + delay - time.time()))
        sock.close()
        xemu.send_signal(signal.SIGTERM)
        xemu.wait(timeout=15)
    finally:
        if xemu.poll() is None:
            xemu.kill()
        try:
            os.remove(sockname)
            os.rmdir(tmp)
        except OSError:
            pass
    if not (os.path.exists(png) and os.path.getsize(png) > 0):
        sys.exit("no screenshot")
    print(png)


if __name__ == "__main__":
    main()
