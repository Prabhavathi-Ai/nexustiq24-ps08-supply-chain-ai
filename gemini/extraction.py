"""Focused Gemini service for understanding disruption notice language."""

import json
from typing import Any, Protocol

from config.settings import gemini_api_key
from gemini.errors import GeminiConfigurationError, GeminiResponseError
from gemini.models import DisruptionUnderstanding


EXTRACTION_PROMPT = """You extract facts from one supply-chain disruption notice.
Return JSON matching exactly this schema:
{
  "event_type": string or null,
  "locations": [string],
  "duration_text": string or null,
  "transport_mode": string or null,
  "route_hints": [string],
  "entity_hints": [string],
  "uncertainties": [string]
}
Extract only facts explicitly supported by the notice. Do not infer supply-chain
impact, identify database records, calculate severity, or invent missing values.
Represent ambiguity in uncertainties. Return JSON only.

NOTICE:
"""


class GeminiTextClient(Protocol):
    """Minimal client boundary used by the extraction service and tests."""

    def generate(self, prompt: str) -> str:
        """Generate structured text for a prompt."""


GEMINI_RETRY_ATTEMPTS = 4
GEMINI_RETRY_INITIAL_DELAY = 0.4
GEMINI_RETRY_MAX_DELAY = 2.0
GEMINI_RETRY_EXP_BASE = 2
GEMINI_RETRY_JITTER = 0.5
GEMINI_RETRY_STATUS_CODES = (408, 429, 500, 502, 503, 504)


class GoogleGeminiTextClient:
    """Adapter around the official Google GenAI SDK, loaded only when requested."""

    def __init__(self, api_key: str, *, http_options: dict[str, Any] | None = None) -> None:
        try:
            from google import genai
        except ImportError as error:
            raise GeminiConfigurationError("Gemini SDK is not installed") from error
        options = dict(http_options) if http_options is not None else {}
        options.setdefault("retry_options", {
            "attempts": GEMINI_RETRY_ATTEMPTS,
            "initial_delay": GEMINI_RETRY_INITIAL_DELAY,
            "max_delay": GEMINI_RETRY_MAX_DELAY,
            "exp_base": GEMINI_RETRY_EXP_BASE,
            "jitter": GEMINI_RETRY_JITTER,
            "http_status_codes": list(GEMINI_RETRY_STATUS_CODES),
        })
        self._client = genai.Client(api_key=api_key, http_options=options)

    def generate(self, prompt: str) -> str:
        try:
            response = self._client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            text = getattr(response, "text", None)
            if not isinstance(text, str):
                raise GeminiResponseError("Gemini returned no text response")
            return text
        except GeminiResponseError:
            raise
        except Exception as error:
            raise GeminiResponseError("Gemini request failed") from error


def extract_understanding(raw_notice: str, client: GeminiTextClient | None = None) -> DisruptionUnderstanding:
    """Extract and validate notice facts through the isolated Gemini boundary."""

    if client is None:
        api_key = gemini_api_key()
        if not api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured")
        client = GoogleGeminiTextClient(api_key)

    try:
        try:
            response_text = client.generate(EXTRACTION_PROMPT + raw_notice)
        except GeminiResponseError:
            raise
        except Exception as error:
            raise GeminiResponseError("Gemini request failed") from error
        payload: Any = json.loads(response_text)
        if not isinstance(payload, dict):
            raise GeminiResponseError("Gemini response must be a JSON object")
        return DisruptionUnderstanding.model_validate(payload)
    except GeminiResponseError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise GeminiResponseError("Gemini returned malformed structured data") from error