"""Non-blocking process locking for scheduled and manual invocations."""

from __future__ import annotations

import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from stream_archiver.errors import LockUnavailableError
from stream_archiver.observability import log_event

LOGGER = logging.getLogger(__name__)


@contextmanager
def execution_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive non-blocking advisory lock for one invocation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    log_event(
        LOGGER,
        logging.DEBUG,
        "lock_acquire_started",
        "attempting non-blocking execution lock",
        path=path,
    )
    stream: TextIO = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockUnavailableError(
                f"another stream-archiver run owns {path}"
            ) from exc
        log_event(
            LOGGER,
            logging.INFO,
            "lock_acquired",
            "exclusive execution lock acquired",
            path=path,
        )
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            log_event(
                LOGGER,
                logging.DEBUG,
                "lock_released",
                "execution lock released",
                path=path,
            )
