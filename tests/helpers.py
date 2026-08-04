"""Small deterministic builders shared by filesystem behavior tests."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def write_at(path: Path, content: bytes, when: datetime) -> Path:
    """Create a regular file with an explicit modification time."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    timestamp_ns = int(when.timestamp() * 1_000_000_000)
    os.utime(path, ns=(timestamp_ns, timestamp_ns))
    return path


def symlink_at(path: Path, target: str, when: datetime) -> Path:
    """Create a symlink and assign an explicit link modification time."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    timestamp_ns = int(when.timestamp() * 1_000_000_000)
    os.utime(path, ns=(timestamp_ns, timestamp_ns), follow_symlinks=False)
    return path
