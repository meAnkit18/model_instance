"""Unit tests for the subprocess wrapper, using real output strings
captured from the live CLI (docs/research.md section 9) rather than
guessed formats."""
import subprocess
from unittest.mock import patch

import pytest

from controller.services.colab_manager import ColabError, ColabManager, GpuUnavailableError

SESSIONS_OUTPUT = (
    "[worker] gpu-t4-s-kkb-usw1b0-abc123 | Hardware: T4 | Variant: GPU\n"
)
STATUS_OUTPUT = (
    "[worker] gpu-t4-s-kkb-usw1b0-abc123 | Hardware: T4 | Variant: GPU | Status: IDLE\n"
)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["colab"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_create_runtime_success():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(0, "[colab] Session READY.\n")) as run:
        info = mgr.create_runtime("worker", "T4")
    assert info.name == "worker"
    run.assert_called_once()
    assert run.call_args[0][0] == ["new", "-s", "worker", "--gpu", "T4"]


def test_create_runtime_no_gpu_omits_flag():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(0)) as run:
        mgr.create_runtime("worker", None)
    assert run.call_args[0][0] == ["new", "-s", "worker"]


def test_create_runtime_gpu_unavailable_classified_correctly():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(1, "", "Error: no GPU capacity available")):
        with pytest.raises(GpuUnavailableError):
            mgr.create_runtime("worker", "T4")


def test_create_runtime_other_failure_is_plain_colab_error():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(1, "", "authentication required")):
        with pytest.raises(ColabError) as exc_info:
            mgr.create_runtime("worker", "T4")
    assert not isinstance(exc_info.value, GpuUnavailableError)


def test_list_sessions_parses_real_output_format():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(0, SESSIONS_OUTPUT)):
        sessions = mgr.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].name == "worker"
    assert sessions[0].hardware == "T4"


def test_status_parses_real_output_format():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(0, STATUS_OUTPUT)):
        info = mgr.status("worker")
    assert info.status == "IDLE"


def test_status_returns_none_for_unknown_session():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(1, "", "session not found")):
        assert mgr.status("nope") is None


def test_exec_script_uses_temp_file_and_cleans_up():
    mgr = ColabManager()
    seen_paths = []

    def fake_run(args, timeout):
        # args: ["exec", "-s", session, "-f", path, "--timeout", "..."]
        seen_paths.append(args[4])
        return _completed(0, "hello\n")

    with patch.object(mgr, "_run", side_effect=fake_run):
        out = mgr.exec_script("worker", "print('hello')", timeout=30)

    assert out == "hello\n"
    import os
    assert not os.path.exists(seen_paths[0])  # temp file cleaned up after use


def test_exec_script_raises_on_failure():
    mgr = ColabManager()
    with patch.object(mgr, "_run", return_value=_completed(1, "", "NameError")):
        with pytest.raises(ColabError):
            mgr.exec_script("worker", "bad code", timeout=30)


def test_no_shell_string_ever_built():
    """Regression guard for the brief's `os.system(user_input)` prohibition
    -- every ColabManager call must go through subprocess.run with an
    argument list, never shell=True."""
    mgr = ColabManager()
    with patch("subprocess.run", return_value=_completed(0, "ok")) as run:
        mgr.list_sessions()
    _, kwargs = run.call_args
    assert kwargs.get("shell", False) is False
    assert isinstance(run.call_args[0][0], list)
