#!/usr/bin/env python3
"""Phase 1 proof-of-concept for docs/architecture.md.

Proves, end to end, that this machine can drive the official Google Colab
CLI to:
    authenticate -> create a GPU runtime -> execute a Python script
    -> print GPU information -> stop the runtime

Prerequisites (one-time, human, interactive -- cannot be automated, see
docs/research.md section 2 and docs/colab-auth.md):
    uv tool install google-colab-cli
    colab auth

Usage:
    python3 phase1_poc/run_poc.py [--gpu T4] [--session poc-<timestamp>]

Design notes:
    - Every Colab CLI invocation goes through `_colab()`, which always calls
      subprocess.run with an argument list (never a shell string), matching
      the brief's "no unsafe shell strings" requirement.
    - The session is always torn down in a `finally` block, even on failure,
      so a bug here doesn't leave a metered GPU runtime running unattended.
    - Stage timings are printed so cold-start latency is visible from the
      first real run, per the brief's "make startup latency obvious" ask.
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

COLAB_BIN = "colab"
GPU_CHECK_SCRIPT = Path(__file__).parent / "gpu_check.py"


def _colab(*args: str, timeout: float) -> subprocess.CompletedProcess:
    cmd = [COLAB_BIN, *args]
    print(f"[poc] $ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", default="T4", help="GPU type (T4, L4, G4, H100, A100)")
    parser.add_argument("--session", default=f"poc-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--exec-timeout", type=float, default=180.0)
    parser.add_argument("--provision-timeout", type=float, default=300.0)
    args = parser.parse_args()

    timings = {}
    session = args.session
    created = False
    t_start = time.monotonic()

    try:
        # 1. authenticate check (does NOT trigger interactive auth --
        #    that must already have been done via `colab auth`)
        whoami = _colab("sessions", timeout=30)
        if whoami.returncode != 0:
            print(
                "[poc] FAILED: `colab sessions` failed -- CLI is not authenticated.\n"
                "       Run `colab auth` interactively first (see docs/colab-auth.md).\n"
                f"       stderr: {whoami.stderr.strip()}",
                file=sys.stderr,
            )
            return 2

        # 2. create GPU runtime
        t0 = time.monotonic()
        created_proc = _colab(
            "new", "-s", session, "--gpu", args.gpu, timeout=args.provision_timeout
        )
        timings["gpu_provision_seconds"] = round(time.monotonic() - t0, 1)
        if created_proc.returncode != 0:
            print(
                f"[poc] FAILED to provision GPU runtime (gpu={args.gpu}).\n"
                f"       This is expected/routine if free-tier GPUs are unavailable\n"
                f"       right now (see docs/research.md section 4/6) -- retry later\n"
                f"       or with a different --gpu.\n"
                f"       stdout: {created_proc.stdout.strip()}\n"
                f"       stderr: {created_proc.stderr.strip()}",
                file=sys.stderr,
            )
            return 3
        created = True
        print(f"[poc] runtime '{session}' created in {timings['gpu_provision_seconds']}s")

        # 3. execute Python script, print GPU information
        t0 = time.monotonic()
        exec_proc = _colab(
            "exec", "-s", session, "-f", str(GPU_CHECK_SCRIPT), timeout=args.exec_timeout
        )
        timings["exec_seconds"] = round(time.monotonic() - t0, 1)
        if exec_proc.returncode != 0:
            print(
                f"[poc] FAILED to execute GPU check script.\n"
                f"       stdout: {exec_proc.stdout.strip()}\n"
                f"       stderr: {exec_proc.stderr.strip()}",
                file=sys.stderr,
            )
            return 4

        gpu_info = None
        for line in exec_proc.stdout.splitlines():
            if line.startswith("GPU_CHECK_RESULT_JSON:"):
                gpu_info = json.loads(line[len("GPU_CHECK_RESULT_JSON:"):])
        if gpu_info is None:
            print(
                "[poc] exec succeeded but did not find the expected result marker.\n"
                f"       raw stdout:\n{exec_proc.stdout}",
                file=sys.stderr,
            )
            return 5

        timings["total_seconds"] = round(time.monotonic() - t_start, 1)
        print("\n" + "=" * 60)
        print("GPU detected:")
        print(json.dumps(gpu_info, indent=2))
        print("\nStartup metrics:")
        print(json.dumps(timings, indent=2))
        print("=" * 60)
        return 0

    finally:
        if created:
            stop_proc = _colab("stop", "-s", session, timeout=60)
            if stop_proc.returncode == 0:
                print(f"[poc] runtime '{session}' stopped.")
            else:
                print(
                    f"[poc] WARNING: failed to stop runtime '{session}' -- "
                    f"stop it manually with `colab stop -s {session}` to avoid "
                    f"burning quota.\n       stderr: {stop_proc.stderr.strip()}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    sys.exit(main())
