class AIProviderError(Exception):
    """Base class for AI provider errors."""


class AIProviderConfigError(AIProviderError):
    """Raised when the provider is misconfigured."""


class AIProviderTransientError(AIProviderError):
    """Raised for temporary upstream failures where retry may help."""


class AIProviderResponseError(AIProviderError):
    """Raised when an upstream provider returns an invalid or unusable response."""
