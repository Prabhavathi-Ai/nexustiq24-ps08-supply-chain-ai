"""Environment-backed configuration for external integrations."""

import os


def gemini_api_key() -> str | None:
    """Return the configured Gemini key without exposing or persisting it."""

    return os.getenv("GEMINI_API_KEY")