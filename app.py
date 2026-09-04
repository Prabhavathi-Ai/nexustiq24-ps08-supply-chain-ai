"""Application entry point for the PS08 control tower."""

import sys

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.disruptions import router as disruptions_router


SUPPORTED_PYTHON = (3, 11)

if sys.version_info[:2] != SUPPORTED_PYTHON:
    raise RuntimeError(
        "Python 3.11 is required. Select the project Python 3.11 environment "
        "before running app.py."
    )


app = FastAPI(title="NexusTiQ 24 Supply Chain Control Tower")
app.include_router(disruptions_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return a minimal readiness response."""

    return {"status": "ok", "track_id": "PS08"}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)