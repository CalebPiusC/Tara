"""Shared exception types. Providers raise these; callers decide recovery."""


class TaraError(Exception):
    pass


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
