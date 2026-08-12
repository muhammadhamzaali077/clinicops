# Spec: Step 11 - Logging

## Objective

A single `request_id` traces one request across all four places it appears — the
`X-Request-ID` response header, exactly one JSON log line, the `inference_log` row, and an
Elasticsearch document.

(The Kibana saved search and the log-reading RCA agent are also built this session — see
Deliverables and Acceptance criteria — but end-to-end traceability is the one outcome that
defines "done".)

## Depends on

- **Step 01–03** — the endpoints that will emit log lines exist.
- **Step 04** — the Elasticsearch and Kibana containers are running.
- **Step 06** — `src/api/middleware.py` exists and already records metrics per request; the
  log line is added to it rather than to a second middleware.
- **Step 07** — `src/agents/rca.py` exists, reasoning from metrics and `alert_history`. This
  session replaces that narrower version with one that reads logs.
- **Step 08** — `inference_log.request_id` exists, giving the log documents something to
  join against.

## Deliverables

| File | Purpose |
| --- | --- |
| `src/common/logging.py` | JSON formatter and logger factory. The only logging configuration in the codebase. |
| `src/api/middleware.py` | Extended: generates `request_id`, emits one JSON line per request, returns the ID as a response header. |
| `infra/filebeat/filebeat.yml` | Ships the Space's logs into Elasticsearch with an index pattern. |
| `infra/filebeat/index-template.json` | Field mappings — `request_id` as `keyword`, timestamps as `date`. |
| `infra/kibana/saved-search.ndjson` | A saved search on `request_id`, importable into Kibana. |
| `src/agents/rca.py` | **Replaced**: queries Elasticsearch by `request_id` or time window and writes a root-cause summary. |
| `tests/test_logging.py` | One line per request; every line has `request_id`; no forbidden field present. |
| `tests/test_no_phi_in_logs.py` | Asserts no log line contains a patient name, note, or symptom text. |

## Interface contract

**Log line** — one JSON object per line, one line per request:

```json
{
  "timestamp": "2026-08-12T09:14:22.417Z",
  "level": "INFO",
  "request_id": "3f6c1b8e-1c2a-4d5e-9f00-2a1b3c4d5e6f",
  "endpoint": "/intake",
  "method": "POST",
  "status_code": 200,
  "latency_ms": 840,
  "patient_id": "P016",
  "decision": "emergency",
  "confidence": 0.97,
  "escalated": true,
  "refused": false,
  "refusal_reason": null,
  "tool_calls": 3,
  "model_version": "<version>",
  "environment": "production"
}
```

**Forbidden fields — rule 4.** No log line may contain, under any key: `name`, `notes`,
`symptoms`, `question`, `answer`, `first_aid`, or any free-text field derived from patient
input. IDs, decisions, codes, and numbers only.

**Response header:** every response carries `X-Request-ID`, matching the log line and the
`inference_log.request_id` row, so one ID ties the API response, the database row, and the
Kibana document together.

**RCA agent** — `python -m src.agents.rca --request-id <id>`:

```json
{
  "request_id": "3f6c1b8e-...",
  "summary": "The request breached the latency budget in the agent loop, not the database.",
  "evidence": [
    "tool_calls=6 — the loop ceiling was reached",
    "no db error logged in the surrounding 60s window"
  ],
  "log_documents_read": 14
}
```

It cites `request_id` values and log fields as evidence. It never quotes patient input,
because none is in the logs to quote.

Defaults taken. Override any of them and I will change it:

- ASSUMED: index names are `clinicops-<env>-YYYY.MM.dd`, with ILM retention matching whatever
  `inference_log` retention is set to in step 08. The two must agree.
- ASSUMED: Elasticsearch listens on the operator's host only, unauthenticated, not exposed to
  the internet. If the Space has to push to it, this assumption breaks and the transport
  decision below has to resolve it.
- ASSUMED: the RCA agent sends log fields including `patient_id` to the OpenAI API, since
  that is what "the RCA agent reads ES" requires. Flagging it rather than hiding it.

BLOCKERS — this step cannot start until you answer:

1. **How the Space's logs physically reach Filebeat.** The Space runs inside Hugging Face's
   infrastructure, `/tmp` is the only writable path, and Filebeat runs on the operator's
   laptop. Does the Space write JSON lines to `/tmp` for something to collect, push to
   Elasticsearch directly, expose a log endpoint, or does Filebeat pull from an HF logs API?
   Nothing in CLAUDE.md decides this and the entire step turns on it.
2. **Is `patient_id` acceptable in a log line?** Rule 4 forbids names, notes, and symptom text
   and permits IDs — but an Elasticsearch index keyed on patient ID is a searchable clinical
   record, a stronger exposure than the rule anticipated, and it feeds the assumption above
   about what goes to the OpenAI API.

## Acceptance criteria

- [ ] One request to `/intake` produces exactly **one** JSON log line — not two, not one per
      internal call — and it parses as valid JSON.
- [ ] Every log line contains a `request_id`, and it matches the `X-Request-ID` response
      header for that request.
- [ ] The same `request_id` appears in the `inference_log` row for that request, so the two
      can be joined.
- [ ] `pytest tests/test_no_phi_in_logs.py` passes: no log line contains a patient name from
      the file, any `notes` text, or any submitted symptom text (rule 4).
- [ ] A request's log document is retrievable from Elasticsearch by `request_id` within 30
      seconds of the request.
- [ ] The Kibana saved search imports and returns that document when queried by
      `request_id`.
- [ ] `python -m src.agents.rca --request-id <id>` returns a summary citing at least one log
      field as evidence, and reports how many documents it read.
- [ ] Every endpoint from steps 01–03 now has a metric (step 06), a log line (this step), and
      a test — closing CLAUDE.md's three-part requirement for all of them.

## Out of scope

This is the last step, so nothing later exists to guard against. Instead, **do not reach
backwards** and do not expand scope:

- **Do not add new endpoints, new metrics, or a sixth Prometheus metric.** Step 06 fixed the
  set at five; this session adds a log line to what exists.
- **Do not add a fourth alert rule** — including one on log volume or error-log rate. Step 07
  fixed the set at three. An alert on logs is a rule-1 conversation.
- **Do not retrain, re-tune, or re-evaluate the model.** Steps 09 and 10 own that. A slow
  request discovered while testing logging is a finding to report, not a model change.
- **Do not weaken or extend the guardrails** to make a log line more informative. In
  particular, do not start logging symptom text or the agent's `answer` because it would make
  RCA easier — that is exactly what rule 4 forbids, and it is the most likely temptation in
  this session.
- **Do not wire the RCA agent to the action executor.** Step 07 owns remediation, everything
  it may run is whitelisted, and RCA remains advisory.
- **Do not add a doctor-facing or receptionist-facing log view.** Kibana is the operator's
  tool.
- **Do not ship CI logs, Terraform state, or `plan.json` to Elasticsearch.** Application
  request logs only.
- **Do not backfill logs for past requests.** They were never emitted and cannot be
  recovered.

## Manual verification

```bash
RID=$(curl -s -D - -o /dev/null -X POST "$SPACE_URL/intake" \
  -H 'Content-Type: application/json' \
  -d '{"patient_id":"P009","age":25,"symptoms":"fever and body ache"}' \
  | grep -i '^x-request-id' | tr -d '\r' | awk '{print $2}')
echo "request_id: $RID"
sleep 30
curl -s "http://localhost:9200/clinicops-*/_search?q=request_id:$RID" | head -c 600
psql "$DATABASE_URL" -c "SELECT request_id, endpoint, decision FROM inference_log WHERE request_id = '$RID';"
python -m src.agents.rca --request-id "$RID"
echo "--- no PHI in logs ---"
curl -s "http://localhost:9200/clinicops-*/_search?q=*&size=100" | grep -c -E "Ayesha|Bilal|fever and body ache"
```

Expected: a non-empty `request_id`; an Elasticsearch hit for it; the same ID in
`inference_log`; an RCA summary citing log fields; and `0` for the PHI grep.
