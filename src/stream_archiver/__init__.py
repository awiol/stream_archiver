"""Policy-driven archival of complete, old filesystem streams."""

from stream_archiver.config import (
    AppConfig,
    CompressionCodec,
    CompressionRule,
    Policy,
    SymlinkRule,
    load_config,
)
from stream_archiver.executor import ArchiveVerificationResult, verify_archive
from stream_archiver.service import PolicyRunResult, run_policy, verify_destination, verify_policies

__all__ = [
    "AppConfig",
    "ArchiveVerificationResult",
    "CompressionCodec",
    "CompressionRule",
    "Policy",
    "PolicyRunResult",
    "SymlinkRule",
    "load_config",
    "run_policy",
    "verify_archive",
    "verify_destination",
    "verify_policies",
]

__version__ = "0.4.0a1"
