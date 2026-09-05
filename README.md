TRACK_ID=PS08

# NexusTiQ 24 — PS08 Supply Chain: Disruption Response Assistant

NexusTiQ 24 PS08 is an **evidence-first supply-chain disruption response assistant**. It accepts an unstructured disruption notice, converts that notice into structured understanding, matches it against committed operational records, calculates the impact, prioritizes affected orders, and presents evidence-backed response options to the right human stakeholders.

The product is a guided control-tower workflow. Its primary users are:

- **Operations Manager** — owns the overall case and reviews the full picture.
- **Supply Chain Planner** — owns order/shortage decisions.
- **Logistics & Transportation** — owns shipment/route decisions.
- **Customer Service** — owns customer communication decisions.

It uses a small synthetic dataset for demonstration and testing. It is not connected to production logistics systems.

## 30-second judge narrative

> This is an evidence-first supply-chain disruption response assistant. An operator gives it a messy disruption notice. Gemini converts that notice into structured understanding, then deterministic logic matches it against operational records, calculates the impact, prioritizes affected orders, and presents evidence-backed response options. The system can compare what-if scenarios and route decisions to the appropriate human roles. Humans make and record the decisions; nothing is automatically executed.

In short: *AI reads the notice, deterministic logic does the analysis, humans make the decisions.*

## What problem it solves

Distributors receive unstructured disruption notices and need answers to:

- Which supplier, shipment, order, or customer is affected?
- What is the impact on inventory and orders?
- Which orders should be prioritized?
- What response options exist?
- Who needs to review and decide?
- What evidence supports each conclusion?

The assistant answers all of these while keeping every claim traceable to committed operational records.

## Current end-to-end flow

The application is a single guided workflow, not a set of separate pages. A case moves through:

```text
Role selection
-> Control Tower
-> Report disruption
-> Gemini understands the unstructured notice
-> deterministic entity matching
-> deterministic impact analysis
-> order prioritization
-> operational analytics
-> shipment/route evidence
-> recommended action options
-> what-if scenario comparison
-> Response Coordination
-> role-based Decision Inbox
-> human stakeholder decisions
-> decision audit/history
-> case close
```

## Gemini boundary

**Gemini is used for exactly one thing:** turning an unstructured disruption notice into structured understanding (event type, locations, duration text, transport mode, route hints, explicit entity hints, uncertainties).

Gemini does **NOT** calculate inventory impact, invent relationships, decide affected orders, rank orders, calculate analytics, establish evidence, execute actions, approve decisions, send messages, modify inventory or shipments, or close cases. Those tasks are performed by deterministic application logic that operates only on committed operational data.

Gemini requests run under a bounded per-request timeout with bounded retries, so a stalled upstream cannot hang the demo indefinitely. A Gemini timeout, configuration error, or upstream failure is reported as a service error (HTTP 503) — it is never converted into a "no impact" operational result.

## Control Tower

The Control Tower is the working surface where a stakeholder:

- Sees active exceptions and their states.
- Reads operational KPIs for the affected portfolio.
- Opens the role-aware Decision Inbox.
- Starts a new investigation from a disruption notice.
- Reviews recent cases and their history.
- Tracks human decision progress on open cases.

## Investigation

A full investigation covers:

- Disruption notice intake (validated, stored, never fabricated).
- Structured understanding of the notice.
- Matching against committed operational data (suppliers, routes, shipments, containers, SKUs).
- Deterministic impact analysis (inventory, orders, customers, shortages).
- Evidence-grounded priority ranking.
- Operational analytics for the affected portfolio.
- Shipment/route movement evidence.
- Recommended action options with trade-offs, prerequisites, and risks.

## What-if scenarios

- Scenario comparisons are **deterministic**, not statistical simulation.
- Scenarios wrap the existing recommended action options.
- Every metric is grounded in committed operational data.
- No invented currency, revenue, probability, SLA, recovery time, or live ETA.
- Simulation is advisory only — selecting a scenario executes nothing.

## Human decisions and stakeholder handoff

```text
System recommends
-> assigned human reviews
-> human records a decision
-> system records the audit entry
-> nothing executes automatically
```

Decision requirements are assigned to the appropriate role: **Supply Chain Planner**, **Logistics & Transportation**, or **Customer Service**. The **Operations Manager** reviews the overall case. The Decision Inbox shows each stakeholder only the decisions assigned to their role, so a cross-role decision is rejected.

There is **no real authentication** and **no real external messaging** — role selection is a demo concept and decisions are recorded in the case, not sent anywhere.

## Evidence and audit

Every meaningful output is traceable to underlying operational records through evidence references: order, customer, shipment, route, SKU, inventory, priority, recommendation, and decision evidence. Each reference names its source stage (for example `matching`, `impact`, `prioritization`, `recommendation`, `movement`, `coordination`) plus the fields and relationships used.

Each case keeps a **history/audit trail**: intake, understanding, matching, impact, prioritization, coordination, decisions, and close events, with timestamps recorded by the application.

## No-impact behavior

An important PS08 behavior: if an alarming disruption notice does **not** map to any pending operational record, the system returns **no impact** rather than fabricating one.

The **Kandla** demo scenario demonstrates this: "Severe flooding has been reported near Kandla..." is treated as a real alert, but because Kandla maps to no committed route or pending shipment/order, the result is `no_impact` — no affected orders, customers, shortages, scenarios, or decisions are invented, and nothing is executed.

"No impact" is a deterministic matching result — never an error fallback. A Gemini failure returns a 503 service error instead.

## Demo scenarios

### Vellore — affected disruption

Enter:

```text
Heavy flooding has affected transport routes near Vellore. Deliveries from some suppliers may be delayed for the next five days.
```

The expected path:

```text
Vellore
-> route R-001 (Chennai -> Vellore -> Bengaluru)
-> shipment SHP-001 -> container CNT-1042 -> SKU-001
-> inventory
-> orders ORD-001, ORD-002, ORD-003
-> linked customers
-> prioritized orders with evidence
-> recommended action options
-> what-if scenario comparison
-> stakeholder decisions for assigned roles
-> decision audit entries
-> case close
```

### Kandla — no-impact disruption

Enter:

```text
Severe flooding has been reported near Kandla and may disrupt local roads.
```

The expected result is `no_impact`: no matching route/shipment/order, no invented impact, no fake decisions, no fake scenarios, and no execution.

## Run

Target Python 3.11. Python 3.14 is not a supported runtime.

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

The key is read only from `GEMINI_API_KEY` and must never be committed. Intake, matching, impact, prioritization, scenarios, coordination, decisions, and no-impact paths work without a live key; only live notice understanding needs it.

No Node.js, npm command, second server, or database setup is required.

## Tests

With the supported Python 3.11 environment active:

```powershell
py -3.11 -m pytest -q
py -3.11 -m unittest discover -s tests
py -3.11 -m compileall -q app.py analysis api matching models services tests gemini config data
```

## Intentional non-features

This demo intentionally does **not** provide:

- Real authentication or real user accounts.
- Real GPS tracking or live telemetry.
- Real messaging/email/WhatsApp notifications.
- Automatic operational execution of any kind.
- External operational APIs (ERP, WMS, TMS).
- Invented financial-loss, SLA, or recovery-time metrics.

Everything is demonstrated with committed sample data and deterministic logic.

## Foundation layout

- `app.py`: single application entry point
- `api/`: HTTP boundary
- `models/`: domain models
- `services/`: orchestration services
- `detectors/`: disruption detection
- `matching/`: deterministic entity matching
- `analysis/`: deterministic impact analysis, prioritization, scenarios, coordination
- `gemini/`: Gemini integration boundary
- `evidence/`: traceable findings
- `storage/`: local persistence boundary
- `config/`: configuration boundary
- `tests/`: automated tests
- `data/`: local datasets and fixtures
- `frontend/`: frontend application surface

All operational facts and impact calculations come from repository data and deterministic Python logic. Gemini only interprets the unstructured notice; it never invents operational records, measurements, or decisions.