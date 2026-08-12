# 02 — Architecture

Three tiers, per CLAUDE.md. The separation is not stylistic: the compute tier has
no durable storage, so state has to live elsewhere, and the observability tier has
to reach the compute tier from outside. See docs/adr/001-deployment-topology.md.

## The three tiers

```
                          ┌────────────────────────────────────────┐
                          │  CLINIC (browser / n8n form)           │
                          │  receptionist · doctors                │
                          └───────────────────┬────────────────────┘
                                              │ HTTPS
                                              v
 ══════════════════════════════════════════════════════════════════════════════
  TIER 1 — COMPUTE                    Hugging Face Docker Space, port 7860
                                      stateless · UID 1000 · only /tmp writable
 ══════════════════════════════════════════════════════════════════════════════
      ┌──────────────────────────────────────────────────────────────────┐
      │  Docker container : FastAPI (Python 3.12)                        │
      │                                                                  │
      │   src/api/routes/     POST /ask   POST /intake   history lookup  │
      │                       GET /health   GET /metrics                 │
      │        │                                                         │
      │        v                                                         │
      │   src/agents/         OpenAI Agents SDK                          │
      │                       4 tools + guardrails                       │
      │        │                                                         │
      │        ├──> src/ml/            scikit-learn triage model         │
      │        │      ^                (loaded into memory at startup)   │
      │        │      │                                                  │
      │        ├──> src/pipeline/build_features.py   ← THE ONLY place    │
      │        │                                       features exist    │
      │        ├──> kb/               approved first-aid text            │
      │        └──> src/common/db.py  the only path to durable state     │
      └───────────────────────────┬──────────────────────────────────────┘
                                  │ DATABASE_URL (TLS)
                                  v
 ══════════════════════════════════════════════════════════════════════════════
  TIER 2 — STATE                                            managed Postgres
                                                   all durable data lives here
 ══════════════════════════════════════════════════════════════════════════════
      ┌──────────────────────────────────────────────────────────────────┐
      │  patients          patient_features      inference_log           │
      │  alert_history     model_registry                                │
      └──────────────────────────────────────────────────────────────────┘
                                  ^
                                  │ read (loaded once by the pipeline)
                       data/raw/patients.xlsx  — 20 rows, 11 columns
                                                 (operator's machine, step 00/08)

 ══════════════════════════════════════════════════════════════════════════════
  TIER 3 — OBSERVABILITY                    local docker compose, operator's host
                       reaches INTO the public Space over HTTPS (pull, not push)
 ══════════════════════════════════════════════════════════════════════════════
      ┌──────────────┐   scrape GET /metrics over HTTPS
      │  Prometheus  │ ─────────────────────────────────────> Space (Tier 1)
      └──────┬───────┘
             │ query                    ┌──────────────┐
             ├─────────────────────────> │   Grafana    │  dashboards, panels
             │                           └──────────────┘
             │ fire                      ┌──────────────┐   webhook
             └─────────────────────────> │ Alertmanager │ ──────────┐
                                         └──────────────┘           │
      ┌──────────────┐  ship logs   ┌───────────────┐               v
      │   Filebeat   │ ───────────> │ Elasticsearch │      ┌────────────────┐
      └──────────────┘              └───────┬───────┘      │      n8n       │
                                            │              │  walk-in flow, │
                                     ┌──────v──────┐       │  notifications,│
                                     │   Kibana    │       │  alert routing │
                                     └─────────────┘       └────────┬───────┘
                                                                    │ notify
                                                                    v
                                                        Dr. Ali  ·  Dr. Sara
```

DECISION NEEDED: Prometheus pulls `GET /metrics` from a **public** Space. How is
that endpoint protected, and how does Prometheus authenticate to it? Left open,
the platform's internal metrics are world-readable.

DECISION NEEDED: Filebeat ships logs to Elasticsearch, but the Space's stdout is
inside Hugging Face's infrastructure and `/tmp` is the only writable path. How do
container logs actually reach Filebeat running on the operator's host — does the
Space push them somewhere, does Filebeat pull from the HF logs API, or does the
Space expose a log endpoint? This is step 11's central mechanical problem and it
is not decided by CLAUDE.md.

DECISION NEEDED: The observability stack runs on the operator's laptop via docker
compose. When that laptop is closed, nothing is scraping, nothing is alerting, and
`emergency` escalations routed through n8n stop. Is that accepted, or does the
notification path need to be independent of the local stack?

## Component responsibilities

### Tier 1 — Compute (the Space)

| Component | Responsibility | Explicitly not its job |
| --- | --- | --- |
| `src/api/routes/` | HTTP surface. Validate input against Pydantic models from `src/api/schemas.py`, emit one metric and one log line per request, delegate. | Business logic, feature computation, direct SQL. |
| `src/api/schemas.py` | Every request and response shape, as Pydantic models. Single source of truth for the API contract. | Persistence, validation of clinical meaning. |
| `src/agents/` | The four tools, the guardrails, the loop and refusal policy (docs/04-agent-spec.md). | Diagnosing, naming medications or doses, inventing first-aid text. |
| `src/ml/` | Load the trained scikit-learn model, run inference, return a label plus confidence. Model registry interaction. | Computing features (rule 2). Deciding escalation policy. |
| `src/pipeline/build_features.py` | Compute every feature, once, for both training and serving. | Anything else. Nothing outside this file computes a feature. |
| `src/pipeline/` (rest) | Validate `patients.xlsx`, load it into Postgres, populate `patient_features`. | Serving traffic. |
| `src/common/config.py` | Read `DATABASE_URL`, `OPENAI_API_KEY`, `ENVIRONMENT` from the environment. | Holding a default secret. Ever. |
| `src/common/db.py` | The only path to Postgres. Connection handling, queries, writes. | Business rules. |
| `src/ops/` | Whitelisted operational actions and incident handlers (rule 7, docs/05-ops-spec.md). | Any action not on the whitelist. Unknown action = refuse. |
| `kb/` | Doctor-approved first-aid text, served verbatim. | Being generated, extended, or paraphrased at runtime. |

The container is stateless. It may write to `/tmp` and nowhere else. It runs as
UID 1000, so nothing in the image may require root at runtime. Restarting the Space
must lose nothing but in-flight requests.

DECISION NEEDED: The model artifact. `models/*.pkl` is gitignored and the Space has
no persistent storage, so where does the trained model come from at container
start — baked into the Docker image at build time, pulled from `model_registry` in
Postgres as bytes, or fetched from an external artifact store? This decides both
step 01 and step 09 and it needs answering before either.

DECISION NEEDED: Which scikit-learn estimator, and is the choice recorded as an
ADR? Step 01 says "triage model" and the approved list says scikit-learn, but the
estimator, the text-vectorisation approach for symptoms, and the calibration
method (needed for the 0.5 confidence threshold in docs/04-agent-spec.md to mean
anything) are all undecided.

DECISION NEEDED: Which OpenAI model backs the agent, and is it pinned to a
specific version? An unpinned model silently changes the behaviour that the 30
eval cases certify.

### Tier 2 — State (managed Postgres)

Everything durable. Five tables, defined in docs/03-data-contract.md:
`patients`, `patient_features`, `inference_log`, `alert_history`,
`model_registry`.

Reached only through `src/common/db.py`, only over `DATABASE_URL`, never from
anywhere else in the codebase.

DECISION NEEDED: Which managed Postgres provider, which region, and which version?
Region matters for the data-protection question raised in docs/01-PRD.md.

DECISION NEEDED: How do schema migrations happen? Five tables need creating before
step 00 can load anything, and the approved tool list contains no migration tool.
Options: plain SQL files in `scripts/` applied by hand, SQL executed by
`src/pipeline/`, or Ansible. Adding Alembic would need a rule-1 exception.

DECISION NEEDED: Is `data/raw/patients.xlsx` loaded once, or re-loaded on a
schedule? And if a row is edited in Postgres and also in the Excel file, which
wins? Without an answer the Excel is both "source of truth" and stale.

### Tier 3 — Observability (local docker compose)

| Component | Responsibility |
| --- | --- |
| Prometheus | Scrape `GET /metrics` from the Space over HTTPS. Hold the recording and alert rules (docs/05-ops-spec.md). |
| Grafana | Dashboards over Prometheus. Panels for latency, triage-decision mix, error rate, model health. Provisioned from `infra/grafana/dashboards/`. |
| Alertmanager | Route fired alerts. Deduplicate, group, and forward to n8n. |
| Elasticsearch | Store the structured JSON logs (step 11). |
| Filebeat | Ship logs into Elasticsearch. |
| Kibana | Search the logs by `request_id`. |
| n8n | The walk-in intake flow, doctor routing, emergency notification to both doctors, and alert fan-out. |
| Terraform | Stand the whole local stack up (step 04). |
| Ansible | Configure it (step 04). |

DECISION NEEDED: Does n8n sit in the request path for `emergency` escalation, or
alongside it? If the Space calls n8n synchronously, a local n8n being down blocks
an emergency response; if asynchronously, the Space needs a durable outbox in
Postgres — which is a table the five in docs/03-data-contract.md do not include.

DECISION NEEDED: How do both doctors actually get notified — email, SMS,
WhatsApp, Slack, a phone call? SC-3 requires 100% escalation, which cannot be
verified without knowing the channel and whether it returns a delivery receipt.

## Data flow — walk-in patient, form submission to logged decision

A patient walks in. The receptionist has the patient's ID (or finds it). This is
the primary path through the system.

```
 1. RECEPTIONIST submits the intake form
       patient_id, age, symptom text
       (n8n walk-in form, or directly to the API)
                    │
                    │  HTTPS POST /intake
                    v
 2. FASTAPI validates against the Pydantic request model
       - request_id generated here, attached to everything downstream
       - metric incremented, one log line written (IDs only, never symptom text)
       - malformed input rejected at this boundary
                    │
                    v
 3. AGENT starts, guardrails active
       loop policy: max 6 tool calls · max 1 retry per tool · 15 s hard timeout
                    │
        ┌───────────┼──────────────┬────────────────┐
        v           v              v                v
   get_patient  get_history     triage          first_aid
        │           │              │                │
        │           │              │                └─> kb/  (approved text only)
        │           │              │
        │           │              └─> src/ml/  ──> src/pipeline/build_features.py
        │           │                  scikit-learn      (the only feature code)
        │           │                  → label + confidence
        │           │
        └───────────┴─> src/common/db.py ─> Postgres: patients, patient_features
                    │
                    v
 4. GUARDRAILS check the candidate response
       - no diagnosis, no medication name, no dose        → else refuse
       - never contradicts an `emergency` label           → else refuse
       - data belongs to the requested patient only       → else refuse
       - prompt-injection attempt                         → refuse AND log
       - confidence < 0.5                                 → escalate to a human
                    │
                    v
 5. DECISION recorded
       src/common/db.py → INSERT INTO inference_log
       patient_id, request_id, label, confidence, model_version, timestamp
       (no name, no notes, no raw symptom text — rule 4)
                    │
                    ├───────────────── if label == emergency ───────────────┐
                    │                                                       │
                    v                                                       v
 6a. RESPONSE to the receptionist                        6b. ESCALATION
       triage label                                          workflow layer (n8n)
       approved first-aid guidance                           notifies BOTH doctors
       for `emergency`: the emergency instruction            Dr. Ali AND Dr. Sara
       verbatim, and it is never softened                     (SC-3: 100%)
                    │                                                       │
                    v                                                       v
 7. OBSERVED
       Prometheus scrapes the counters and histograms this request touched
       Filebeat ships the JSON log line (request_id) → Elasticsearch → Kibana
       Grafana panels update; alert rules evaluate
```

Non-emergency cases stop at step 6a and are queued to the owning doctor from the
`doctor` column (Dr. Ali or Dr. Sara). Emergency cases do both 6a and 6b, always.

DECISION NEEDED: At step 2, where does the intake form live — an n8n-hosted form,
a page served by FastAPI, or something the clinic already uses? CLAUDE.md places
the "n8n walk-in flow" in step 03 without saying whether n8n hosts the form or
merely receives it.

DECISION NEEDED: At step 3, what happens when the patient is not in Postgres at
all (a genuinely new walk-in)? All 20 known patients have IDs; a new patient has
none. Does `POST /intake` accept an unknown or absent `patient_id`, triage on
symptoms and age alone, and if so does it create a `patients` row?

DECISION NEEDED: At step 5, is the write to `inference_log` on the critical path?
If Postgres is unreachable, does the receptionist still get a triage answer (fast,
but unlogged) or an error (logged-or-nothing)? For an `emergency` this matters:
refusing to answer because a database is down is its own hazard.

DECISION NEEDED: At step 6b, is escalation retried, and for how long? SC-3 demands
100% and a single best-effort notification cannot promise it.

## Boundaries that must not be crossed

1. Features exist in `src/pipeline/build_features.py` only. Training imports it.
   Serving imports it. Nothing recomputes anything. (Rule 2)
2. Durable state moves only through `src/common/db.py`. (Rule 6)
3. The compute tier writes only to `/tmp`. (Rule 6)
4. Secrets come only from the environment. (Rule 3)
5. Logs carry IDs and decisions only. (Rule 4)
6. Tier 3 pulls from Tier 1 over HTTPS. Tier 1 never depends on Tier 3 being up
   in order to serve a triage decision — subject to the open question about n8n
   above.
7. Operational actions come from the whitelist. Unknown action = refuse. (Rule 7)
