"""Deterministic entity matching components."""

from .engine import match_understanding, normalize_match_text
from .models import MatchCandidate, MatchingResponse

__all__ = ["MatchCandidate", "MatchingResponse", "match_understanding", "normalize_match_text"]