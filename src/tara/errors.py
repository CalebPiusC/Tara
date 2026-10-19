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
