"""Non-blocking process locking for scheduled and manual invocations."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from stream_archiver.errors import LockUnavailableError


@contextmanager
def execution_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive non-blocking advisory lock for one invocation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    stream: TextIO = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockUnavailableError(f"another stream-archiver run owns {path}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
