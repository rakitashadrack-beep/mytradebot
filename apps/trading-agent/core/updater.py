"""Safe update manifest checking; installation remains Windows-installer controlled."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateInfo:
    available: bool
    version: str = ""
    download_url: str = ""
    sha256: str = ""
    notes: str = ""


def version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise ValueError("Versions must contain only dot-separated integers") from exc


class UpdateChecker:
    def __init__(self, current_version: str, manifest_url: str = "") -> None:
        self.current_version, self.manifest_url = current_version, manifest_url

    def check(self) -> UpdateInfo:
        if not self.manifest_url:
            return UpdateInfo(False)
        with urllib.request.urlopen(self.manifest_url, timeout=10) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        version, url, digest = manifest["version"], manifest["download_url"], manifest["sha256"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise ValueError("Update manifest has an invalid SHA-256 digest")
        return UpdateInfo(version_tuple(version) > version_tuple(self.current_version), version, url, digest,
                          str(manifest.get("notes", "")))
