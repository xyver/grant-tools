"""Reusable audible/terminal completion alerts for long-running commands.

Use ``completion_alert("label")`` around Python work, or run any command with
``python build/completion_alert.py --label "label" -- <command>``.  Alerts are
best-effort and never replace or hide the wrapped command's exit status. PASS,
WARN, FAIL, and CANCELLED use distinct markers and Windows sounds.

For an agent-run job, pass ``--log`` and ``--receipt`` so the full command
output stays on disk and the agent receives only the completion marker.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Iterator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


ALERT_STATUSES = {"PASS", "WARN", "FAIL", "CANCELLED"}


def emit_alert(label: str, status: str, *, elapsed_seconds: float) -> dict:
    """Emit a best-effort audible alert and a stable one-line status marker."""
    status = status.upper()
    if status not in ALERT_STATUSES:
        raise ValueError("Completion alert status must be PASS, WARN, FAIL, or CANCELLED")
    marker = {
        "event": "completion_alert",
        "label": label,
        "status": status,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "finished_at": _now(),
    }
    # BEL works in supporting terminals. MessageBeep reaches normal Windows
    # sessions even when the terminal has visual/audible bell disabled.
    print("\a" + json.dumps(marker, sort_keys=True), file=sys.stderr, flush=True)
    if sys.platform == "win32":
        try:
            import winsound

            sounds = {
                "PASS": winsound.MB_OK,
                "WARN": winsound.MB_ICONEXCLAMATION,
                "FAIL": winsound.MB_ICONHAND,
                "CANCELLED": winsound.MB_ICONASTERISK,
            }
            winsound.MessageBeep(sounds[status])
        except Exception:
            pass
    return marker


def write_receipt(path: Path, marker: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".building")
    temporary.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _notify(label: str, status: str, elapsed_seconds: float, receipt: Path | None) -> None:
    """Keep notification failures from changing the completed job's result."""
    try:
        marker = emit_alert(label, status, elapsed_seconds=elapsed_seconds)
        if receipt is not None:
            write_receipt(receipt, marker)
    except Exception:
        pass


def run_main(
    label: str,
    command: Callable[[], int],
    *,
    receipt: Path | None = None,
    status_by_exit_code: dict[int, str] | None = None,
) -> int:
    """Run a script's ``main`` and alert with its real exit status.

    This is the safe entrypoint helper for commands whose ``main`` returns an
    exit code.  Unlike wrapping ``raise SystemExit(main())`` in
    :func:`completion_alert`, a non-zero return is reported as FAIL without
    treating a normal ``SystemExit(0)`` as an exception. Exit 130 and keyboard
    interruption report CANCELLED. Callers may map known exit codes to WARN or
    another supported status without changing the returned exit code.
    """
    started = time.perf_counter()
    try:
        result = command()
        return_code = 0 if result is None else int(result)
    except SystemExit as exc:
        return_code = exc.code if isinstance(exc.code, int) else 1
    except KeyboardInterrupt:
        _notify(label, "CANCELLED", time.perf_counter() - started, receipt)
        raise
    except BaseException:
        _notify(label, "FAIL", time.perf_counter() - started, receipt)
        raise
    elapsed = time.perf_counter() - started
    try:
        default_status = "PASS" if return_code == 0 else "CANCELLED" if return_code == 130 else "FAIL"
        status = (status_by_exit_code or {}).get(return_code, default_status).upper()
        if status not in ALERT_STATUSES:
            raise ValueError(f"Unsupported completion status for exit code {return_code}: {status}")
        marker = emit_alert(label, status, elapsed_seconds=elapsed)
        marker["exit_code"] = return_code
        if receipt is not None:
            write_receipt(receipt, marker)
    except Exception:
        pass
    return return_code


@contextmanager
def completion_alert(label: str, *, receipt: Path | None = None) -> Iterator[None]:
    """Alert when a Python block exits, while preserving its exception."""
    started = time.perf_counter()
    try:
        yield
    except KeyboardInterrupt:
        _notify(label, "CANCELLED", time.perf_counter() - started, receipt)
        raise
    except BaseException:
        _notify(label, "FAIL", time.perf_counter() - started, receipt)
        raise
    _notify(label, "PASS", time.perf_counter() - started, receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--log", type=Path, help="Append command stdout/stderr to this file instead of the terminal")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    if args.log is not None and args.receipt is not None and args.log.resolve() == args.receipt.resolve():
        parser.error("--log and --receipt must be different files")
    started = time.perf_counter()
    try:
        if args.log is None:
            return_code = subprocess.run(command).returncode
        else:
            args.log.parent.mkdir(parents=True, exist_ok=True)
            with args.log.open("a", encoding="utf-8") as log:
                log.write(f"\n=== {args.label} started {_now()} ===\n")
                log.flush()
                return_code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
    except BaseException:
        _notify(args.label, "FAIL", time.perf_counter() - started, args.receipt)
        raise
    elapsed = time.perf_counter() - started
    try:
        marker = emit_alert(args.label, "PASS" if return_code == 0 else "FAIL", elapsed_seconds=elapsed)
        marker["exit_code"] = return_code
        if args.receipt:
            write_receipt(args.receipt, marker)
    except Exception:
        pass
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
