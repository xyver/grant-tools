"""Settings and paths for the grant tools.

Values come from environment variables, with an optional `.env` file next to
this module filling in anything not already set. See `.env.example`.
"""
import os
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parent
DOCS_DIR = MODULE_DIR / "docs"
# Kept for diagnostics output; the grant tools no longer search for a
# surrounding DaedalMap workspace.
PROJECT_ROOT = MODULE_DIR


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE reader. Real environment variables always win."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(MODULE_DIR / ".env")


def _env_path(*names: str) -> Path | None:
    for name in names:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return Path(value)
    return None


# Processed data (the data bundle or your own pipeline build): a folder that
# contains countries/. DATA_ROOT is accepted for older setups.
DATA_ROOT = _env_path("GRANT_DATA_ROOT", "DATA_ROOT") or MODULE_DIR / "data"
# Collector snapshots; collectors/feeds.py writes here by default.
STATE_DIR = _env_path("GRANT_STATE_DIR") or MODULE_DIR / "state"
# DaedalMap geometry tools used for place text to loc_id lookups.
GEOMETRY_MCP_URL = str(os.environ.get("GEOMETRY_MCP_URL", "") or "").strip() or "https://app.daedalmap.com/mcp"
