"""Subprocess execution with live, credential-redacted diagnostics."""

import os
import re
import shlex
import signal
import subprocess
from collections import deque
from pathlib import Path
from typing import Callable

from .models import BuildError


def redact(text: str) -> str:
    text = re.sub(r"(?i)(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[REDACTED]@", text)
    return re.sub(r"(?i)((?:password|passwd|token|secret|authorization)\s*[:=]\s*)[^\s]+", r"\1[REDACTED]", text)


class Runner:
    def __init__(self, log: Path, emit_log: Callable[[str], None] | None = None):
        self.log_path = log
        self.log = log.open("a", encoding="utf-8")
        self.emit_log = emit_log

    def write(self, line: str) -> None:
        clean = redact(line)
        self.log.write(clean)
        self.log.flush()
        if self.emit_log:
            self.emit_log(clean.rstrip("\n"))

    def run(self, args: list[str], *, check: bool = True, capture: bool = False,
            on_line: Callable[[str], None] | None = None) -> str:
        self.write(f"$ {shlex.join(args)}\n")
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, errors="replace", start_new_session=True)
        except OSError as exc:
            raise BuildError(f"Cannot start {args[0]}: {exc}") from exc
        tail: deque[str] = deque(maxlen=20)
        output = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self.write(line)
                if on_line:
                    on_line(line)
                tail.append(redact(line))
                if capture:
                    output.append(line)
            code = process.wait()
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            if process.stdout:
                process.stdout.close()
        if check and code:
            raise BuildError(f"{args[0]} failed (exit {code}).\n{''.join(tail).strip()}")
        return "".join(output)

    def close(self) -> None:
        self.log.close()
