"""0110-10-11: run the ``gigai`` CLI in a real terminal (a pty), for what only a terminal shows: a prompt.

``CliRunner`` and a plain subprocess have no terminal, so ``sys.stdin.isatty()``
is false and a command that asks only in a terminal never asks: the smoke run
that found ``gigai scout new --no-assess`` waiting at a ``[y/N]`` was a person's
terminal. :func:`run_cli_in_pty` starts the CLI in a fresh process whose stdin,
stdout and stderr are one pty (its controlling terminal), plays ``steps``
(wait for a text, then send keys) and returns the exit code and everything the
terminal showed.

The process is a fresh interpreter (fork, then exec at once): nothing a test
patched in this process reaches it, so use it for runs that call no model.
POSIX only (``pty``); the tests that use it skip elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
import select
import sys
import time

#: What a terminal sends for Ctrl-C (the tty driver turns it into SIGINT) and for Ctrl-D at the start of a line (end of input).
CTRL_C = b"\x03"
CTRL_D = b"\x04"

_CLI = "import sys; from gigai.cli import cli; sys.exit(cli())"


@dataclass(frozen=True)
class PtyRun:
    exit_code: int | None
    output: str
    #: True when the process was still running at the timeout (it was waiting for input, or stuck) and had to be killed.
    timed_out: bool


def _read(master: int, wait: float) -> bytes | None:
    """What the terminal shows within ``wait`` seconds; ``b""`` when nothing came, ``None`` at the end of output."""

    ready, _, _ = select.select([master], [], [], wait)
    if not ready:
        return b""
    try:
        data = os.read(master, 65536)
    except OSError:  # Linux: EIO once the child's side is closed
        return None
    return data or None


def run_cli_in_pty(
    args: Sequence[str], *, steps: Sequence[tuple[str, bytes]] = (), timeout: float = 120.0, env: Mapping[str, str] | None = None,
) -> PtyRun:
    """``gigai <args>`` in a pty. Each step waits until the terminal shows its text, then sends its bytes.

    A step whose text never shows is not sent. The run ends when the process
    ends, or at ``timeout`` (the process is killed and ``timed_out`` is true:
    a command that waits at a prompt nobody answers ends this way).
    """

    import pty

    pid, master = pty.fork()
    if pid == 0:  # the child: its 0, 1 and 2 are the pty, and the pty is its controlling terminal
        try:
            os.execve(sys.executable, [sys.executable, "-c", _CLI, *args], dict(os.environ if env is None else env))
        finally:
            os._exit(127)
    shown = b""
    pending = list(steps)
    deadline = time.monotonic() + timeout
    exit_code: int | None = None
    timed_out = False
    try:
        while True:
            if pending and pending[0][0].encode("utf-8") in shown:
                os.write(master, pending.pop(0)[1])
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            data = _read(master, min(0.2, left))
            if data is None:
                break
            shown += data
            if not data:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    exit_code = os.waitstatus_to_exitcode(status)
                    while True:  # what it wrote last
                        rest = _read(master, 0.2)
                        if not rest:
                            break
                        shown += rest
                    break
        if exit_code is None:
            if timed_out:
                os.kill(pid, 9)
            _, status = os.waitpid(pid, 0)
            exit_code = None if timed_out else os.waitstatus_to_exitcode(status)
    finally:
        os.close(master)
    return PtyRun(exit_code, shown.decode("utf-8", "replace").replace("\r\n", "\n"), timed_out)


__all__ = ["CTRL_C", "CTRL_D", "PtyRun", "run_cli_in_pty"]
