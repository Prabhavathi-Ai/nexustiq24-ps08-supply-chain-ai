"""API models and in-memory endpoints for disruption notice intake."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from gemini.errors import GeminiExtractionError
from gemini.extraction import extract_understanding
from gemini.models import DisruptionUnderstanding


MAX_DESCRIPTION_LENGTH = 5_000


class DisruptionNoticeRequest(BaseModel):
    """Validated input received from a user-provided disruption notice."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., max_length=MAX_DESCRIPTION_LENGTH)
    reported_at: datetime | None = None
    source: str | None = None


class DisruptionNoticeResponse(BaseModel):
    """Stored disruption notice returned by the intake API."""

    disruption_id: str
    status: str
    original_description: str
    normalized_description: str
    reported_at: datetime
    source: str | None


class DisruptionUnderstandingResponse(BaseModel):
    disruption_id: str
    original_description: str
    understanding: DisruptionUnderstanding


router = APIRouter(prefix="/api/disruptions", tags=["disruptions"])
_disruptions: dict[str, DisruptionNoticeResponse] = {}


def normalize_description(description: str) -> str:
    """Trim the notice and collapse repeated whitespace without changing words."""

    return " ".join(description.split())


def clear_disruptions() -> None:
    """Clear local records for isolated tests and local development."""

    _disruptions.clear()


@router.post("", response_model=DisruptionNoticeResponse, status_code=status.HTTP_201_CREATED)
def create_disruption(notice: DisruptionNoticeRequest) -> DisruptionNoticeResponse:
    """Accept and store a disruption notice without interpreting it."""

    normalized_description = normalize_description(notice.description)
    if not normalized_description:
        raise HTTPException(status_code=422, detail="description must not be empty")

    record = DisruptionNoticeResponse(
        disruption_id=f"DIS-{uuid4().hex[:12].upper()}",
        status="accepted",
        original_description=notice.description,
        normalized_description=normalized_description,
        reported_at=notice.reported_at or datetime.now(timezone.utc),
        source=notice.source,
    )
    _disruptions[record.disruption_id] = record
    return record


@router.get("/{disruption_id}", response_model=DisruptionNoticeResponse)
def get_disruption(disruption_id: str) -> DisruptionNoticeResponse:
    """Return a stored disruption notice by identifier."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    return record


@router.post("/{disruption_id}/understanding", response_model=DisruptionUnderstandingResponse)
def understand_disruption(disruption_id: str) -> DisruptionUnderstandingResponse:
    """Extract notice facts without calculating operational impact."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    try:
        understanding = extract_understanding(record.original_description)
    except GeminiExtractionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return DisruptionUnderstandingResponse(
        disruption_id=record.disruption_id,
        original_description=record.original_description,
        understanding=understanding,
    )