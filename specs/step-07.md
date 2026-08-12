# Spec: Step 07 - Incidents

## Objective

A proposed operational action executes only if it is in `AUTO_ALLOWED` and within the
2-per-hour limit, and every attempt — executed or refused — writes an audit row.

(The three alert rules, the alert-triage agent, and the RCA agent are also built this
session — see Deliverables and Acceptance criteria — but the whitelist-and-audit invariant is
the one outcome that defines "done".)

## Depends on

- **Step 00** — `db.py` works.
- **Step 04** — Alertmanager and Prometheus containers are running.
- **Step 06** — the five metrics exist and are being scraped, so alert rules have data to
  evaluate.

## Deliverables

| File | Purpose |
| --- | --- |
| `infra/prometheus/alerts.yml` | The three alert rules with their PromQL. |
| `infra/alertmanager/alertmanager.yml` | Routes firing alerts to the Space's alert webhook. |
| `scripts/schema.sql` | Extended with the `alert_history` table. |
| `src/ops/whitelist.py` | The three sets — `AUTO_ALLOWED`, `NEEDS_APPROVAL`, `NEVER_AUTO` — as data, not logic. |
| `src/ops/executor.py` | Resolves an action against the whitelist, enforces the rate limit, executes or refuses, always audits. |
| `src/ops/triage.py` | The alert-triage agent: reads the alert plus recent `alert_history`, proposes an action. |
| `src/agents/rca.py` | The RCA agent: writes a root-cause summary from metrics and alert history. |
| `src/api/routes/ops.py` | `POST /ops/alert` — the Alertmanager webhook receiver. |
| `tests/test_whitelist.py` | Unknown action refused; each `NEVER_AUTO` action refused. |
| `tests/test_rate_limit.py` | The third auto-action inside an hour is refused. |

## Interface contract

**Alert rules** (`infra/prometheus/alerts.yml`) — the three from docs/05-ops-spec.md:

1. `ClinicOpsTriageLatencyHigh` — `histogram_quantile(0.95, sum by (le) (rate(clinicops_request_duration_seconds_bucket{endpoint=~"/intake|/ask"}[10m]))) > 2`, `for: 10m`, `warning`
2. `ClinicOpsEmergencyEscalationFailed` — `increase(clinicops_escalations_total{reason="emergency",outcome="failed"}[5m]) > 0`, `for: 0m`, `critical`
3. `ClinicOpsGuardrailViolation` — `increase(clinicops_guardrail_refusals_total{refusal_reason="prompt_injection_attempt"}[15m]) > 0`, `for: 0m`

**`POST /ops/alert`** — Alertmanager webhook payload in; returns what was decided:

```json
{
  "alert_name": "ClinicOpsTriageLatencyHigh",
  "fingerprint": "a1b2c3",
  "triage_summary": "Latency rose after the last deploy; no DB errors.",
  "rca_summary": "p95 tracks agent tool-call count, not DB time.",
  "proposed_action": "restart_space",
  "whitelist_set": "AUTO_ALLOWED",
  "action_result": "success",
  "auto_actions_in_window": 1
}
```

An action not in any set returns `action_result: "refused"` with
`whitelist_set: "unknown"` (rule 7).

**`alert_history` columns** — per docs/03-data-contract.md: `id BIGSERIAL PK`,
`alert_name TEXT NOT NULL`, `fingerprint TEXT NOT NULL` (indexed), `severity TEXT NOT NULL`,
`status TEXT NOT NULL CHECK (status IN ('firing','resolved'))`, `started_at TIMESTAMPTZ NOT NULL`,
`resolved_at TIMESTAMPTZ NULL`, `triage_summary TEXT NULL`, `rca_summary TEXT NULL`,
`action_taken TEXT NULL`, `action_result TEXT NULL CHECK (action_result IN ('success','failed','refused','awaiting_approval'))`,
`approved_by TEXT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

**Rate limit** — at most **2** `AUTO_ALLOWED` executions per rolling hour, counted from
`alert_history` in Postgres, not process memory (the Space is stateless and a restart
would otherwise reset the limit). `NEEDS_APPROVAL` actions do not count.

**Audit** — every attempt writes a row: timestamp, action name, whitelist set, outcome,
actor, approver, triggering alert and fingerprint, reason code, and the rate-limit state
at decision time. Append-only. Refusals are audited as loudly as executions. No patient
data (rule 4).

Defaults taken. Override any of them and I will change it:

- ASSUMED: the rate-limit window is a **rolling** hour. A fixed clock hour would allow 4
  actions across a boundary.
- ASSUMED: append-only is enforced by a dedicated Postgres role holding `INSERT` and
  `SELECT` but not `UPDATE` or `DELETE` on the audit table. Convention is not enforcement.
- ASSUMED: there is no emergency bypass of the rate limit. The third action is refused and
  a human takes over — that is the point of the limit.
- ASSUMED: alert rule 3 is `severity: warning` and fires only on
  `prompt_injection_attempt`. Alerting on every refusal reason would page constantly on
  guardrails working correctly.
- ASSUMED: `NEEDS_APPROVAL` approval is recorded by setting `approved_by` through a script in
  `scripts/`, with no timeout — an unapproved action simply never runs.

BLOCKERS — this step cannot start until you answer:

1. **Which table holds the audit trail.** `alert_history` covers alert-driven actions, but a
   manually invoked action has no alert to hang off, and the brief specifies five tables with
   no `audit_log` among them. A sixth table needs your authorisation.
2. **Is `restart_space` safe to run unattended?** It drops in-flight requests, one of which
   could be an emergency triage. It is in `AUTO_ALLOWED` as drafted; moving it to
   `NEEDS_APPROVAL` is a clinical-risk call, not mine.
3. **Who approves the clinical-behaviour actions** — `rollback_model`, `promote_model`,
   `reload_kb`: the platform engineer, or a doctor?
4. **Does alert rule 2 cover a *failed* escalation or a *missing* one?** It cannot cover both
   in three rules, and a missing escalation is the more dangerous of the two.

## Acceptance criteria

- [ ] `promtool check rules infra/prometheus/alerts.yml` passes and Prometheus loads all
      three rules at `/rules`.
- [ ] `POST /ops/alert` with a synthetic Alertmanager payload writes one `alert_history`
      row with `status='firing'` and a non-null `triage_summary` and `rca_summary`.
- [ ] An action in `AUTO_ALLOWED` executes and records `action_result='success'`.
- [ ] An action not in any set is refused with `whitelist_set='unknown'` and
      `action_result='refused'` — and is still audited.
- [ ] Every `NEVER_AUTO` action is refused regardless of who or what proposes it, and each
      attempt writes an audit row.
- [ ] The third `AUTO_ALLOWED` action inside one hour is refused with a rate-limit reason
      code, and the refusal is audited.
- [ ] A `NEEDS_APPROVAL` action records `action_result='awaiting_approval'` and does not
      execute until `approved_by` is set.
- [ ] No `alert_history` row contains a patient name, a note, or symptom text (rule 4).

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **The RCA agent reading Elasticsearch** — step 11. This session's RCA agent reasons from
  Prometheus metrics and `alert_history` only. Log-based root-cause analysis is added in
  step 11 and will replace this narrower version.
- **JSON structured logging, `request_id` correlation, Filebeat, Kibana** — step 11. The
  `request_id` column in `inference_log` cannot be joined to a log document yet.
- **Writing to or reading from `inference_log`** — step 08. Alert triage uses metrics and
  `alert_history`; it does not inspect clinical decisions.
- **`rollback_model`, `promote_model`, drift-triggered retraining, or anything touching
  `model_registry`** — step 09. Those action *names* appear in `NEEDS_APPROVAL` as data,
  but their implementations do not exist and must not be stubbed into working code this
  session.
- **New Prometheus metrics** — step 06 defined exactly five; alert rules consume them as
  they are. If a rule needs a metric that does not exist, stop and ask (rule 1).
- **Changes to `ci.yml` or CI-triggered remediation** — step 05. CI failures are not ops
  actions.
- **The eval harness or safety gating** — step 10.
- **A break-glass path for `NEVER_AUTO`** — that is a documented human procedure, not code,
  and it is an open decision.

## Manual verification

```bash
psql "$DATABASE_URL" -f scripts/schema.sql
curl -s -X POST localhost:7860/ops/alert -H 'Content-Type: application/json' \
  -d '{"alerts":[{"labels":{"alertname":"ClinicOpsTriageLatencyHigh","severity":"warning"},"fingerprint":"a1b2c3","status":"firing","startsAt":"2026-08-12T09:00:00Z"}]}'
echo "--- unknown action must be refused ---"
python -c "from src.ops.executor import execute; print(execute('delete_all_patients', actor='test'))"
echo "--- third auto-action in the hour must be refused ---"
for i in 1 2 3; do python -c "from src.ops.executor import execute; print(execute('rerun_health_check', actor='test'))"; done
psql "$DATABASE_URL" -c "SELECT alert_name, action_taken, action_result, approved_by FROM alert_history ORDER BY id DESC LIMIT 6;"
```

Expected: one `firing` row with both summaries; the unknown action refused; the third
`rerun_health_check` refused on the rate limit; and an audit row for every attempt
including the two refusals.
