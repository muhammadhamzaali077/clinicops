# ADR 001 — Deployment topology: three separated tiers

- **Status:** Accepted (mandated by CLAUDE.md, "three tiers, do not deviate")
- **Date:** DECISION NEEDED: date this decision was taken
- **Deciders:** DECISION NEEDED: who
- **Supersedes / superseded by:** —

## Context

ClinicOps serves a two-doctor clinic: patient history lookup and symptom triage,
with an audit trail of every decision. It has to be operable by one platform
engineer who is not on the clinic premises.

Three constraints shape the topology, and one of them is not negotiable by us:

1. **The compute platform has no persistent storage.** The application runs as a
   Hugging Face Docker Space on port 7860. A Space's filesystem does not survive a
   restart, a rebuild, or a scale-to-zero. Only `/tmp` is writable, and `/tmp` is
   as ephemeral as the container. The process runs as UID 1000, so nothing can
   assume root or write outside its own space at runtime.
2. **The data is clinical.** Every triage decision needs to be recoverable
   afterwards — which model version made it, at what confidence, whether it was
   escalated. An audit trail that disappears on the next container restart is not
   an audit trail.
3. **The operator watches from outside.** The observability stack runs on the
   engineer's machine via docker compose. It cannot be inside the Space, because a
   monitoring system that dies with the thing it monitors reports nothing at the
   moment it matters most.

## Decision

Three tiers, separated, with a one-directional dependency from compute to state and
a pull-based dependency from observability to compute.

1. **Compute** — FastAPI in Docker on a Hugging Face Docker Space, port 7860.
   Stateless. UID 1000. `/tmp` is the only writable path. Holds no durable data,
   ever.
2. **State** — managed Postgres, reached over `DATABASE_URL`. All durable data:
   `patients`, `patient_features`, `inference_log`, `alert_history`,
   `model_registry`. Reached only through `src/common/db.py`.
3. **Observability** — Prometheus, Grafana, Alertmanager, Elasticsearch, Kibana,
   and n8n, on the operator's host via docker compose. Reaches into the public
   Space over HTTPS. Prometheus *pulls* `GET /metrics`; the Space does not push.

**Recorded explicitly, because it is the reason the split exists:** Hugging Face
Spaces provide no persistent storage. This is not a preference about clean
architecture. It is a property of the platform. Any durable state written to the
Space's local disk is silently lost on the next restart — which for a clinical
audit log means the record of a triage decision vanishes with no error and no
warning. Postgres exists in this design because the compute tier physically cannot
keep anything.

Rule 6 in CLAUDE.md ("Compute is stateless. All durable state goes to Postgres via
`src/common/db.py`. Never write to local disk except `/tmp`") is the enforcement of
this ADR in code.

## Consequences

**Good:**

- The Space can be restarted, redeployed, or scaled to zero at any moment with no
  data loss. `restart_space` is therefore safe enough to sit in `AUTO_ALLOWED`
  (docs/05-ops-spec.md) — a property the topology buys directly.
- State survives every deployment. Model versions, decisions, and alert history
  outlive any container.
- Observability survives a total compute outage and can report on it.
- Each tier scales, fails, and is reasoned about independently.

**Costs, accepted:**

- Every request that needs data pays a network round trip to Postgres. There is no
  local cache to fall back on, by construction.
- `DATABASE_URL` is a hard dependency of the request path. Postgres being
  unreachable degrades or stops the clinical path. Whether triage still answers
  without being able to log is an open question in docs/02-architecture.md.
- The model artifact has nowhere local to live, which is why "where does the `.pkl`
  come from at container start" is an unresolved question in
  docs/02-architecture.md and docs/03-data-contract.md rather than an implementation
  detail.
- The observability tier runs on a laptop. When it is closed, nothing is scraping
  and nothing is alerting.
- Three tiers means three sets of credentials, three failure domains, and a public
  endpoint holding patient data.

DECISION NEEDED: The Space is public and serves patient data. What authentication
sits in front of it? This is the single largest gap in the topology as specified,
and it is flagged in docs/01-PRD.md as blocking a real deployment.

DECISION NEEDED: The escalation path (Space → n8n on the operator's laptop → both
doctors) makes the clinic's emergency notification depend on a machine outside the
clinic being powered on. Is that acceptable, or does escalation need a path that
does not traverse Tier 3?

DECISION NEEDED: Which managed Postgres provider and region, and does the region
satisfy whatever data-protection regime applies (also open in docs/01-PRD.md)?

DECISION NEEDED: Is there a backup and restore procedure for Postgres, and has a
restore been tested? Tier 2 is now the only copy of everything except the original
Excel file.

## Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| **Single tier: everything in the Space, SQLite on local disk** | Data loss on every restart. Silent, unrecoverable, and the lost data would be clinical decisions. Not viable on a platform with no persistent storage. |
| **State inside the Space via a mounted volume** | Hugging Face Spaces offer no such guarantee at the tier this project targets; it would reintroduce the storage assumption the platform does not support. |
| **Observability inside the Space** | A monitoring stack that shares a failure domain with the monitored system cannot report the outage that matters. Also impossible to run Prometheus, Grafana, Alertmanager, ELK, and n8n inside a single stateless container. |
| **Push-based metrics (the Space pushes to Prometheus)** | Requires the local stack to be reachable from the internet, exposing the operator's host. Pull keeps the network dependency one-directional. |
| **Self-managed Postgres in the same compose stack as observability** | Puts durable clinical state on a laptop, which is a worse durability story than the Space. |
