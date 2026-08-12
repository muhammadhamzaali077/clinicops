# 05 — Operations Specification

Prometheus scrapes the Space over HTTPS. Grafana reads Prometheus. Alertmanager
fires into n8n. Every operational action comes from the whitelist, and an action
that is not on the whitelist is refused (rule 7).

## Prometheus metrics

Five metrics. Every endpoint contributes to at least one (CLAUDE.md: every endpoint
gets a metric, a log line, and a test).

Label values must never carry patient names, notes, or symptom text (rule 4).
`patient_id` is deliberately **not** a label on any metric — 20 patients today is a
harmless cardinality, but a per-patient label turns the metrics endpoint into a
patient register, and `/metrics` is scraped from outside the Space.

| # | Metric | Type | Labels | Purpose |
| --- | --- | --- | --- | --- |
| 1 | `clinicops_requests_total` | Counter | `endpoint`, `method`, `status_code` | Traffic and error rate for every endpoint. The denominator for the availability SLO. |
| 2 | `clinicops_request_duration_seconds` | Histogram | `endpoint` | Latency. Source for SC-1 (lookup under 5 s) and SC-2 (triage p95 under 2 s). |
| 3 | `clinicops_triage_decisions_total` | Counter | `decision`, `model_version` | The decision mix: `self_care` / `see_doctor_today` / `emergency`. Detects drift in the label distribution and is the denominator for escalation coverage. |
| 4 | `clinicops_escalations_total` | Counter | `outcome` (`dispatched`, `failed`), `reason` (`emergency`, `low_confidence`) | SC-3. Compared against the `emergency` count from metric 3 to prove 100% coverage. |
| 5 | `clinicops_guardrail_refusals_total` | Counter | `refusal_reason` | Every guardrail refusal by reason code, including `prompt_injection_attempt`. SC-5 expects zero on the evals; in production this is the safety signal. |

DECISION NEEDED: Histogram buckets for metric 2. The defaults are tuned for
sub-second web traffic; this system has a 2 s p95 target and a 15 s agent timeout,
so the buckets need to straddle both. Proposed:
`[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30]` — confirm or replace.

DECISION NEEDED: Model health has no metric here. Confidence distribution,
inference latency separated from LLM latency, and feature-drift indicators are all
step 09 concerns, but the brief specifies exactly five metrics. Do drift and model
health share metric 3, or is a sixth metric authorised?

DECISION NEEDED: Is there a `environment` label (`production` / `staging`) on every
metric, given `ENVIRONMENT` is already a required config variable?

DECISION NEEDED: The Space scales to zero and cold-starts. Counters reset to zero on
every restart, which Prometheus handles for `rate()` but which makes any absolute
total meaningless. Is that accepted, or does SC-3's "100% of emergencies" need to be
counted from `inference_log` in Postgres rather than from a counter?

## SLOs

Three, derived from the PRD's success criteria. Each needs a measurement window to
be actionable.

| # | SLO | Target | Window | Derived from |
| --- | --- | --- | --- | --- |
| SLO-1 | **Triage latency** — p95 of `clinicops_request_duration_seconds` on the triage path | < 2 s | DECISION NEEDED: rolling 1 h, 24 h, or 30 d? | SC-2 |
| SLO-2 | **Emergency escalation coverage** — `clinicops_escalations_total{reason="emergency",outcome="dispatched"}` ÷ `clinicops_triage_decisions_total{decision="emergency"}` | 100% | DECISION NEEDED: window, and whether any error budget is allowed at all | SC-3 |
| SLO-3 | **Availability** — fraction of requests not returning 5xx | DECISION NEEDED: 99%? 99.5%? | DECISION NEEDED | Not specified in the PRD — flagged there too |

DECISION NEEDED: SLO-2 is a 100% target, which means an error budget of zero and a
single failure burning it entirely. Is that the intent (any missed escalation is an
incident, full stop), or is there a tolerance?

DECISION NEEDED: SC-1 (history lookup under 5 s) has no SLO above because only three
were requested. Is lookup latency folded into SLO-1 as a second threshold on a
different endpoint, or genuinely unmonitored?

DECISION NEEDED: What is the SLO for `emergency_recall` (SC-4, ≥ 0.95)? It is
measured by the eval harness, not by Prometheus, so it cannot be an SLO in the
usual sense — but it is the most safety-critical number in the system. Is it gated
only at deploy time (step 09's quality gate), or monitored in production somehow?

## Alert rules

Three rules, PromQL below. Each fires into Alertmanager, which routes to n8n, which
notifies the platform engineer.

### 1. `ClinicOpsTriageLatencyHigh`

Triage p95 has breached SLO-1.

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(clinicops_request_duration_seconds_bucket{endpoint=~"/intake|/ask"}[10m])
  )
) > 2
```

- `for: 10m`
- `severity: warning`

DECISION NEEDED: `10m` for both the rate window and the `for` clause is a guess
that trades detection speed against flapping on a low-traffic clinic. With a
handful of requests an hour, a 10-minute rate window may be empty most of the time.
What are the real values?

DECISION NEEDED: The `endpoint=~"/intake|/ask"` matcher assumes both endpoints count
as "the triage path". Confirm — `/ask` includes agent LLM time and will breach 2 s
far more readily than `/intake` alone.

### 2. `ClinicOpsEmergencyEscalationFailed`

An `emergency` decision was made and the notification to both doctors did not get
out. This is the most serious alert in the system.

```promql
increase(clinicops_escalations_total{reason="emergency",outcome="failed"}[5m]) > 0
```

- `for: 0m` — fires immediately, no waiting
- `severity: critical`

DECISION NEEDED: This rule catches a *failed* dispatch. It does not catch a missing
one — an `emergency` decision that never attempted an escalation increments neither
`outcome`. A coverage-gap rule comparing metric 4 against metric 3 would catch that,
but it would be a fourth alert rule and the brief specifies three. Which behaviour
do you want covered by rule 2?

DECISION NEEDED: Prometheus scrapes from the operator's laptop. If the laptop is
closed, this alert cannot fire at all. Given it is the critical safety alert, does it
need a path independent of the local stack?

### 3. `ClinicOpsGuardrailViolation`

A guardrail refused something. In production this means either a genuine
out-of-scope request or an attack.

```promql
increase(clinicops_guardrail_refusals_total{refusal_reason="prompt_injection_attempt"}[15m]) > 0
```

- `for: 0m`
- `severity: DECISION NEEDED: critical or warning?`

DECISION NEEDED: Should this alert on **all** refusal reasons, or only
`prompt_injection_attempt` as written? Routine out-of-scope refusals (someone asks
for a dose) are the guardrails working correctly and would page the engineer
constantly; injection attempts are an attack. Splitting them means a fourth rule.

DECISION NEEDED: A third rule covering the Space being down entirely (`up == 0` or
`ClinicOpsSpaceDown`) is arguably more valuable than any of the above, and there is
no room for it in three. Is availability alerting expected here, and if so which of
the three rules above does it displace?

## Action whitelist

Rule 7: every operational action is whitelisted; an unknown action is refused. The
incident agent (step 07) may propose any action, but may only *execute* what is in
`AUTO_ALLOWED`.

The organising principle for `AUTO_ALLOWED` is **reversibility**: an action goes in
this set only if undoing it restores the prior state exactly, and only if the worst
case of running it at the wrong moment is a brief degradation rather than data loss
or a clinical hazard.

### AUTO_ALLOWED — reversible only, executed without a human

| Action | Effect | Why it is reversible |
| --- | --- | --- |
| `restart_space` | Restart the Space container. | Compute is stateless (rule 6); nothing is lost but in-flight requests. |
| `reload_model` | Re-load the current production model into memory. | Same version, same artifact; a no-op if already loaded. |
| `clear_tmp_cache` | Delete files under `/tmp`. | `/tmp` holds nothing durable by rule 6. |
| `scale_observability_stack` | Restart a Tier 3 container (Prometheus, Grafana, Alertmanager, ELK, n8n). | Local, no clinical path, no patient data. |
| `rerun_health_check` | Re-probe `GET /health`. | Read-only. |
| `snapshot_metrics` | Capture the current metric values for an incident record. | Read-only. |

DECISION NEEDED: Is `restart_space` genuinely safe to run unattended? It drops
in-flight requests, and one of those could be an `emergency` triage. A restart
during an emergency is a clinical event, not just an availability blip. Should it be
moved to `NEEDS_APPROVAL`, or gated on there being no in-flight triage?

DECISION NEEDED: `scale_observability_stack` restarting n8n would break the
escalation path mid-incident if n8n is in the synchronous path (open question in
docs/02-architecture.md). Conditional on that answer.

### NEEDS_APPROVAL — a human authorises, then the action runs

| Action | Effect | Why approval |
| --- | --- | --- |
| `rollback_model` | Move production to the previous `model_registry` version. | Reversible, but it changes clinical behaviour. A human decides. |
| `promote_model` | Promote a candidate to production. | Changes clinical behaviour. Gated on the quality gate *and* a human. |
| `redeploy_space` | Deploy a specific image tag. | Reversible, but it is a change to the live clinical system. |
| `pause_intake` | Stop accepting `POST /intake`. | Safe technically, severe operationally — the clinic loses triage. |
| `resume_intake` | Re-enable intake. | Paired with the above; approval keeps the pair symmetric. |
| `rotate_credentials` | Rotate `DATABASE_URL` or `OPENAI_API_KEY`. | Recoverable but disruptive; a bad rotation takes the system down. |
| `reload_kb` | Re-load `kb/` first-aid content. | The KB is doctor-approved clinical content; a reload of unreviewed content is a clinical change. |
| `backfill_features` | Recompute `patient_features`. | Writes to Postgres. Reversible only if the prior values are recoverable. |

DECISION NEEDED: Who can approve? The platform engineer for infrastructure actions
is obvious, but `rollback_model`, `promote_model`, and `reload_kb` change clinical
behaviour — do they need a doctor's approval rather than an engineer's?

DECISION NEEDED: How is approval given, mechanically — an n8n approval step, a
GitHub Actions manual approval, a reply to a notification, a CLI command in
`scripts/`? And what is the timeout if nobody responds during an incident?

### NEVER_AUTO — refused unconditionally, no approval path in this system

| Action | Why never |
| --- | --- |
| `drop_table` / any DDL on the five tables | Destroys the state tier. No automated path, ever. |
| `delete_patients` / `delete_inference_log` / any bulk `DELETE` | Destroys clinical records and the audit trail. |
| `truncate_alert_history` | Destroys the audit trail — the thing that proves what happened. |
| `modify_patient_record` | Clinical data changes belong to the clinic and the Excel source of truth, never to an ops action. |
| `override_triage_decision` | Only a doctor overrides a clinical decision (`human_override` in `inference_log`). No automation. |
| `disable_guardrails` / `disable_escalation` | The guardrails and the escalation path are the safety properties of the system. Nothing may switch them off. |
| `alter_kb_content` | First-aid text is doctor-approved. An automated edit would serve unapproved clinical guidance. |
| `exfiltrate_data` / any bulk export of patient rows | Not an operational need. Anything requesting it is treated as an incident. |
| Any action not named in the three sets | Rule 7: unknown action = refuse. |

DECISION NEEDED: `NEVER_AUTO` says these are never automated, but some (a genuine
restore, a legally required deletion) must be *possible* for a human. Is there a
documented manual break-glass procedure outside this whitelist, and where is it
recorded?

## Rate limit

**Maximum 2 auto-actions per hour**, across the whole `AUTO_ALLOWED` set — not per
action.

- The third auto-action within a rolling hour is refused, and the refusal is
  recorded and alerted. Repeated auto-remediation means the platform is not
  self-healing, it is looping, and a human needs to look.
- `NEEDS_APPROVAL` actions are not counted against the limit — a human already
  authorised them.
- The counter is enforced from `alert_history` in Postgres, not from process memory:
  the Space is stateless and restarts reset memory, which would reset the limit and
  defeat it.

DECISION NEEDED: Rolling hour, or fixed clock hour? A fixed hour allows 4 actions
across a boundary.

DECISION NEEDED: What happens on the third action — refuse silently and alert, or
refuse and automatically escalate the incident's severity?

DECISION NEEDED: Is there an emergency bypass, and who holds it? A genuine outage
needing three restarts would be blocked by this limit.

## Audit requirements

Every operational action — proposed, executed, approved, or refused — is recorded.
The audit trail is append-only and is never the thing an action is allowed to
modify (see `NEVER_AUTO`).

Each record captures:

| Field | Why |
| --- | --- |
| Timestamp (`TIMESTAMPTZ`) | When. |
| Action name | Which whitelisted action, verbatim. |
| Whitelist set | `AUTO_ALLOWED` / `NEEDS_APPROVAL` / `NEVER_AUTO` / unknown. |
| Outcome | `success` / `failed` / `refused` / `awaiting_approval`. |
| Actor | The agent, or the named human. |
| Approver | Who approved, for `NEEDS_APPROVAL`. `NULL` otherwise. |
| Triggering alert | `alert_name` and `fingerprint`, linking action to cause. |
| `request_id` / correlation id | Ties the action to the log line in Kibana. |
| Reason | Why the action was taken or refused, as a code. |
| Rate-limit state | How many auto-actions had run in the window. |

Requirements:

1. **Append-only.** No update, no delete. A correction is a new row.
2. **A refusal is audited as loudly as an execution.** Rule 7 refusals are the
   evidence that the whitelist works; an unaudited refusal is invisible.
3. **Every `NEVER_AUTO` attempt is audited and alerted.** Something requesting a
   forbidden action is a security signal.
4. **No patient data in the audit trail.** Action names, IDs, and codes only
   (rule 4).
5. **Durable in Postgres**, via `src/common/db.py` — never a file on the Space
   (rule 6).

DECISION NEEDED: Which table? docs/03-data-contract.md's `alert_history` covers
alert-driven actions, but an action invoked manually or by CI has no alert to hang
off, and the brief specifies exactly five tables with no `audit_log` among them. Is
`alert_history` doing double duty, or is a sixth table authorised?

DECISION NEEDED: How is append-only actually enforced — a Postgres role without
`UPDATE`/`DELETE` on the table, a trigger, or convention alone? Convention is not
enforcement.

DECISION NEEDED: Audit retention. Longer than `inference_log`, or the same? An audit
trail deleted on the same schedule as the data it describes cannot answer questions
about the past.
