class AppError(Exception):
    """Base application exception."""


class CostLimitExceeded(AppError):
    """Raised when an AI call would exceed configured spend limits."""


class ExtractionFailed(AppError):
    """Raised when user input cannot be converted into a usable business record."""


class SecurityError(AppError):
    """Raised when an authorization, forgery, or boundary check fails."""


class VoiceTranscriptionError(AppError):
    """Raised when an audio voice message cannot be transcribed."""
