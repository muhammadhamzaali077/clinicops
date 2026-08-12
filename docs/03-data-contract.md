# 03 — Data Contract

Three layers: the Excel source, the Postgres schema, and the API shapes. A change
to any one of them is a change to this document first.

## Part 1 — The Excel source

File: `data/raw/patients.xlsx` — one sheet, 21 rows (1 header + 20 patients),
11 columns. Column names, order, and observed values below were read from the
actual file, not assumed.

| # | Column | Excel storage | Contract type | Observed in the 20 rows |
| --- | --- | --- | --- | --- |
| 1 | `patient_id` | shared string | `str`, pattern `^P\d{3}$` | `P001`–`P020`, unique, no gaps, no blanks |
| 2 | `name` | shared string | `str`, non-empty | 20 distinct full names, e.g. `Ayesha Khan` |
| 3 | `age` | numeric | `int`, years | 7 … 74. Min 7 (P020), max 74 (P016). No blanks |
| 4 | `gender` | shared string | `str`, enum | `F` (10), `M` (10) — only these two values |
| 5 | `doctor` | shared string | `str`, enum | `Dr. Ali` (10), `Dr. Sara` (10) — exactly the two doctors |
| 6 | `last_visit` | **numeric — Excel date serial** | `date` | 46044 … 46240 → **2026-01-22 … 2026-08-06** |
| 7 | `chronic_conditions` | shared string | `str`, `; `-delimited list, `none` sentinel | 12 distinct; e.g. `diabetes; hypertension`, `COPD; hypertension`, `none` |
| 8 | `allergies` | shared string | `str`, `; `-delimited list, `none` sentinel | 7 distinct: `aspirin`, `dust`, `latex`, `none`, `peanuts`, `penicillin`, `sulfa drugs` |
| 9 | `current_medications` | shared string | `str`, `; `-delimited list, `none` sentinel | 13 distinct; e.g. `metformin; amlodipine`, `epinephrine auto-injector`, `none` |
| 10 | `past_symptoms` | shared string | `str`, `; `-delimited list | 20 distinct; e.g. `cough; wheezing`, `chest tightness on exertion` |
| 11 | `notes` | shared string | `str`, nullable | 13 distinct values + **7 blanks**. Free text, e.g. `Overdue for HbA1c test` |

Facts that the loader and the validator (step 08) have to handle:

- **`last_visit` is a number, not a date.** It is stored as an Excel serial
  (base 1899-12-30), so a naïve read yields `46217`, not `2026-01-22`. The loader
  must convert. The validator must reject a serial that decodes outside a
  plausible range.
- **`notes` is blank for 7 of 20 rows.** Blank means "no note", not "unknown".
- **`none` is a sentinel, not a value.** It appears in `chronic_conditions`,
  `allergies`, and `current_medications`. It means "no known items", and must not
  become a condition, allergy, or medication named "none".
- **List columns use `; ` (semicolon + space).** Consistent across all four list
  columns in all 20 rows.
- **No column is entirely blank, and no `patient_id` repeats.**

DECISION NEEDED: `last_visit` — date only, or datetime? Every value in the file is
a whole-day serial with no time component. If the contract is `timestamptz`, what
timezone does a bare date mean (clinic-local Asia/Karachi, or UTC)? This affects
`days_since_last_visit` in `build_features.py`, and getting it wrong shifts the
feature by up to a day.

DECISION NEEDED: `gender` — is it a closed enum of `F`/`M` (matching the file), or
an open string? A `CHECK` constraint on two values will reject the first patient
who is neither.

DECISION NEEDED: `doctor` — stored as the literal string `Dr. Ali` / `Dr. Sara`,
or normalised into a `doctors` table with a foreign key? A string means routing
breaks on a typo or a third doctor; a table is a sixth table, and the brief
specifies five.

DECISION NEEDED: The list columns — kept as delimited strings in Postgres (faithful
to the source), or split into `TEXT[]` / child tables (queryable)? "Which patients
are allergic to penicillin?" is easy in one shape and painful in the other.

DECISION NEEDED: What does the validator do when it finds a bad row — fail the
whole load, or skip the row and report? With 20 rows and one file, an all-or-nothing
load is defensible, but it means one typo blocks the clinic.

DECISION NEEDED: `age` is a stored integer, but a patient's age changes while
`last_visit` does not. Is `age` re-derived from a date of birth (which the file
does not contain), or is the stored integer accepted as fixed and re-loaded when
the Excel is updated? Triage depends on age, so a stale age is a clinical input.

DECISION NEEDED: Is `name` loaded into Postgres at all? Rule 4 forbids logging it.
If names are stored, `SELECT *` in any debugging session or any error message
containing a row becomes a rule-4 hazard; if they are not, no human can confirm
they are looking at the right patient. See the same question in docs/01-PRD.md.

## Part 2 — Postgres schema

Five tables. Every column typed. Reached only via `src/common/db.py` (rule 6).

Conventions used below, all open to override:

DECISION NEEDED: Naming and key conventions across all five tables — surrogate
`BIGSERIAL` primary keys or natural keys; `TIMESTAMPTZ` or `TIMESTAMP`;
`created_at`/`updated_at` on every table or only where needed. The schema below
picks one style so it is concrete; confirm or replace it.

### `patients`

The 20 rows from the Excel, one row per patient. Authoritative record for lookup.

| Column | Type | Constraints | Source |
| --- | --- | --- | --- |
| `patient_id` | `TEXT` | `PRIMARY KEY`, `CHECK (patient_id ~ '^P\d{3}$')` | Excel col 1 |
| `name` | `TEXT` | `NOT NULL` | Excel col 2 — see DECISION NEEDED above |
| `age` | `SMALLINT` | `NOT NULL`, `CHECK (age BETWEEN 0 AND 120)` | Excel col 3 |
| `gender` | `TEXT` | `NOT NULL` | Excel col 4 |
| `doctor` | `TEXT` | `NOT NULL` | Excel col 5 |
| `last_visit` | `DATE` | `NOT NULL` | Excel col 6, decoded from serial |
| `chronic_conditions` | `TEXT` | `NOT NULL DEFAULT 'none'` | Excel col 7 |
| `allergies` | `TEXT` | `NOT NULL DEFAULT 'none'` | Excel col 8 |
| `current_medications` | `TEXT` | `NOT NULL DEFAULT 'none'` | Excel col 9 |
| `past_symptoms` | `TEXT` | `NOT NULL` | Excel col 10 |
| `notes` | `TEXT` | `NULL` | Excel col 11 — blank becomes `NULL` |
| `loaded_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | Set by the loader |
| `source_file` | `TEXT` | `NOT NULL` | Provenance, e.g. `patients.xlsx` |

DECISION NEEDED: Is a re-load an `UPSERT` on `patient_id`, a truncate-and-replace,
or an append with versioning? Truncate loses any row edited in Postgres; upsert
leaves deleted patients behind forever.

### `patient_features`

The output of `src/pipeline/build_features.py` — the only place features are
computed (rule 2). Both training and serving read features from here or from that
module; nothing else derives them.

| Column | Type | Constraints |
| --- | --- | --- |
| `patient_id` | `TEXT` | `PRIMARY KEY REFERENCES patients(patient_id) ON DELETE CASCADE` |
| `feature_version` | `TEXT` | `NOT NULL` — which version of `build_features.py` produced this |
| `age` | `SMALLINT` | `NOT NULL` |
| `days_since_last_visit` | `INTEGER` | `NOT NULL` |
| `n_chronic_conditions` | `SMALLINT` | `NOT NULL` |
| `n_allergies` | `SMALLINT` | `NOT NULL` |
| `n_current_medications` | `SMALLINT` | `NOT NULL` |
| `computed_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

DECISION NEEDED: **The feature list itself is not decided.** The five above are the
obvious arithmetic on the 11 columns, but the model classifies *symptoms + age*,
and symptom text is the input that actually carries the signal. What is the full
feature set, and how is symptom text represented (bag of words, TF-IDF, a
hand-built symptom vocabulary)? This is step 01's central question, and rule 2
means the answer must live in exactly one file.

DECISION NEEDED: `patient_features` is keyed per patient, but triage features
depend on the *symptoms presented at this visit*, which are per-encounter and not
in the Excel at all. Is this table the stored patient-level features only, with
per-request symptom features computed in-process at serving time (still inside
`build_features.py`), or does it need an encounter key? As written, a table keyed
on `patient_id` alone cannot hold a per-visit feature row.

DECISION NEEDED: Is `patient_features` refreshed on a schedule?
`days_since_last_visit` goes stale every midnight, so a stored value is wrong by
construction unless it is recomputed at read time.

### `inference_log`

One row per triage decision. Rule 4 applies with full force: IDs and decisions
only. No name, no notes, no raw symptom text.

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | `BIGSERIAL` | `PRIMARY KEY` |
| `request_id` | `UUID` | `NOT NULL`, indexed — ties the row to logs in Kibana |
| `patient_id` | `TEXT` | `NULL REFERENCES patients(patient_id)` — null for an unknown walk-in |
| `endpoint` | `TEXT` | `NOT NULL` — e.g. `/intake`, `/ask` |
| `age_at_request` | `SMALLINT` | `NOT NULL` |
| `model_version` | `TEXT` | `NOT NULL REFERENCES model_registry(model_version)` |
| `feature_version` | `TEXT` | `NOT NULL` |
| `decision` | `TEXT` | `NOT NULL CHECK (decision IN ('self_care','see_doctor_today','emergency'))` |
| `confidence` | `NUMERIC(4,3)` | `NOT NULL CHECK (confidence BETWEEN 0 AND 1)` |
| `escalated` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` — were both doctors notified |
| `escalated_at` | `TIMESTAMPTZ` | `NULL` |
| `refused` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` — guardrail refusal |
| `refusal_reason` | `TEXT` | `NULL` — enum-ish code, never free text containing input |
| `human_override` | `TEXT` | `NULL CHECK (human_override IN ('self_care','see_doctor_today','emergency'))` |
| `latency_ms` | `INTEGER` | `NOT NULL` |
| `tool_calls` | `SMALLINT` | `NOT NULL DEFAULT 0` — against the max of 6 |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

DECISION NEEDED: Storing **no** symptom text makes it impossible to investigate a
bad triage decision or to build a labelled training set from real traffic. Is there
an approved mechanism for that (a hashed or vocabulary-mapped representation, a
separate access-controlled store, doctor-entered labels only), or is the tradeoff
accepted — that ClinicOps can never audit *why* it made a specific call?

DECISION NEEDED: Retention. How long do `inference_log` rows live, and is there a
deletion job? Unbounded retention of clinical decisions is a data-protection
question, not a disk-space one.

DECISION NEEDED: Where does a `human_override` come from — a doctor-facing endpoint,
n8n, or manual SQL? The column is only meaningful if something writes it.

DECISION NEEDED: `confidence` for a *refused* or *escalated-to-human* request —
what is stored when there is no model output at all? `NOT NULL` forces a value.

### `alert_history`

One row per alert fired by Alertmanager and handled by `src/ops/` (step 07).

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | `BIGSERIAL` | `PRIMARY KEY` |
| `alert_name` | `TEXT` | `NOT NULL` — matches the Prometheus rule name |
| `fingerprint` | `TEXT` | `NOT NULL`, indexed — Alertmanager dedup key |
| `severity` | `TEXT` | `NOT NULL` |
| `status` | `TEXT` | `NOT NULL CHECK (status IN ('firing','resolved'))` |
| `started_at` | `TIMESTAMPTZ` | `NOT NULL` |
| `resolved_at` | `TIMESTAMPTZ` | `NULL` |
| `triage_summary` | `TEXT` | `NULL` — output of the alert-triage agent |
| `rca_summary` | `TEXT` | `NULL` — output of the RCA agent |
| `action_taken` | `TEXT` | `NULL` — the whitelisted action name, or `NULL` if none |
| `action_result` | `TEXT` | `NULL CHECK (action_result IN ('success','failed','refused','awaiting_approval'))` |
| `approved_by` | `TEXT` | `NULL` — who approved a `NEEDS_APPROVAL` action |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

DECISION NEEDED: `severity` levels — what is the set (`critical`/`warning`/`info`?)
and does it map to a paging policy?

DECISION NEEDED: Is `alert_history` also the audit log required by
docs/05-ops-spec.md, or is a separate audit table needed? An audit trail that only
exists for alert-triggered actions misses manually invoked ones, and the brief
specifies five tables with no audit table among them.

### `model_registry`

One row per trained model version (step 09).

| Column | Type | Constraints |
| --- | --- | --- |
| `model_version` | `TEXT` | `PRIMARY KEY` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |
| `algorithm` | `TEXT` | `NOT NULL` — the scikit-learn estimator used |
| `feature_version` | `TEXT` | `NOT NULL` |
| `training_rows` | `INTEGER` | `NOT NULL` |
| `emergency_recall` | `NUMERIC(4,3)` | `NOT NULL` — against SC-4's 0.95 floor |
| `accuracy` | `NUMERIC(4,3)` | `NOT NULL` |
| `safety_pass` | `NUMERIC(4,3)` | `NOT NULL` — must be 1.000 to promote |
| `status` | `TEXT` | `NOT NULL CHECK (status IN ('candidate','shadow','canary','production','rolled_back','archived'))` |
| `promoted_at` | `TIMESTAMPTZ` | `NULL` |
| `git_sha` | `TEXT` | `NOT NULL` — the commit that produced it |
| `model_card` | `TEXT` | `NULL` — path or inline card |
| `artifact_uri` | `TEXT` | `NULL` — where the `.pkl` actually lives |

DECISION NEEDED: `artifact_uri` points where? Tied to the same open question in
docs/02-architecture.md: the Space is stateless and `models/*.pkl` is gitignored,
so the artifact is either bytes in Postgres (a `BYTEA` column this table does not
have), baked into the image, or in external storage that the approved tool list
does not name.

DECISION NEEDED: `model_version` format — semver, timestamp, git SHA, or an
incrementing integer?

DECISION NEEDED: Can two rows hold `status = 'production'` at once? Shadow and
canary deployment (step 09) implies more than one live model, so the constraint
needs to state what "live" means.

## Part 3 — API shapes

Every shape below is a Pydantic model in `src/api/schemas.py` (CLAUDE.md
convention). JSON shown is the wire format.

Endpoints named by CLAUDE.md: `POST /ask` (step 02), `POST /intake` (step 03).
The history-lookup endpoint is required by the PRD but never named.

DECISION NEEDED: The history-lookup endpoint's method and path. Proposed below as
`GET /patients/{patient_id}`, which is conventional but unconfirmed — and a `GET`
that returns patient data to a public Space is exactly the exposure flagged in
docs/01-PRD.md.

DECISION NEEDED: API versioning — is there a `/v1` prefix? Cheap now, painful to
retrofit.

DECISION NEEDED: A single error envelope for every 4xx/5xx. Proposed below; confirm
the shape and confirm that no error message may echo input (rule 4 — an error that
quotes the offending symptom text logs symptom text).

### `GET /patients/{patient_id}` — history lookup

Request: path parameter only.

Response `200`:

```json
{
  "request_id": "3f6c1b8e-1c2a-4d5e-9f00-2a1b3c4d5e6f",
  "patient_id": "P014",
  "age": 66,
  "gender": "M",
  "doctor": "Dr. Sara",
  "last_visit": "2026-08-01",
  "days_since_last_visit": 11,
  "chronic_conditions": ["hypertension", "diabetes"],
  "allergies": ["none"],
  "current_medications": ["metformin", "losartan"],
  "past_symptoms": ["chest tightness on exertion"],
  "notes": "Cardiology referral pending",
  "name": "DECISION NEEDED: is name returned in the response body at all?"
}
```

DECISION NEEDED: Does this response include `name` and `notes`? Both are rule-4
"never log" fields. Returning them to the receptionist is arguably the whole point
of a history lookup; it also means they cross the network and land in a browser.

DECISION NEEDED: Are the list fields returned as arrays (as above) or as the raw
`; `-delimited strings from the source?

Response `404`: standard error envelope, `code: "patient_not_found"`.

### `POST /intake` — walk-in triage

Request:

```json
{
  "patient_id": "P009",
  "age": 25,
  "symptoms": "fever and body ache since yesterday"
}
```

DECISION NEEDED: Is `age` supplied by the receptionist, or read from the
`patients` row? Supplying it allows a mismatch with stored data; reading it makes
triage impossible for an unknown walk-in.

DECISION NEEDED: Is `symptoms` free text, or a checklist of predefined symptom
codes? Free text is the natural front-desk input and the harder ML problem;
a checklist is trainable on 20 rows and constrains the receptionist.

Response `200`:

```json
{
  "request_id": "3f6c1b8e-1c2a-4d5e-9f00-2a1b3c4d5e6f",
  "patient_id": "P009",
  "decision": "self_care",
  "confidence": 0.82,
  "first_aid": "Approved guidance text served verbatim from kb/.",
  "routed_to": "Dr. Ali",
  "escalated": false,
  "model_version": "DECISION NEEDED: version format",
  "disclaimer": "DECISION NEEDED: exact disclaimer wording, doctor-approved"
}
```

Response `200`, `emergency` case:

```json
{
  "request_id": "3f6c1b8e-1c2a-4d5e-9f00-2a1b3c4d5e6f",
  "patient_id": "P016",
  "decision": "emergency",
  "confidence": 0.97,
  "first_aid": "Approved emergency instruction, served verbatim and never softened.",
  "routed_to": ["Dr. Ali", "Dr. Sara"],
  "escalated": true,
  "model_version": "…",
  "disclaimer": "…"
}
```

Note the shape change: `routed_to` is a string for normal cases and an array for
emergencies.

DECISION NEEDED: Should `routed_to` always be an array to keep one stable shape,
or does the client rely on the distinction?

Response `200`, refusal (out of scope — e.g. under the age cutoff):

```json
{
  "request_id": "3f6c1b8e-1c2a-4d5e-9f00-2a1b3c4d5e6f",
  "patient_id": "P020",
  "decision": null,
  "refused": true,
  "refusal_reason": "out_of_scope_paediatric",
  "first_aid": null,
  "routed_to": "Dr. Sara",
  "escalated": false
}
```

DECISION NEEDED: Is a refusal `200` with `refused: true`, or a `4xx`? A refusal is
a successful, intended outcome, not a client error — but clients often treat
non-2xx as failure, and the receptionist must not see "error" when the system is
working correctly.

DECISION NEEDED: The closed set of `refusal_reason` codes. Every guardrail in
docs/04-agent-spec.md needs one, and they must never contain input text.

### `POST /ask` — the agent endpoint

Request:

```json
{
  "patient_id": "P001",
  "question": "The patient is wheezing and short of breath. What should we do?"
}
```

Response `200`:

```json
{
  "request_id": "3f6c1b8e-1c2a-4d5e-9f00-2a1b3c4d5e6f",
  "patient_id": "P001",
  "answer": "Prose for the receptionist. No diagnosis, no medication, no dose.",
  "decision": "see_doctor_today",
  "confidence": 0.71,
  "tool_calls": 3,
  "escalated": false,
  "refused": false,
  "refusal_reason": null
}
```

DECISION NEEDED: Does `POST /ask` always produce a triage `decision`, or only when
the question warrants triage? A question like "when did this patient last visit?"
has an answer and no triage decision.

DECISION NEEDED: Is `POST /ask` conversational (multi-turn, with history) or
single-shot? A `conversation_id` changes the schema and adds state, which the
compute tier cannot hold (rule 6).

DECISION NEEDED: Who may call `/ask` — receptionist only, doctors only, or both?
The refusal rules differ if a doctor is asking.

### `GET /health`

```json
{ "status": "ok", "environment": "production", "model_version": "…", "db": "ok" }
```

DECISION NEEDED: Does `/health` check Postgres connectivity (accurate, but a slow
or flapping DB then marks the Space unhealthy) or only report process liveness?

### `GET /metrics`

Prometheus text exposition format. Metrics defined in docs/05-ops-spec.md.

### Error envelope

```json
{
  "request_id": "3f6c1b8e-1c2a-4d5e-9f00-2a1b3c4d5e6f",
  "code": "patient_not_found",
  "message": "Safe, fixed wording. Never echoes input."
}
```
