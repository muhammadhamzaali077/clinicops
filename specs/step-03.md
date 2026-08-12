# Spec: Step 03 - Workflows

## Objective

`POST /intake` routes every decision to the patient's own doctor, and to **both** doctors
whenever the decision is `emergency`.

(`GET /morning-brief` and `GET /followups` are also built this session — see Deliverables
and Acceptance criteria — but routing is the one outcome that defines "done".)

## Depends on

- **Step 00** — `patients` loaded, `db.py` works.
- **Step 01** — the model and `/triage` work.
- **Step 02** — the agent, the four tools, and the guardrails work.

## Deliverables

| File | Purpose |
| --- | --- |
| `src/api/routes/intake.py` | `POST /intake` — the walk-in path: triage, route, escalate. |
| `src/api/routes/reports.py` | `GET /morning-brief` and `GET /followups`. |
| `src/common/routing.py` | Maps a patient to their owning doctor; returns both doctors for an `emergency`. |
| `src/common/notify.py` | Dispatches the escalation to the workflow layer. One function, one webhook call. |
| `src/api/schemas.py` | Extended with `IntakeRequest`, `IntakeResponse`, `MorningBriefResponse`, `FollowupsResponse`. |
| `infra/n8n/walk-in-flow.json` | Exported n8n workflow: intake form → `POST /intake` → notify on `emergency`. |
| `tests/test_intake.py` | Routing correctness, emergency fan-out to both doctors, refusal path. |
| `tests/test_reports.py` | Both GET endpoints against known fixture data. |

## Interface contract

**`POST /intake`**

Request:

```json
{ "patient_id": "P009", "age": 25, "symptoms": "fever and body ache since yesterday" }
```

Response `200`, normal:

```json
{
  "patient_id": "P009",
  "decision": "self_care",
  "confidence": 0.82,
  "first_aid": "Approved guidance from kb/.",
  "routed_to": ["Dr. Ali"],
  "escalated": false
}
```

Response `200`, `emergency` — `routed_to` carries **both** doctors and `escalated` is
`true`:

```json
{
  "patient_id": "P016",
  "decision": "emergency",
  "confidence": 0.97,
  "first_aid": "Approved emergency instruction, verbatim.",
  "routed_to": ["Dr. Ali", "Dr. Sara"],
  "escalated": true
}
```

Response `200`, refusal (age below the cutoff, or a guardrail refusal):

```json
{
  "patient_id": "P020",
  "decision": null,
  "refused": true,
  "refusal_reason": "out_of_scope_paediatric",
  "first_aid": null,
  "routed_to": ["Dr. Sara"],
  "escalated": false
}
```

`400` on invalid input, per step 01's rules. `404` with `patient_not_found` for an
unknown `patient_id`.

**`GET /morning-brief`**

```json
{
  "date": "2026-08-12",
  "per_doctor": [
    { "doctor": "Dr. Ali", "patient_count": 10, "emergencies_today": 0 },
    { "doctor": "Dr. Sara", "patient_count": 10, "emergencies_today": 1 }
  ]
}
```

**`GET /followups`** — patients overdue for a visit, by `days_since_last_visit`:

```json
{
  "generated_at": "2026-08-12T09:00:00Z",
  "overdue": [
    { "patient_id": "P011", "doctor": "Dr. Ali", "days_since_last_visit": 190 },
    { "patient_id": "P006", "doctor": "Dr. Sara", "days_since_last_visit": 170 }
  ]
}
```

Defaults taken. Override any of them and I will change it:

- ASSUMED: a refusal is `200` with `refused: true`. A refusal is a correct outcome, not a
  client error, and the receptionist must not see "error" when the system works.
- ASSUMED: `age` comes from the stored `patients` row; a supplied `age` disagreeing by more
  than 1 year returns `400`. The stored record is the clinical record.
- ASSUMED: a walk-in with no `patient_id` is rejected `400`. Creating patient records is not
  in any of the twelve steps.
- ASSUMED: escalation is synchronous — the response waits for the dispatch result — because
  async needs a durable outbox table that is not among the five specified.
- ASSUMED: `morning-brief` omits `emergencies_today` rather than reporting a misleading `0`.
  Decisions are not persisted until step 08; the field is added there.

BLOCKERS — this step cannot start until you answer:

1. **The overdue threshold in days.** `/followups` has no rule without it. The file's
   `notes` flag 5 patients as overdue (P004, P006, P011, P018, plus P016's recall) but state
   no cutoff; the longest gap in the data is ~200 days.
2. **The notification channel** — email, SMS, WhatsApp, Slack? `notify.py` is one function
   and one webhook call, and cannot be written without a destination. It is also what SC-3's
   "100% escalated" gets measured against.
## Acceptance criteria

- [ ] `POST /intake` for a `Dr. Ali` patient with a non-emergency decision returns
      `routed_to: ["Dr. Ali"]` only.
- [ ] `POST /intake` producing `emergency` returns **both** doctors in `routed_to`,
      `escalated: true`, and the emergency instruction verbatim in `first_aid`.
- [ ] The emergency instruction still reaches the caller when the notification dispatch
      fails — the receptionist is never left without it.
- [ ] `POST /intake` with an unknown `patient_id` returns `404 patient_not_found` and no
      fabricated record.
- [ ] `GET /morning-brief` returns both doctors with `patient_count: 10` each, matching
      the loaded data.
- [ ] `GET /followups` returns only patients past the agreed threshold, sorted by
      `days_since_last_visit` descending.
- [ ] `infra/n8n/walk-in-flow.json` imports into n8n without error and its HTTP node
      targets `POST /intake`.
- [ ] No response body or log line from any of the three endpoints contains a patient
      name, a note, or the raw symptom text (rule 4).

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **Terraform, the docker compose ops stack, or Ansible host prep** — step 04. The n8n
  deliverable this session is an exported workflow JSON file only; standing n8n up is
  step 04's job.
- **Prometheus metrics, `clinicops_escalations_total`, `GET /metrics`** — step 06. So
  SC-3's "100% of emergencies escalated" cannot be *measured* yet, only implemented.
- **Alert rules, Alertmanager routing, the alert webhook, `alert_history`** — step 07.
  A failed escalation is logged this session, not alerted on.
- **Writing decisions to `inference_log`** — step 08. This is why `morning-brief` cannot
  count today's real emergencies yet.
- **`patient_features` as a table, or recomputing features anywhere** —
  `days_since_last_visit` for `/followups` comes from `build_features.py` (step 01) or
  is computed in SQL from `last_visit`; do not add a second feature path (rule 2).
- **Loud ingest validation** — step 08.
- **`model_registry`, quality gate, shadow/canary, rollback, drift** — step 09.
- **The 30 eval cases and the harness** — step 10.
- **JSON logging with `request_id`, Filebeat, Elasticsearch, Kibana** — step 11.
- **A doctor-facing UI** — not in any step. Doctors receive notifications and read the
  two GET endpoints; building a UI is beyond the twelve steps.

## Manual verification

```bash
echo "--- routing invariant, deterministic (does not depend on model output) ---"
pytest tests/test_intake.py -q -k "routes_to_owning_doctor or emergency_routes_to_both"
echo "--- live: non-emergency routes to exactly one doctor ---"
curl -s -X POST localhost:7860/intake -H 'Content-Type: application/json' \
  -d '{"patient_id":"P009","symptoms":"mild sore throat since yesterday"}' \
  | grep -oE '"routed_to":\[[^]]*\]'
echo "--- live: emergency routes to both ---"
curl -s -X POST localhost:7860/intake -H 'Content-Type: application/json' \
  -d '{"patient_id":"P016","symptoms":"sudden severe breathlessness, lips turning blue"}' \
  | grep -oE '"routed_to":\[[^]]*\]|"escalated":(true|false)'
curl -s localhost:7860/morning-brief
curl -s localhost:7860/followups
```

Expected: both pytest cases pass — that is the proof of the objective, since it does not
depend on how the model happens to label a given sentence. The live calls then show
`["Dr. Ali"]` for the first and `["Dr. Ali","Dr. Sara"]` with `"escalated":true` for the
second; the brief shows 10 patients per doctor; followups lists overdue patients
longest-gap-first.
