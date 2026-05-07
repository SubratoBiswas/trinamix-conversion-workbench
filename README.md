# Trinamix Conversion Workbench

**AI-powered Oracle Fusion data conversion & migration platform.**

A production-grade local-runnable application that helps consultants and
implementers move legacy data into Oracle Fusion using FBDI templates. It
parses Oracle's FBDI Excel templates, profiles legacy extracts, suggests
field mappings with an explainable AI engine, applies cleansing rules,
generates Fusion-ready CSVs, simulates loads, and provides an audit trail
across the entire pipeline.

![Status](https://img.shields.io/badge/status-running-success)
![Backend](https://img.shields.io/badge/backend-FastAPI%20%C2%B7%20SQLAlchemy%20%C2%B7%20SQLite-blue)
![Frontend](https://img.shields.io/badge/frontend-React%2018%20%C2%B7%20Vite%20%C2%B7%20Tailwind-blue)
![AI](https://img.shields.io/badge/AI-rule--based%20default%20%C2%B7%20Anthropic%20%C2%B7%20OpenAI-violet)

---

## Highlights

- **Real Oracle FBDI parsing.** The parser handles the messy reality of
  Oracle Item / Customer / Sales Order templates: instructions sheet,
  metadata rows, per-module Required flags, "Character (300)" type
  conventions. Tested against a real `ScpItemImportTemplate.xlsm` with 107
  fields, 14 of which auto-detect as required.
- **Pluggable AI mapping engine.** Defaults to a rule-based provider that
  needs no API key — uses Jaccard token similarity, semantic dictionaries,
  type compatibility, value affinity (date/numeric/code/identifier
  detection) and cardinality boosting. Optional Anthropic or OpenAI
  providers when an API key is configured.
- **Full conversion pipeline.** Profile → AI map → review → cleanse →
  transform → validate → generate FBDI output → simulate load → audit.
  Each phase has a real implementation backing it.
- **Explainable mappings.** Every suggestion shows confidence, reasoning
  ("semantic keyword match; high-cardinality identifier-like values"),
  sample source values, and a suggested transformation rule.
- **Visual workflow builder.** Drag-and-drop dataflow editor (ReactFlow)
  with palette of conversion components. Each node maps to a real backend
  operation when run.
- **Dependency-aware load simulation.** Errors are categorised, root-caused,
  and tied to upstream business objects (e.g. UOM master must be loaded
  before Item).

---

## Quick Start

### Option A — Docker Compose (single command)

```bash
docker compose up --build
```

Then visit:

- Frontend: <http://localhost:8080>
- Backend API: <http://localhost:8000/api/health>
- Default login: `admin@trinamix.com` / `admin123`

The first start auto-seeds an admin user, a sample legacy CSV, the real
Oracle FBDI Item Master template (107 fields), 11 conversion-order
dependencies, and one demo project bound to all of them.

### Option B — Local development (no Docker)

**Backend** (Python 3.12+):

```bash
cd backend
python -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend** (Node 20+):

```bash
cd frontend
npm install
npm run dev                        # opens http://localhost:5173
```

The Vite dev server proxies `/api` to `http://localhost:8000`, so just
visit <http://localhost:5173> and sign in.

---

## Project Layout

```
trinamix-conversion-workbench/
├── backend/                        # FastAPI + SQLAlchemy + SQLite
│   ├── app/
│   │   ├── ai/                    # Mapping providers (rule_based, anthropic, openai)
│   │   ├── load/                  # Fusion load simulator
│   │   ├── models/                # ORM models
│   │   ├── parsers/               # FBDI .xlsm parser, tabular profiler
│   │   ├── routers/               # REST endpoints
│   │   ├── schemas/               # Pydantic schemas
│   │   ├── seed/                  # Demo data + sample files
│   │   ├── services/              # Business logic layer
│   │   ├── transformations/       # Rule engine (TRIM, VALUE_MAP, DATE_FORMAT, …)
│   │   ├── validation/            # Cleansing + validation engines
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── tests/test_app.py          # End-to-end pipeline test
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/                       # React + TS + Vite + Tailwind
│   ├── src/
│   │   ├── api/                   # Axios client + endpoint wrappers
│   │   ├── components/            # Layout, UI primitives, workflow node
│   │   ├── lib/                   # cn(), formatters, status helpers
│   │   ├── pages/                 # 15 page components
│   │   ├── store/                 # Zustand auth store
│   │   ├── types/                 # Shared TS types (mirror Pydantic)
│   │   ├── App.tsx                # Routes + auth guard
│   │   ├── main.tsx
│   │   └── index.css
│   ├── tailwind.config.js         # Trinamix design tokens
│   ├── package.json
│   ├── nginx.conf
│   └── Dockerfile
│
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Architecture

```
   ┌─────────────────────────┐         ┌──────────────────────────┐
   │  React SPA (port 5173/  │  /api   │  FastAPI (port 8000)     │
   │  80 in docker)          ├────────►│                          │
   │  - Dashboard            │         │  Routers ── Services     │
   │  - Datasets / FBDI      │         │     │           │        │
   │  - Mapping Review       │         │     ▼           ▼        │
   │  - Transformation       │         │  Pydantic    Engine      │
   │  - Cleansing/Validation │         │  schemas     modules:    │
   │  - Output Preview       │         │              parsers/    │
   │  - Load Dashboard       │         │              ai/         │
   │  - Workflow Builder     │         │              transforms/ │
   │  - Dependency Graph     │         │              validation/ │
   │  - Audit                │         │              load/       │
   └─────────────────────────┘         │     │                    │
                                       │     ▼                    │
                                       │  SQLAlchemy → SQLite     │
                                       │  uploads/ outputs/       │
                                       └──────────────────────────┘
```

### The conversion pipeline

```
Legacy CSV/XLSX ──► Profile (type inference, null %, distinct, samples)
        │
        │   FBDI .xlsm ──► Parse (sheets, fields, types, lengths, required)
        │           │
        ▼           ▼
   ┌────────────────────────┐
   │  AI Mapping Engine     │  Jaccard + semantic + type + value affinity
   │  (pluggable)           │  → MappingSuggestion(confidence, reason, rule)
   └────────────────────────┘
        │
        ▼
   ┌────────────────────────┐  Approve / override / reject mappings
   │  Mapping Review        │  Add transformation rules per field
   └────────────────────────┘
        │
        ▼
   ┌────────────────────────┐  Source-side checks: nulls, casing, dupes,
   │  Cleansing             │  invalid dates, unmapped requireds
   └────────────────────────┘
        │
        ▼
   ┌────────────────────────┐  FBDI compliance: types, lengths, formats,
   │  Validation            │  required-field coverage
   └────────────────────────┘
        │
        ▼
   ┌────────────────────────┐  Apply mappings + rule pipelines + defaults
   │  Generate Output       │  → Fusion-ready CSV/XLSX
   └────────────────────────┘
        │
        ▼
   ┌────────────────────────┐  Categorize errors, root-cause, tie to
   │  Simulate Load         │  upstream object dependencies
   └────────────────────────┘
```

---

## AI provider configuration

The default rule-based mapper requires **no API key** and is good enough
for most demos. To switch on an LLM provider, copy `.env.example` to
`.env` (project root or `backend/`) and set:

```ini
# rule_based (default), anthropic, or openai
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# or:
# AI_PROVIDER=openai
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini
```

LLM providers fall back to the rule-based mapper if the API call fails,
so your workflow never breaks.

---

## Working with your own data

### Upload a custom legacy extract

Datasets → Upload Dataset → drop `.csv`, `.xlsx`, or `.xls`. The profiler
will infer types, compute null %, distinct counts, and sample values.

### Upload a custom Oracle FBDI template

FBDI Templates → Upload Template → drop the Oracle `.xlsm`. The parser
extracts each field's name, description, type, max length, and required
flag from the template's metadata rows. You can manually correct any
field via the "Edit" action on the detail page.

### Run a conversion

1. Create a project pairing the dataset with an FBDI template.
2. Open the project, click **AI Auto-Map**.
3. Open Mapping Review, approve / override / reject suggestions.
4. (Optional) Add Transformation rules in the Transformation Studio.
5. Run **Cleansing** → **Validation** → **Generate Output** → **Simulate Load**.
6. Download the FBDI-ready CSV from the project page.

### Build a reusable dataflow

Dataflows → New Dataflow → bind to a project → drag nodes from the palette
(Dataset, AI Auto Map, Transform, Validate, Preview Output, Load to Fusion)
→ wire them up → **Save** then **Run**. Each node executes the real backend
operation and reports its status back to the canvas.

---

## API reference (selected)

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Service health, version, AI provider in use |
| `/api/auth/login` | POST | JWT login |
| `/api/datasets/upload` | POST | Upload + profile a dataset |
| `/api/fbdi/upload` | POST | Upload + parse an FBDI template |
| `/api/projects` | POST | Create a conversion project |
| `/api/projects/{id}/suggest-mapping` | POST | Run AI mapping engine |
| `/api/mappings/{id}/approve` | PUT | Approve a mapping |
| `/api/projects/{id}/profile-cleansing` | POST | Run cleansing engine |
| `/api/projects/{id}/validate` | POST | Run validation engine |
| `/api/projects/{id}/generate-output` | POST | Generate FBDI CSV/XLSX |
| `/api/projects/{id}/simulate-load` | POST | Simulate Fusion load |
| `/api/projects/{id}/download-output` | GET | Download converted file |
| `/api/workflows/{id}/run` | POST | Execute a saved dataflow |
| `/api/dashboard/kpis` | GET | Dashboard KPI aggregation |

Full interactive docs at <http://localhost:8000/docs> when the backend is
running.

---

## Running the test suite

```bash
cd backend
rm -f *.db                                # tests use an isolated db; clean first
python -m pytest tests/ -v
```

Three end-to-end tests verify: health endpoint, login + seed integrity,
and the entire conversion flow (suggest mapping → approve → cleansing →
validation → generate output → simulate load).

---

## Design system

- **Sidebar** — `#0F172A` deep slate, `#6366F1` indigo accent on active items.
- **Canvas** — `#F8FAFC` page background, `#FFFFFF` card surfaces.
- **Typography** — Inter 13–14 px for tables and body, semibold 600 for KPIs.
- **Color used purposefully** — only on status pills, severity icons,
  confidence bars, KPI deltas, and chart segments. Tables stay neutral.
- **Density** — 13 px tables, compact action menus, sticky table headers
  and 70 vh modal scroll regions for production-grade ergonomics.

---

## Notes & caveats

- This is a local-runnable workbench, not a SaaS deployment. The default
  JWT secret is for development only — change it in `.env` for any
  shared environment.
- SQLite is used so the demo is fully self-contained. For production
  loads with real data volumes, swap `DATABASE_URL` to PostgreSQL —
  SQLAlchemy will work transparently.
- The "Simulate Load" engine is **not** a live Oracle Fusion connector —
  it categorises validation issues into Fusion-style error buckets so you
  can practise interpreting load failures. Replacing it with a real
  HCM/SCM Data Loader bridge is a clearly bounded extension point in
  `app/load/simulator.py`.
- The bundled FBDI parser is conservative — it never invents fields. If
  a column's required-flag column doesn't exist, the field is left as
  optional and you can correct it manually via the field-edit modal.
"# trinamix-conversion-workbench" 
