TRACK_ID=PS08

# NexusTiQ 24 - Supply Chain Disruption Response Assistant

NexusTiQ 24 PS08 is an evidence-first Supply Chain Disruption Response Assistant. It accepts an unstructured disruption notice, extracts notice facts, maps those facts to committed supply-chain records, traces potential operational impact, prioritizes affected orders, and presents evidence-grounded action options for a human operator.

It uses a small synthetic dataset for demonstration and testing. It is not connected to production logistics systems.

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

Set the Gemini key before using notice understanding:

```powershell
$env:GEMINI_API_KEY = "your-key"
python app.py
```

The key is read only from `GEMINI_API_KEY` and must never be committed. The deterministic intake, matching, impact, prioritization, and no-impact paths remain locally testable without a live key.

## What the application does

The single investigation screen follows this flow:

```text
disruption notice
-> Gemini notice understanding
-> deterministic entity matching
-> deterministic impact traversal
-> deterministic order prioritization
-> evidence-grounded action options
```

The application can show:

- Matched suppliers, routes, shipments, containers, and SKUs.
- Downstream potential relationships to inventory, orders, and customers.
- Deterministic urgency, severity, and affected-order ranking.
- Action options with trade-offs, prerequisites, risks, and evidence.
- Direct impact, downstream potential impact, review-required, insufficient-information, and no-impact states.

## Architecture and boundaries

- **Gemini:** Understands messy disruption language and extracts event type, locations, duration text, transport mode, route hints, explicit entity hints, and uncertainties.
- **Deterministic Python:** Performs matching, relationship traversal, evidence generation, order scoring, severity/urgency classification, and action-option selection.
- **Human operator:** Makes the final decision. The application does not execute logistics actions.

Every meaningful impact, priority, and recommendation claim carries source records, fields, relationships, supporting facts, and a source stage where available. If no operational record matches, the system reports no impact and does not invent an affected order, customer, shortage, or recommendation.

## Data and limitations

The committed synthetic data contains suppliers, shipments, containers, routes, SKUs, inventory, orders, customers, and example disruptions. It supports deterministic relationship demonstrations only.

The application does not provide live GPS tracking, production data, financial-loss calculation, SLA prediction, alternate-supplier capability, autonomous execution, customer communication, shipment changes, or purchase orders. Inventory checks compare linked quantities; they are not a shared-stock allocation optimizer.

## Testing

With the supported Python 3.11 environment active:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py analysis api matching models services tests
```

The application is served by `app.py`; no Node.js, npm command, second server, database setup, or asset build is required.

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
