TRACK_ID=PS08

# NexusTiQ 24 - Supply Chain Disruption Response Assistant

This repository contains the PS08 evidence-first supply chain disruption control tower.

## Runtime

The project targets Python 3.11. Python 3.14 is not a supported project runtime.

Create and activate a Python 3.11 environment, install the dependencies, then start the application:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

The application listens on `http://localhost:8000`.

## Foundation layout

- `app.py`: single application entry point
- `api/`: HTTP boundary
- `models/`: domain models
- `services/`: orchestration services
- `detectors/`: disruption detection
- `matching/`: deterministic entity matching
- `analysis/`: deterministic impact analysis
- `gemini/`: Gemini integration boundary
- `evidence/`: traceable findings
- `storage/`: local persistence boundary
- `config/`: configuration boundary
- `tests/`: automated tests
- `data/`: local datasets and fixtures
- `frontend/`: frontend application surface

All operational facts and impact calculations must come from repository data and deterministic Python logic. Gemini may interpret notices and explain findings, but it must not invent operational records or measurements.
