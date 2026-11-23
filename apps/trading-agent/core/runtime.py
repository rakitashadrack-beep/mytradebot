"""Small, dependency-free helpers for locating writable runtime files."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_packaged() -> bool:
    """Return whether the program is running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def data_directory() -> Path:
    """Return the directory used for mutable application data.

    Source checkouts retain their current working-directory behaviour on Linux,
    making development and tests straightforward. Installed Windows bundles use
    LOCALAPPDATA, which is writable without administrator privileges.
    """
    configured = os.getenv("MYTRADEBOT_DATA_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser()
    elif is_packaged() or os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        path = Path(base) / "MyTradeBot" if base else Path.cwd() / "MyTradeBot-data"
    else:
        path = Path.cwd()
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_env_file() -> Path:
    """Return the private environment file location for this runtime."""
    if is_packaged():
        return data_directory() / ".env"
    return Path(__file__).resolve().parents[1] / ".env"
