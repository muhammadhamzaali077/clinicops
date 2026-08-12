# ADR 002 — One tool per job

- **Status:** Accepted (mandated by CLAUDE.md, non-negotiable rule 1)
- **Date:** DECISION NEEDED: date this decision was taken
- **Deciders:** DECISION NEEDED: who
- **Supersedes / superseded by:** —

## Context

ClinicOps spans twelve build steps: data pipeline, ML, an agent, IaC, CI/CD,
observability, incident response, MLOps, evaluation, and logging. Each of those
areas has a dozen credible tools, and most projects of this shape accumulate several
per area — two HTTP frameworks because one was there first, three ways to run a
background job, a second vector store that arrived with a tutorial.

One platform engineer operates this system, part-time, from outside the clinic. The
cost of a second tool is not its install; it is a second failure mode to learn, a
second upgrade path, a second set of credentials, and a second thing to read at 2am
during an incident. On a clinical system, that cost is paid in response time when
something is wrong.

## Decision

Exactly one approved tool per job. The list is closed.

| Job | Tool |
| --- | --- |
| Language | **Python 3.12** |
| HTTP / API | **FastAPI** |
| Machine learning | **scikit-learn** |
| Agent framework | **OpenAI Agents SDK** |
| Packaging / runtime | **Docker** |
| Infrastructure provisioning | **Terraform** |
| Configuration management | **Ansible** |
| CI/CD | **GitHub Actions** |
| Metrics and alerting | **Prometheus** (with Alertmanager) |
| Dashboards | **Grafana** |
| Logs | **ELK** (Elasticsearch, Filebeat, Kibana) |
| Durable state | **Postgres** |
| Workflow automation | **n8n** |

Explicitly forbidden, with the job each would have duplicated:

| Forbidden | Would have duplicated | Why refused |
| --- | --- | --- |
| **LangChain** | OpenAI Agents SDK | A second agent abstraction. Its indirection makes the four tools' behaviour harder to audit, and auditability is the point of docs/04-agent-spec.md. |
| **LlamaIndex** | OpenAI Agents SDK + Postgres | A second retrieval and orchestration layer. The `kb/` first-aid content is small, doctor-approved, and served verbatim — it needs no retrieval framework, and a framework that paraphrases is a rule-5 hazard. |
| **PyTorch** | scikit-learn | A deep-learning stack for a three-class classifier over 20 patients' worth of symptom text. Adds GPU assumptions, a large image, and a training story nobody can debug quickly. |
| **Flask** | FastAPI | A second HTTP framework. FastAPI's Pydantic integration is load-bearing here: the API contract in docs/03-data-contract.md *is* the schema definitions. |
| **Django** | FastAPI + Postgres access | Brings an ORM, a migration system, an admin, and a request model that duplicate decisions already made. The admin in particular would expose patient data through a surface nobody specified. |
| **Celery** | n8n (+ FastAPI) | A second job runner, and one that needs a broker — which is durable state outside Postgres, contradicting ADR 001. |
| **MongoDB** | Postgres | A second datastore. The data is relational (patients → features → decisions) and the audit trail needs constraints, not schema flexibility. |

## Rationale

1. **Fewer failure modes than components.** Each tool is one thing to monitor, patch,
   and understand. Two tools for one job is more than twice the operational surface,
   because the interaction between them is a third thing.
2. **Auditability.** This system makes clinical decisions and must be able to explain
   any one of them afterwards. Each layer of framework indirection is a layer between
   a decision and its explanation. This is the reason LangChain and LlamaIndex are
   refused specifically, rather than as a general preference.
3. **Matching the problem's actual size.** 20 patients, 11 columns, three output
   classes. scikit-learn is the right size for that; PyTorch is not. Tool choice
   tracks the problem, not the résumé.
4. **One obvious place for everything.** The same instinct as rule 2 (features exist
   in exactly one file) and rule 6 (state moves through exactly one module). A
   newcomer asking "where do metrics come from" has one answer.
5. **A closed list forces the conversation.** Rule 1 ends with "if you think an
   alternative is needed, stop and ask me". The point is not that the list is
   perfect — it is that adding to it is a decision made deliberately, by a human,
   and recorded, rather than one that happens quietly in a requirements file.

## Consequences

**Good:**

- A short, learnable stack. One engineer can hold all of it.
- Every "which tool should I use for X" question is already answered.
- Dependency surface stays small, which matters for a container serving patient data.
- Nothing arrives without an ADR, so the stack has a written history.

**Costs, accepted:**

- Some jobs will be done with a tool that is merely adequate rather than ideal. That
  is the trade, taken knowingly.
- Gaps in the list have to be resolved by asking, which is slower than installing
  something. Known gaps already visible:
  - **Postgres schema migrations** — no migration tool is on the list
    (docs/02-architecture.md).
  - **A Postgres driver / query layer** — `src/common/db.py` needs one; whether
    `psycopg` counts as part of "Postgres" or as a separate tool needs stating.
  - **Excel reading** — step 00 and step 08 must parse `.xlsx`, which Python's
    standard library cannot do directly; `openpyxl` or `pandas` is implied but
    neither is on the list.
  - **Testing** — CLAUDE.md requires a test per endpoint and `tests/` exists, but no
    test runner is named.
  - **Log shipping** — Filebeat is treated above as part of ELK; confirm that reading.

DECISION NEEDED: Are the gaps above in-list-by-implication (a library needed to use an
approved tool counts as part of it) or does each need an explicit rule-1 exception?
This affects step 00 immediately — the Excel loader cannot be written without an
answer. My reading is that `psycopg`, an Excel reader, and `pytest` are
implementation details of Postgres, the pipeline, and the testing requirement rather
than competing tool choices, but rule 1 says to ask rather than assume.

DECISION NEEDED: What is the process for adding a tool? Rule 1 says stop and ask. Is
the outcome of a granted exception a new ADR that amends this one, an edit to the
list in CLAUDE.md, or both?

DECISION NEEDED: Are versions pinned, and where? "Python 3.12" is pinned; FastAPI,
scikit-learn, and the Agents SDK are not. A scikit-learn upgrade can silently change
model behaviour, which the eval suite would then have to re-certify.

DECISION NEEDED: Does the forbidden list forbid these tools as *transitive
dependencies* too, or only as direct choices? Some approved packages pull in
substantial dependency trees, and a strict reading would be hard to satisfy.
