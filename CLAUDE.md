# ClinicOps

## What this project is

ClinicOps is a clinic triage platform, built as an enterprise platform-engineering
exercise.

The clinic is small: two doctors (Dr. Ali, Dr. Sara) and 20 patients held in a single
Excel file at `data/raw/patients.xlsx`. The clinic wants two things:

1. Look up patient history instantly.
2. Enter symptoms and get back a triage decision plus approved first-aid guidance.

Everything else in this repo — the pipeline, the model, the agent, the IaC, the CI/CD,
the observability and incident tooling — exists to deliver those two capabilities in a
way that is operable, testable, and safe.

## Architecture — three tiers, do not deviate

1. **COMPUTE**: FastAPI in Docker on a Hugging Face Docker Space, port 7860.
   Stateless. Only `/tmp` is writable. Runs as user UID 1000.
2. **STATE**: managed Postgres reached via `DATABASE_URL`. All durable data lives here.
3. **OBSERVABILITY**: Prometheus, Grafana, Alertmanager, Elasticsearch, Kibana, n8n
   running locally via docker compose, reaching into the public Space over HTTPS.

## Approved tools — one tool per job, no alternatives

Python 3.12 · FastAPI · scikit-learn · OpenAI Agents SDK · Docker ·
Terraform · Ansible · GitHub Actions · Prometheus · Grafana · ELK · Postgres.

**Forbidden**: LangChain, PyTorch, Flask, Django, Celery, MongoDB, LlamaIndex,
or any substitute for the above.

## Non-negotiable rules

1. One tool per job. If you think an alternative is needed, stop and ask me.
2. Features are computed in exactly ONE place: src/pipeline/build_features.py.
   Training and serving both import it. Never recompute a feature elsewhere.
3. No secrets in code. Environment variables only.
4. Never log patient names, notes, or raw symptom text. IDs and decisions only.
5. The agent never diagnoses and never names a medication or a dose.
6. Compute is stateless. All durable state goes to Postgres via src/common/db.py.
   Never write to local disk except /tmp.
7. Every operational action is whitelisted. Unknown action = refuse.

## Repo layout

```
src/
  common/          config, db helper, shared utilities
  pipeline/        Excel validation, Postgres load, build_features.py
  ml/              triage model training, registry, evaluation
  agents/          OpenAI Agents SDK tools, guardrails
  api/
    routes/        FastAPI routers
  ops/             whitelisted operational actions, incident handlers
docs/adr/          architecture decision records
specs/             step-00.md .. step-11.md — one spec per build step
data/
  raw/             patients.xlsx (source of truth input)
  processed/       derived data (gitignored)
models/            trained artifacts (*.pkl gitignored)
kb/                approved first-aid knowledge base
infra/
  ansible/         Ansible playbooks
  grafana/
    dashboards/    Grafana dashboard JSON
.github/workflows/ GitHub Actions pipelines
tests/             pytest suite
evals/             eval cases and harness
scripts/           operator-facing scripts
```

## The 12 build steps

One per session, in order.

| Step | Focus |
| --- | --- |
| 00 | foundation: config, db helper, load the Excel into Postgres |
| 01 | triage model: symptoms + age -> self_care \| see_doctor_today \| emergency |
| 02 | agent: 4 tools + guardrails + POST /ask |
| 03 | workflows: POST /intake, doctor routing, n8n walk-in flow |
| 04 | IaC: Terraform for the local ops stack + Ansible + an AI plan reviewer |
| 05 | CI/CD: change-risk score, AI failure analysis, quality gate, auto-rollback |
| 06 | observability: Prometheus metrics, Grafana panels, health-summary agent |
| 07 | incidents: alert rules, alert triage, RCA agent, whitelisted actions |
| 08 | data pipeline: validate the Excel, load Postgres, build features, inference_log |
| 09 | MLOps: registry, quality gate, shadow/canary, rollback, drift, model card |
| 10 | evaluation: 30 eval cases including adversarial, eval harness, ADRs |
| 11 | logging: JSON logs with request_id, Filebeat to Elasticsearch, Kibana |

## Coding conventions

- **Type hints everywhere.** Every function signature is annotated, including return
  types. No bare `dict` or `list` where a specific type is known.
- **Pydantic models live in `src/api/schemas.py`.** Request and response bodies are
  Pydantic models defined there — not inline dicts, not defined per-route.
- **Every agent tool carries a docstring written for a reader.** It explains what the
  tool does, what it needs, what it returns, and when the agent should reach for it.
  Prose a colleague can follow, not a restated signature.
- **Every endpoint gets three things**: a Prometheus metric, a log line, and a test.
  An endpoint missing any of the three is not finished.
- Features come from `src/pipeline/build_features.py` and nowhere else (rule 2).
- Durable state goes through `src/common/db.py` (rule 6).
- Config and secrets come from environment variables via `src/common/config.py`
  (rule 3). Add new variables to `.env.example` with an empty value.
- Logs carry IDs and decisions. Never patient names, notes, or raw symptom text
  (rule 4).

## How to work with me

- **Read the relevant `specs/step-NN.md` first.** Before writing anything, read that
  step's spec. It is the contract for the session.
- **Implement ONE spec per session.** One step, start to finish.
- **Never work ahead.** Do not build parts of step N+1 because they seem convenient.
  If a later step's work looks necessary now, stop and ask.
- **End every session with three things:**
  1. The files changed.
  2. The manual verification command — what I run to confirm it works.
  3. Anything you assumed.
- If a rule above blocks the task, or an approved tool seems like the wrong fit, stop
  and ask rather than substituting.
