# Yellow Network Release Evidence Agent

The Yellow Network Release Evidence Agent imports a release-assessment dataset into Jira, investigates the Jira records for a selected target build, and creates an evidence-backed release-readiness report.

The intended flow is:

1. Import the source dataset into a dedicated Jira project.
2. Have the assessment agent find requirements and relevant evidence in Jira.
3. Use individual Jira tools to check evidence scope and outcomes.
4. Review the report, its Jira references, and the investigation trace.

Jira is the source of truth during assessment. The source JSON is an importer input and is not passed to the assessment agent as evidence.

## Architecture overview

```text
┌───────────────────────────────┐
│ React dashboard (Vite)        │
│ Import dataset | Assessment   │
└───────────────┬───────────────┘
                │ HTTP /api/*
┌───────────────▼───────────────┐
│ FastAPI local API             │
│ validates requests, runs jobs │
└───────┬─────────────┬─────────┘
        │             │
   import job     assessment job
        │             │
┌───────▼──────┐  ┌───▼────────────────┐
│ Importer     │  │ AI agent/tool loop │◄──── Gemini API or local Ollama
└──┬───────┬───┘  └───┬────────────────┘
   │       │          │ individual Jira tools
   │       │          ▼
   │       └───────► Jira Cloud ◄────────────┘
   │                   │
   ▼                   └── evidence, comments, links, status
SQLite import mappings

Assessment output: reports/assessment_*.json and reports/assessment_*.md
```

### Main components

- **React UI:** Provides Import and Assessment tabs, progress/status, report history, verdict filtering, Jira links, and a rerun action.
- **FastAPI:** Accepts import and assessment requests, validates them, starts background jobs, exposes job status and report endpoints, and keeps Jira/model credentials on the server.
- **Importer:** Reads and validates the dataset, creates or updates Jira issues, preserves comments and links, and synchronizes recorded Jira statuses where transitions allow.
- **Jira client:** Handles authenticated Jira REST API requests, including issue search and pagination.
- **Assessment agent:** Sends target details and the available tool definitions to the chosen model. The model selects individual tools; the agent executes them against Jira and returns each result to the model.
- **Evidence checks and finalizer:** Deterministic code checks scope and selected evidence conditions, validates the model’s references, and computes the final verdict.
- **Report generator:** Writes both human-readable Markdown and structured JSON. The JSON includes the tool-call trace used by the dashboard.

The API starts one import or assessment job at a time. Job state is held in memory while the API process runs; reports themselves are persisted as files.

## Import workflow and Jira data model

The dashboard previews the selected JSON file and sends the dataset to `POST /api/imports`. The API validates the dataset structure, duplicate source identifiers, and label before starting the importer.

For each dataset record, the importer:

1. Maps its `source_id` to a Jira issue key using SQLite.
2. Creates an issue if the source ID is new, or updates the mapped issue when re-importing.
3. Adds the dataset label (for example `tyn-release-assessment-v2`), a kind label such as `kind-requirement`, and a release label such as `release-R2.4`.
4. Preserves source ID, record kind, recorded status, and description in the Jira issue description.
5. Adds scenario comments with their original actor, role, and event timestamp kept in the comment text. Jira-native comment authors and timestamps are importer metadata, not the scenario actor/time.
6. Creates Jira issue links for dataset relationships.
7. Attempts to transition the Jira issue to its recorded status if Jira exposes a matching transition.

The dashboard’s re-import uses update mode: changed managed fields and statuses are synchronized, and only missing comments and links are appended. If the import reports errors, it does not automatically start an assessment. If there are changes and no import errors, it starts one automatically.

### What is stored where?

| Store | Contents |
|---|---|
| Jira | Imported issues, descriptions, labels, comments, relationships, and current Jira workflow status. The assessment agent retrieves evidence from here. |
| `db/state.db` | Import mapping (`source_id` → Jira key) and link tracking, used for idempotent imports. It is not the assessment evidence store. |
| `reports/` | Generated Markdown and JSON assessment reports, including the investigation trace. |
| `inputs/dataset.json` | Source data used by the importer only; it is not read by the assessment agent. |

The database schema also has an assessment-cache table, but report history in the current dashboard is loaded from the JSON report files in `reports/`.

## Assessment flow

1. The dashboard or CLI sends the project, dataset label, release, commit, service, environment, prompt version, and provider/model selection.
2. The agent starts a bounded model/tool loop (maximum 100 Jira tool calls per run).
3. The model is instructed to retrieve the manifest and requirements first, then inspect relevant Jira issues, comments, and links.
4. Each tool enforces the project and dataset-label scope when reading a specific Jira record. Linked issues outside that scope are not returned as evidence.
5. The model returns one proposed decision per discovered requirement and cites Jira issue/comment references.
6. The code finalizer checks the trace and citations, applies deterministic safeguards, and calculates the overall verdict. It does not simply accept the model’s overall verdict.
7. The report generator saves the result, reasoning, references, tool calls, durations, runtime, token usage where available, and any agent error.

### Individual Jira tools

Each callable tool has its own module under `tools/jira/`. The model chooses tools individually instead of sending the entire dataset to one all-in-one validator.

#### Discovery and retrieval

| Tool | Purpose |
|---|---|
| `get_manifest` | Finds the one manifest with the selected project, dataset label, and `kind-manifest` label; reads its target. The manifest target must match the requested target. |
| `search_requirements` | Finds requirements scoped by project, dataset label, `kind-requirement`, and target release. |
| `search_verification` | Searches scoped Jira records for verification evidence for the target release and optional service/environment. |
| `search_approvals` | Searches scoped Jira record text for approval, approved, or sign-off evidence. |
| `search_defects` | Searches scoped Jira record text for defect, bug, failure, or incident evidence. |
| `get_issue` | Reads one issue only if it belongs to the selected project and dataset label. |
| `get_comments` | Reads comments for a scoped issue. Scenario event details are read from comment text. |
| `get_linked_issues` | Reads linked records but returns only issues in the same project and dataset label. |

Searches and comments are paginated. Repeated pages or responses that indicate missing pages are treated as errors rather than silently accepted as complete results.

#### Individual evidence checks

Scope checks are run against the same source reference that the model intends to cite. A normal evidence reference is `TYN-42`; a comment reference is `TYN-42#comment-12345`.

| Tool | What it checks |
|---|---|
| `check_release` | The evidence text mentions the target release. |
| `check_commit` | The evidence text mentions the target commit. |
| `check_service` | The evidence text mentions the target service. |
| `check_environment` | The evidence identifies the target environment; a different or unspecified environment is not accepted. |
| `check_prompt_version` | The evidence mentions the target prompt version when the requirement depends on a prompt. |
| `check_verification_outcome` | Detects explicit verification results, including `X of Y cases pass`, partial pass counts, explicit `FAIL`, and aborted runs. |
| `check_approval_actor` | Requires the named scenario actor and exact role, and checks for explicit approval or rejection. Jira’s native author is not used as the scenario actor. |
| `check_open_defect` | Requires target release/commit/service/environment scope, an explicit failure, and a recognizable Jira workflow status. A resolved failure is not considered open; an unknown status is insufficient evidence. |
| `check_supersession` | Requires two comments on the same scoped issue, valid timezone-aware recorded event timestamps in the comment text, a later event, and an explicit reference to the earlier record. |

#### Decision safeguards

- A requirement can be `PASS`, `FAIL`, or `INSUFFICIENT_EVIDENCE`.
- Missing, aborted, out-of-scope, or inconclusive evidence is not automatically a failure.
- A model `PASS` requires a cited Jira reference with successful release, commit, service, and environment checks on that same reference. Prompt-dependent criteria also require a prompt-version check there.
- Cited evidence must have a successful evidence-specific check. Explicitly named approvals require a passing actor/role approval check.
- Criteria mentioning defects/exposure/breaches require a usable open/resolved Jira status check.
- A successful target-scoped deterministic failure overrides a model `PASS`.
- An earlier failed run is treated as superseded only when the later run has a valid chronological supersession, target scope checks, and a passing retest.
- The mandatory flag is read from the imported requirement record. Optional requirements do not affect the overall verdict.
- The overall verdict is recomputed from mandatory requirements: any `FAIL` → `BLOCKED`; otherwise any `INSUFFICIENT_EVIDENCE` → `NEEDS_REVIEW`; otherwise → `READY`.

The model still interprets the meaning of free-form acceptance criteria and evidence. The deterministic checks do not independently compute every domain-specific metric or threshold. Review each requirement’s reasoning and the trace rather than treating the model as a substitute for human release approval.

## Folder architecture

```text
yellow_network_release_agent/
├── main.py                     # CLI commands: import, validate, assess, web
├── requirements.txt            # Pinned Python dependencies
├── README.md
├── .gitignore                  # Ignores credentials, DB state, and generated reports
├── .env                        # Local credentials/settings; do not commit
├── inputs/
│   └── dataset.json            # Import source; not assessment context
├── importer/
│   ├── loader.py               # Loads and validates dataset JSON
│   ├── models.py               # Pydantic dataset/record models
│   ├── jira_importer.py        # Jira create/update/comments/status/link workflow
│   └── validator.py            # Checks imported Jira records against source
├── jira/
│   └── client.py               # Jira Cloud REST client and pagination
├── db/
│   └── store.py                # SQLite source-ID/Jira-key and link mappings
├── agent/
│   ├── agent.py                # Tool loop, trace, finalizer, provider selection
│   ├── gemini.py               # Gemini GenerateContent/function-call adapter
│   └── prompts.py               # Agent instructions and target prompt
├── tools/
│   ├── jira_tools.py           # Tool registry and dispatcher
│   └── jira/                   # One module/schema per Jira retrieval/check tool
├── evaluation/
│   ├── dimensions.py           # Deterministic scope and evidence checks
│   └── engine.py               # Requirement/overall verdict aggregation
├── reports/
│   └── assessment_*.{json,md}  # Generated reports (created at runtime)
├── frontend/
│   ├── api.py                  # FastAPI endpoints and background jobs
│   ├── app.py                  # Legacy Streamlit interface
│   └── web/
│       ├── package.json        # React/Vite dependencies and scripts
│       └── src/
│           ├── main.tsx        # React entry point
│           ├── App.tsx         # Import and assessment dashboard
│           └── style.css       # Dashboard styling
└── tests/                      # Unit and API tests; no live Jira needed
```

## Setup

### Prerequisites

- Python 3.11 is the project’s current tested environment.
- Node.js and npm for the React dashboard.
- A Jira Cloud project and Jira API token with permission to create/read/update issues, comments, links, and workflow transitions as needed.
- A Gemini API key, or a locally running Ollama server and a tool-calling model.

### Configure credentials

Create a local `.env` file in the project root. Do not commit it or share its contents.

```dotenv
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=your-jira-email
JIRA_API_TOKEN=your-jira-api-token
JIRA_PROJECT_KEY=TYN
JIRA_PROJECT_NAME=Yellow Network Release Assessment

# Select the model provider used when no provider is specified.
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.5-flash-lite

# Optional Ollama settings:
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:4b

# Optional paths:
DATASET_PATH=inputs/dataset.json
DB_PATH=db/state.db
REPORTS_DIR=reports
```

The `.env` file is loaded by the backend. Credentials are not sent to the browser. The Jira evidence selected by the agent is sent to the configured model provider for reasoning; use Ollama if you need the model inference itself to remain local. Reports may contain Jira evidence, so keep them private.

### Run the React dashboard

In PowerShell terminal 1, start the API:

```powershell
cd D:\yellow_network_release_agent
.\.venv311\Scripts\Activate.ps1
python main.py web
```

In PowerShell terminal 2, start Vite:

```powershell
cd D:\yellow_network_release_agent\frontend\web
npm ci
npm run dev
```

Open the local URL printed by Vite, usually `http://127.0.0.1:5173/`.

1. Go to **Import dataset** and select `inputs/dataset.json`.
2. Confirm the project key and start the import.
3. Review any import errors. A successful import with changes automatically starts an assessment.
4. In **Assessment**, review the verdict, requirement filter, Jira links, reasoning, and trace. Use **Rerun assessment** after Jira evidence changes.

The API binds to `127.0.0.1` by default. Avoid exposing it publicly; it has access to Jira credentials and reports. The dashboard supports one active import or assessment job at a time.

## Command-line usage

Activate the virtual environment from the project root:

```powershell
cd D:\yellow_network_release_agent
.\.venv311\Scripts\Activate.ps1
```

Useful commands:

```powershell
# Preview an import without writing to Jira
python main.py import --dry-run

# Import, creating new Jira records
python main.py import

# Re-import and update records already mapped in SQLite
python main.py import --update-existing

# Validate the existing Jira import
python main.py validate

# Assess the configured target using Gemini
python main.py assess --provider gemini

# Start the API for the React dashboard
python main.py web
```

The CLI defaults to `inputs/dataset.json`, project `TYN`, label `tyn-release-assessment-v2`, and target R2.4 (`b7a91f2`, `atlas-assistant`, `production`, prompt `p7`). Options vary by command:

- `import`: `--dataset`, `--project-key`, `--project-name`, `--dry-run`, `--force`, `--update-existing`, and `--db`.
- `validate`: `--dataset`, `--project`, and `--db`.
- `assess`: `--project`, `--label`, `--release`, `--commit`, `--service`, `--environment`, `--prompt-version`, `--provider`, `--model`, `--ollama-url`, and `--output-dir`.

Run `python main.py import --help`, `python main.py validate --help`, or `python main.py assess --help` for the full options.

`--force` clears local import mappings before reimporting, which can create duplicate Jira issues. It does not delete existing Jira issues. Use it only when you understand the consequences.

## API and reports

The local API provides:

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Health check |
| `POST /api/imports` | Queue a dataset import |
| `POST /api/assessments` | Queue an assessment |
| `GET /api/jobs/{job_id}` | Get import or assessment progress/result |
| `GET /api/reports` | List saved reports |
| `GET /api/reports/{report_id}` | Read one saved JSON report |

Each assessment writes a matching JSON and Markdown file under `reports/`. The JSON report includes target metadata, provider/model, runtime, token usage when available, overall and per-requirement verdicts, Jira references, tool calls and results, and any agent error. The Markdown report is the human-readable version.

An API/model error can still leave a report containing an agent warning. Treat that run as incomplete and review the warning before relying on its verdict.

## Gemini rate limits

Gemini may return HTTP 429 when the project exceeds its per-minute quota. The agent resends the conversation history with each model request, so long tool traces or repeated runs can consume input tokens quickly. Wait for the retry interval in the error, avoid starting concurrent/repeated reruns, and check the project’s current quota in Google AI Studio. Higher usage may require a higher quota or billing plan.

## Tests and build

Run the Python tests:

```powershell
.\.venv311\Scripts\python.exe -m pytest
```

Run Ruff:

```powershell
.\.venv311\Scripts\python.exe -m ruff check .
```

Build the React application:

```powershell
cd D:\yellow_network_release_agent\frontend\web
npm run build
```

## Known limitations

- The model interprets free-form acceptance criteria; deterministic checks cover evidence scope and selected outcome conditions, not every possible business-specific metric.
- Scope checks rely on the evidence text containing the target values. Different wording or missing metadata can result in `INSUFFICIENT_EVIDENCE`.
- Jira status/category must be recognizable for the open-defect check. Unknown workflow states remain inconclusive.
- The Gemini conversation context grows during tool use, and the tool loop is bounded at 100 calls. Large Jira responses or repeated assessments can hit model token quotas.
- A report records what the agent found during that run; rerun after importing or editing relevant Jira evidence.
