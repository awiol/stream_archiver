"""Application-specific exception types."""


class StreamArchiverError(Exception):
    """Base class for expected stream-archiver failures."""


class ConfigurationError(StreamArchiverError):
    """Raised when configuration is missing, invalid, or unsafe."""


class PlanningError(StreamArchiverError):
    """Raised when a safe, collision-free archive plan cannot be produced."""


class ExecutionError(StreamArchiverError):
    """Raised when a planned archive cannot be committed safely."""


class RecoveryError(StreamArchiverError):
    """Raised when cleanup of a previously committed archive cannot resume safely."""


class LockUnavailableError(StreamArchiverError):
    """Raised when another stream-archiver process already owns the execution lock."""
