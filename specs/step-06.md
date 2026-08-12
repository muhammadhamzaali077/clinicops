# Spec: Step 06 - Observability

## Objective

The local Prometheus reports the ClinicOps target as `up` while scraping exactly the five
named metrics from the public Space over HTTPS, with no patient identifier in any label
value.

(The Grafana dashboard and the `GREEN`/`AMBER`/`RED` health-summary agent are also built
this session — see Deliverables and Acceptance criteria — but the working scrape is the one
outcome that defines "done".)

## Depends on

- **Step 01–03** — the endpoints that will be instrumented exist (`/triage`, `/ask`,
  `/intake`, `/morning-brief`, `/followups`).
- **Step 04** — the Prometheus and Grafana containers exist and mount a config path.

## Deliverables

| File | Purpose |
| --- | --- |
| `src/common/metrics.py` | Defines the five metrics once. Every route imports from here. |
| `src/api/routes/metrics.py` | `GET /metrics` in Prometheus text exposition format. |
| `src/api/middleware.py` | Records request count and duration for every endpoint automatically. |
| `infra/prometheus/prometheus.yml` | Scrape config targeting the public Space over HTTPS. |
| `infra/prometheus/rules.yml` | Recording rules only — no alert rules this session. |
| `infra/grafana/provisioning/datasource.yml` | Prometheus datasource, provisioned. |
| `infra/grafana/dashboards/clinicops.json` | Panels: request rate, latency p95, decision mix, refusals, escalations. |
| `src/agents/health_summary.py` | Queries Prometheus, returns `GREEN`/`AMBER`/`RED` with reasoning. |
| `scripts/health_summary.py` | CLI wrapper for the above. |
| `tests/test_metrics.py` | All five metric names present; no `patient_id` label anywhere. |

## Interface contract

**`GET /metrics`** — text exposition format, exactly these five metrics:

| Metric | Type | Labels |
| --- | --- | --- |
| `clinicops_requests_total` | Counter | `endpoint`, `method`, `status_code` |
| `clinicops_request_duration_seconds` | Histogram | `endpoint` |
| `clinicops_triage_decisions_total` | Counter | `decision`, `model_version` |
| `clinicops_escalations_total` | Counter | `outcome`, `reason` |
| `clinicops_guardrail_refusals_total` | Counter | `refusal_reason` |

No metric carries `patient_id`, a name, or symptom text as a label value (rule 4).
`/metrics` is scraped from outside the Space, so a per-patient label would publish a
patient register.

**`infra/prometheus/prometheus.yml`** — HTTPS scrape of the public Space:

```yaml
scrape_configs:
  - job_name: clinicops
    scheme: https
    metrics_path: /metrics
    static_configs:
      - targets: ["<space-host>"]
```

**Health summary** — `python scripts/health_summary.py`:

```json
{
  "status": "AMBER",
  "reasons": [
    "triage p95 is 2.4s against a 2s target",
    "no emergency escalations failed in the last hour"
  ],
  "checked_at": "2026-08-12T09:00:00Z"
}
```

`status` ∈ `{GREEN, AMBER, RED}` — exactly one, never a hedge. Proposed mapping:
`RED` if any emergency escalation failed or the Space is unreachable; `AMBER` if triage
p95 breaches 2 s or the error rate is elevated; `GREEN` otherwise.

**Retrofit:** this session adds the metric to each endpoint from steps 01–03, closing
the CLAUDE.md "metric + log line + test" gap those sessions deliberately left open.

Defaults taken. Override any of them and I will change it:

- ASSUMED: buckets are `[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30]`, straddling the 2 s
  target and the 15 s agent timeout.
- ASSUMED: every metric carries an `environment` label from `ENVIRONMENT`.
- ASSUMED: scrape interval 30 s. Low clinic traffic means shorter intervals mostly scrape
  unchanged counters.
- ASSUMED: SC-3 is counted from `inference_log` (step 08), not from these counters, because
  a cold start resets a counter to zero and destroys any absolute total.
- ASSUMED: thresholds are `RED` if any emergency escalation failed in the window or the
  target is down; `AMBER` if triage p95 exceeds 2 s or the 5xx rate exceeds 1%; `GREEN`
  otherwise.
- ASSUMED: the health summary is a CLI only, not an endpoint. An endpoint would need a
  metric, a log line, and a test of its own.

BLOCKER — this step cannot start until you answer:

1. **How `/metrics` is protected on a public Space, and how Prometheus authenticates to it.**
   Left open, the platform's internal metrics are world-readable. A bearer token in the
   scrape config is the obvious answer but it is yours to authorise.

## Acceptance criteria

- [ ] `curl $SPACE_URL/metrics` returns 200 in text exposition format containing all
      five `clinicops_*` metric names.
- [ ] `curl $SPACE_URL/metrics | grep -c patient_id` returns `0` — no patient identifier
      is ever a label value.
- [ ] After one call to each of `/triage`, `/ask`, and `/intake`,
      `clinicops_requests_total` shows a series for each of those endpoints.
- [ ] A `POST /triage` returning `emergency` increments
      `clinicops_triage_decisions_total{decision="emergency"}`.
- [ ] A guardrail refusal increments `clinicops_guardrail_refusals_total` with the
      matching `refusal_reason` label.
- [ ] Prometheus shows the `clinicops` target as `UP` at `/targets`, scraping over
      `https`.
- [ ] The Grafana dashboard loads with all panels populated from live data, no "No data"
      panels.
- [ ] `python scripts/health_summary.py` prints exactly one of `GREEN`, `AMBER`, `RED`
      with at least one reason, and returns `RED` when the Space is stopped.

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **Alert rules, `alerts.yml`, Alertmanager routing, or anything that pages a human** —
  step 07. This session produces recording rules and dashboards; nothing fires. The three
  alert rules with their PromQL belong to step 07.
- **`alert_history`, the alert-triage agent, the RCA agent** — step 07. The health-summary
  agent is a read-only reporter: it never proposes or executes an action.
- **The whitelisted action executor, rate limiting, or the audit trail** — step 07. The
  health summary reports `RED`; it does not restart anything.
- **Filebeat, Elasticsearch indices, Kibana dashboards, or log-based panels** — step 11.
  Grafana panels this session are Prometheus-only.
- **JSON structured logging or `request_id` propagation** — step 11. The middleware added
  here records metrics; the log-line half of it is step 11's.
- **Drift metrics, model-confidence histograms, shadow/canary comparison panels** —
  step 09. If model health needs a sixth metric, that is a rule-1 conversation, not a
  quiet addition.
- **Writing anything to `inference_log`** — step 08. Metrics live in process memory and
  are lost on restart, which is expected until step 08 gives decisions a durable home.
- **The 30 eval cases or exporting eval results as metrics** — step 10.

## Manual verification

```bash
curl -s "$SPACE_URL/metrics" | grep -E "^clinicops_" | cut -d'{' -f1 | sort -u
curl -s "$SPACE_URL/metrics" | grep -c patient_id
curl -s http://localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"'
python scripts/health_summary.py
```

Expected: five distinct `clinicops_*` metric names; `0` for the `patient_id` grep;
`"health":"up"` for the clinicops target; and one of `GREEN`/`AMBER`/`RED` with reasons.
