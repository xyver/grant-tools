"""Shared completion-alert entry point for standalone downloader scripts."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build.completion_alert import run_main  # noqa: E402


def run_downloader_main(label: str, command: Callable[[], int | None]) -> int:
    """Run a downloader and preserve its exit status while emitting an alert."""
    return run_main(f"downloader: {label}", command)
