"""Shared exception types. Providers raise these; callers decide recovery."""
from __future__ import annotations

from typing import Any


class TaraError(Exception):
    pass


class ToolError(TaraError):
    """A tool failed in a way the model may report and recover from.

    `code` is stable and safe to show to the model; `details` must never contain
    secrets or raw provider bodies. Text is bounded and content-free.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = dict(details or {})


class ProviderError(TaraError):
    """Upstream provider failed (network, 5xx, auth)."""


class ProviderTimeout(ProviderError):
    pass


class MalformedOutput(ProviderError):
    """Provider answered but output failed schema/semantic validation."""


class JoinError(TaraError):
    pass


class PermissionDenied(TaraError):
    pass


class ConfigError(TaraError):
    pass


class ModelLoadError(ProviderError):
    """A local model failed to load (missing files, library missing, timeout)."""


class QueueFull(ProviderError):
    """Inference admission refused: the bounded wait queue is full."""


class InvalidStateChange(TaraError):
    """A conversation-state event violated an invariant."""


class BudgetExceeded(ProviderError):
    """Mandatory prompt sections alone exceed the context budget: do not call the model."""


class FatalProviderError(ProviderError):
    """Retrying cannot help (bad key, unknown model, rejected request)."""


class SessionLimitExceeded(TaraError):
    """Too many concurrent voice sessions."""


class DuplicateSession(TaraError):
    """A session with this id is already active."""
