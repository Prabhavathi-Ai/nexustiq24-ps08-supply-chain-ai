"""Application entry point for the PS08 control tower."""

import sys

from fastapi import FastAPI


SUPPORTED_PYTHON = (3, 11)

if sys.version_info[:2] != SUPPORTED_PYTHON:
    raise RuntimeError(
        "Python 3.11 is required. Select the project Python 3.11 environment "
        "before running app.py."
    )


app = FastAPI(title="NexusTiQ 24 Supply Chain Control Tower")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return a minimal readiness response."""

    return {"status": "ok", "track_id": "PS08"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)