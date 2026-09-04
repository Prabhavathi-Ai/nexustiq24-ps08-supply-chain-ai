"""Controlled errors raised by the Gemini extraction boundary."""


class GeminiExtractionError(RuntimeError):
    """Base error for unavailable or invalid Gemini extraction."""


class GeminiConfigurationError(GeminiExtractionError):
    """Raised when Gemini configuration is missing."""


class GeminiResponseError(GeminiExtractionError):
    """Raised when Gemini fails or returns an invalid response."""