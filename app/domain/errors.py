"""Domain errors independent of transport and persistence implementations."""


class WorkflowError(Exception):
    """Base class for errors that can be mapped by an inbound adapter."""


class DuplicateRequest(WorkflowError):
    """Raised when the same project request hash is submitted twice."""


class ConcurrencyConflict(WorkflowError):
    """Raised when an optimistic-lock review update loses a race."""


class MigrationError(WorkflowError):
    """Raised when an applied migration no longer matches its source."""
