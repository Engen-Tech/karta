# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
from __future__ import annotations

import os
import pty
import select
import signal
import sys
import time


def main() -> int:
    command = sys.argv[1:]
    if not command:
        print("tui-driver: command required", file=sys.stderr)
        return 2
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(command[0], command, os.environ)
    output = bytearray()
    sent_exit = False
    done_file = os.environ.get("KARTA_TUI_DONE_FILE")
    deadline = time.monotonic() + 30
    status = 1
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.25)
            if readable:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    output.extend(chunk)
                else:
                    break
            if not sent_exit and done_file and os.path.exists(done_file):
                os.write(fd, b"\x04")
                sent_exit = True
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.2)
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == 0:
                os.kill(pid, signal.SIGKILL)
                _, status = os.waitpid(pid, 0)
            print("tui-driver: timeout", file=sys.stderr)
            return 124
        try:
            while chunk := os.read(fd, 65536):
                output.extend(chunk)
        except OSError:
            pass
        if not sent_exit:
            print("tui-driver: completion sentinel was not observed", file=sys.stderr)
            return 1
        return os.waitstatus_to_exitcode(status)
    finally:
        sys.stdout.buffer.write(output)
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
