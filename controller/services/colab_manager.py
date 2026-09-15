"""The ONLY module in this codebase that shells out to the `colab` CLI.

Every command here was verified against a live install of the official
`google-colab-cli` (v0.6.0, see docs/research.md section 1) -- nothing is
invented. All subprocess calls use argument lists, never a shell string,
per the brief's "no unsafe shell strings" requirement.

Output formats (verified live, docs/research.md section 9):
    `colab new -s NAME [--gpu G]`   -> "[colab] Session READY." on success
    `colab sessions`                -> "[name] backend_id | Hardware: H | Variant: V" per line
    `colab status -s NAME`          -> same, plus "| Status: S"
    `colab stop -s NAME`            -> "[colab] Session terminated." on success
    `colab exec -s NAME -f FILE`    -> script's stdout, verbatim
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from controller.config import settings
from controller.logging_utils import log_event, redact

_STATUS_LINE = re.compile(
    r"\[(?P<name>[^\]]+)\]\s+(?P<backend_id>\S+)\s*\|\s*Hardware:\s*(?P<hardware>\S+)"
    r"\s*\|\s*Variant:\s*(?P<variant>\S+)(?:\s*\|\s*Status:\s*(?P<status>\S+))?"
)


class ColabError(RuntimeError):
    """Raised for any non-zero-exit `colab` invocation. `stderr` is
    pre-redacted and safe to surface to callers/HTTP error bodies."""

    def __init__(self, message: str, returncode: int, stdout: str, stderr: str):
        super().__init__(message)
        self.returncode = returncode
        self.stdout = redact(stdout)
        self.stderr = redact(stderr)


class GpuUnavailableError(ColabError):
    """Raised when `colab new --gpu ...` fails in a way that looks like a
    routine capacity/availability problem rather than a real bug -- this is
    an expected, common outcome on free tier (docs/research.md section 4)."""


@dataclass
class SessionInfo:
    name: str
    backend_id: str | None
    hardware: str | None
    variant: str | None
    status: str | None


_UNAVAILABLE_MARKERS = (
    "no gpu",
    "unavailable",
    "no accelerator",
    "resource exhausted",
    "could not allocate",
    "capacity",
)


class ColabManager:
    """Thin, typed wrapper around the `colab` CLI subprocess. All GPU
    lifecycle operations (create/exec/status/stop/list) live here so no
    other module ever needs to know the CLI's argv shape."""

    def __init__(self, colab_bin: str | None = None):
        self.colab_bin = colab_bin or settings.colab_bin

    def _run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess:
        cmd = [self.colab_bin, *args]
        log_event("colab_cli_invoke", cmd=" ".join(cmd))
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=None,  # inherit environment (PATH, HOME) -- no secrets injected here
            )
        except subprocess.TimeoutExpired as e:
            raise ColabError(
                f"`colab {args[0]}` timed out after {timeout}s", -1,
                (e.stdout or ""), (e.stderr or "")
            )

    def create_runtime(self, session: str, gpu: str | None) -> SessionInfo:
        args = ["new", "-s", session]
        if gpu:
            args += ["--gpu", gpu]
        proc = self._run(args, timeout=settings.startup_timeout_seconds)
        if proc.returncode != 0:
            combined = f"{proc.stdout}\n{proc.stderr}".lower()
            if any(marker in combined for marker in _UNAVAILABLE_MARKERS):
                raise GpuUnavailableError(
                    f"GPU '{gpu}' unavailable", proc.returncode, proc.stdout, proc.stderr
                )
            raise ColabError(
                "`colab new` failed", proc.returncode, proc.stdout, proc.stderr
            )
        return SessionInfo(name=session, backend_id=None, hardware=gpu, variant=None, status="READY")

    def upload_file(self, session: str, local_path: str, remote_path: str) -> None:
        proc = self._run(["upload", "-s", session, local_path, remote_path], timeout=120)
        if proc.returncode != 0:
            raise ColabError(
                "`colab upload` failed", proc.returncode, proc.stdout, proc.stderr
            )

    def install_requirements(self, session: str, requirements_path: str, timeout: float) -> None:
        proc = self._run(
            ["install", "-s", session, "-r", requirements_path], timeout=timeout
        )
        if proc.returncode != 0:
            raise ColabError(
                "`colab install` failed", proc.returncode, proc.stdout, proc.stderr
            )

    def exec_script(self, session: str, script_text: str, timeout: float) -> str:
        """Writes `script_text` to a temp file and runs it in `session`'s
        kernel via `colab exec -f`. Returns raw stdout. A temp file (not
        stdin/argv) keeps request payloads out of process argv (visible in
        `ps`) and out of shell interpretation entirely."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="colab_exec_"
        ) as f:
            f.write(script_text)
            script_path = f.name
        try:
            proc = self._run(
                ["exec", "-s", session, "-f", script_path, "--timeout", str(timeout)],
                timeout=timeout + 15,  # leave headroom over the CLI's own --timeout
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        if proc.returncode != 0:
            raise ColabError(
                "`colab exec` failed", proc.returncode, proc.stdout, proc.stderr
            )
        return proc.stdout

    def status(self, session: str) -> SessionInfo | None:
        proc = self._run(["status", "-s", session], timeout=30)
        if proc.returncode != 0:
            return None
        return self._parse_session_line(proc.stdout, session)

    def list_sessions(self) -> list[SessionInfo]:
        proc = self._run(["sessions"], timeout=30)
        if proc.returncode != 0:
            return []
        sessions = []
        for line in proc.stdout.splitlines():
            m = _STATUS_LINE.search(line)
            if m:
                sessions.append(
                    SessionInfo(
                        name=m.group("name"),
                        backend_id=m.group("backend_id"),
                        hardware=m.group("hardware"),
                        variant=m.group("variant"),
                        status=m.group("status"),
                    )
                )
        return sessions

    def stop_runtime(self, session: str) -> bool:
        proc = self._run(["stop", "-s", session], timeout=60)
        return proc.returncode == 0

    def _parse_session_line(self, text: str, expected_name: str) -> SessionInfo | None:
        for line in text.splitlines():
            m = _STATUS_LINE.search(line)
            if m and m.group("name") == expected_name:
                return SessionInfo(
                    name=m.group("name"),
                    backend_id=m.group("backend_id"),
                    hardware=m.group("hardware"),
                    variant=m.group("variant"),
                    status=m.group("status"),
                )
        return None


colab_manager = ColabManager()
