"""Immutable records shared by discovery, planning, execution, and verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class EntryKind(StrEnum):
    """Filesystem entry types considered by stream grouping."""

    REGULAR = "regular"
    SYMLINK = "symlink"


class ActionKind(StrEnum):
    """Actions produced by the planner for one selected entry.

    ``COPY`` is retained only to recover manifests written by version 0.1.0.
    New plans use ``MOVE`` for uncompressed files.
    """

    MOVE = "move"
    COPY = "copy"
    GZIP = "gzip"
    BZ2 = "bz2"
    PRESERVE_SYMLINK = "preserve-symlink"
    DROP_ALIAS_SYMLINK = "drop-alias-symlink"
    SKIP_SYMLINK = "skip-symlink"


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Stable-enough source identity used to detect changes before deletion."""

    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class Entry:
    """One regular file or symbolic link discovered without following links."""

    relative_path: Path
    absolute_path: Path
    kind: EntryKind
    identity: FileIdentity
    link_target: str | None = None

    @property
    def mtime_ns(self) -> int:
        """Return the nanosecond modification time used for ordering."""

        return self.identity.mtime_ns


@dataclass(frozen=True, slots=True)
class Stream:
    """A non-empty time-ordered group not separated by the configured gap."""

    entries: tuple[Entry, ...]

    @property
    def oldest_mtime_ns(self) -> int:
        """Return the oldest entry modification time in the stream."""

        return self.entries[0].mtime_ns

    @property
    def newest_mtime_ns(self) -> int:
        """Return the newest entry modification time in the stream."""

        return self.entries[-1].mtime_ns


@dataclass(frozen=True, slots=True)
class PlannedAction:
    """One source action and its optional destination-relative path."""

    source: Entry
    kind: ActionKind
    archive_path: Path | None


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    """A deterministic, collision-free plan for one source stream.

    Each plan owns exactly one source root. Files discovered under different
    source roots can never be placed in the same final archive directory.
    """

    policy_name: str
    source_root: Path
    destination_root: Path
    plan_id: str
    archive_name: str
    stream: Stream
    actions: tuple[PlannedAction, ...]

    @property
    def payload_actions(self) -> tuple[PlannedAction, ...]:
        """Return actions that create objects in the archive payload namespace."""

        return tuple(
            action
            for action in self.actions
            if action.kind
            in {
                ActionKind.MOVE,
                ActionKind.GZIP,
                ActionKind.BZ2,
                ActionKind.PRESERVE_SYMLINK,
            }
        )

    @property
    def cleanup_only_actions(self) -> tuple[PlannedAction, ...]:
        """Return selected actions that remove source entries without archiving them."""

        return tuple(
            action for action in self.actions if action.kind is ActionKind.DROP_ALIAS_SYMLINK
        )

    @property
    def payload_oldest_mtime_ns(self) -> int:
        """Return the oldest modification time among payload-producing entries."""

        return min(action.source.mtime_ns for action in self.payload_actions)

    @property
    def payload_newest_mtime_ns(self) -> int:
        """Return the newest modification time among payload-producing entries."""

        return max(action.source.mtime_ns for action in self.payload_actions)

    @property
    def final_directory(self) -> Path:
        """Return the committed archive directory for this plan."""

        return self.destination_root / self.archive_name
