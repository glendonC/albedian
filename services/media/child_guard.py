"""The guard each guarded call runs under. services/media/guarded_call.py starts it, tells it when to end, and lists
what neither covers; read that first.

The caller starts this file in a new interpreter, in a session of its own (so a process group of its own, whose id is
this process's), with two pipes beside the tool's streams: one it watches and one it reports on. It reports that it
runs, starts the tool in its group with its own standard streams (which the caller set), points its own 0, 1 and 2 at
the null device so only the tool holds its streams, and waits for the first of:
the tool's end, the watched pipe closing, or SIGTERM, SIGINT or SIGHUP sent to it. Then it ends its whole group,
itself included, with SIGKILL; when the tool had ended it first reports the tool's status, and when the tool could not
be started it reports that instead. It ends its group on every way out of its own code, an exception included.
Any other descriptor the caller passed it (a lock, say) stays open in it while it lives; the tool gets none of them.

It signals its group only while it leads it and is alive in it, so the id cannot be anyone else's; a guard started
outside a group of its own ends itself alone. One thread only: the tool's end (SIGCHLD) and the stop signals wake its
one wait through Python's wake-up pipe. Only the standard library: the caller runs it with -I -S.

What it reports, one line each: "guarding" first, then "exit <status>" (the tool's return code as Popen gives it,
negative for a signal), "tool not started <exception name>", or "stopped" (SIGTERM, SIGINT or SIGHUP sent to it).

Usage: python -I -S child_guard.py <watched fd> <report fd> <tool> [arguments ...]
"""

import os
import selectors
import signal
import subprocess
import sys


def end_group(*_: object) -> None:
    """End this process's group, this process included, when this process leads it; else end this process alone, so
    no group but its own is ever signalled."""
    if os.getpgrp() == os.getpid():
        os.killpg(os.getpid(), signal.SIGKILL)
    os.kill(os.getpid(), signal.SIGKILL)


def main() -> None:
    try:
        watched, report = int(sys.argv[1]), int(sys.argv[2])
        woken_read, woken_write = os.pipe()
        os.set_blocking(woken_write, False)
        signal.set_wakeup_fd(woken_write)  # each signal handled here wakes the wait below
        signal.signal(signal.SIGCHLD, lambda *_: None)  # the tool's end is one of them
        os.write(report, b"guarding\n")

        def stop_group(*_: object) -> None:
            try:
                os.write(report, b"stopped\n")
            except OSError:
                pass
            end_group()

        for stop in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(stop, stop_group)
        try:
            tool = subprocess.Popen(sys.argv[3:])  # its standard streams are this process's own, which the caller set
        except OSError as exc:
            os.write(report, f"tool not started {type(exc).__name__}\n".encode())
            return
        null = os.open(os.devnull, os.O_RDWR)
        try:
            for stream in (0, 1, 2):
                os.dup2(null, stream)
        finally:
            os.close(null)
        with selectors.DefaultSelector() as selector:
            selector.register(watched, selectors.EVENT_READ)
            selector.register(woken_read, selectors.EVENT_READ)
            selector.select()
        if tool.poll() is not None:
            os.write(report, f"exit {tool.returncode}\n".encode())
    finally:
        end_group()


if __name__ == "__main__":
    main()
