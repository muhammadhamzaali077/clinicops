# Spec: Step 08 - Data pipeline

## Objective

`python -m src.pipeline.ingest` is all-or-nothing: it writes all 20 patients and their
features when the Excel satisfies the data contract, and writes nothing at all while exiting
non-zero when it does not.

(The `inference_log` write path is also built this session — see Deliverables and Acceptance
criteria — but ingest atomicity is the one outcome that defines "done".)

## Depends on

- **Step 00** — `load_patients.py`, `db.py`, and the `patients` table exist.
- **Step 01** — `src/pipeline/build_features.py` exists. **This session hardens it; it does
  not create or duplicate it.** Rule 2 forced it into existence in step 01.
- **Step 02–03** — `/ask` and `/intake` produce decisions that now need persisting.

## Deliverables

| File | Purpose |
| --- | --- |
| `src/pipeline/validate.py` | Validates every row against the 11-column contract. Returns all violations, not the first. |
| `src/pipeline/ingest.py` | Validate → load → build features, in one transaction. Replaces direct use of `load_patients.py`. |
| `src/pipeline/build_features.py` | **Hardened, not rewritten.** Gains `FEATURE_VERSION`, explicit patient-level vs per-encounter split, and a documented output schema. |
| `scripts/schema.sql` | Extended with `patient_features` and `inference_log`. |
| `src/common/inference_log.py` | The single write path for a decision row. |
| `tests/test_validate.py` | One test per validation rule, each against a deliberately broken fixture. |
| `tests/test_single_source_features.py` | Asserts no module outside `build_features.py` computes a feature. |
| `tests/test_inference_log.py` | A decision writes exactly one row, containing no name, note, or symptom text. |
| `tests/fixtures/*.xlsx` | Broken fixtures: bad `patient_id`, out-of-range age, duplicate ID, missing column, bad date serial. |

## Interface contract

**Validation rules** — a violation of any one fails the whole ingest:

| Rule | Check |
| --- | --- |
| Column set | Exactly the 11 contract columns, in order |
| `patient_id` | Matches `^P\d{3}$`, unique, non-blank |
| `age` | Integer, `0 <= age <= 120` |
| `gender` | Non-blank |
| `doctor` | Non-blank |
| `last_visit` | Decodes from an Excel serial to a plausible date |
| List columns | `; `-delimited; `none` treated as a sentinel, never a value |
| `notes` | May be blank (7 of 20 rows are) |
| Row count | At least 1 data row |

**Loud failure** means: non-zero exit, every violation printed with its row number and
column, **zero rows written**, and the existing `patients` contents unchanged. Not a
warning, not a partial load.

**`src/pipeline/build_features.py`** — the single source (rule 2):

```python
FEATURE_VERSION: str
def build_patient_features(row: PatientRow) -> PatientFeatures: ...   # stored, per patient
def build_request_features(symptoms: str, age: int) -> FeatureVector: ...  # per encounter
```

**`patient_features` columns**: `patient_id TEXT PK REFERENCES patients`,
`feature_version TEXT NOT NULL`, `age SMALLINT NOT NULL`,
`days_since_last_visit INTEGER NOT NULL`, `n_chronic_conditions SMALLINT NOT NULL`,
`n_allergies SMALLINT NOT NULL`, `n_current_medications SMALLINT NOT NULL`,
`computed_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

**`inference_log` columns** — per docs/03-data-contract.md: `id BIGSERIAL PK`,
`request_id UUID NOT NULL` (indexed), `patient_id TEXT NULL REFERENCES patients`,
`endpoint TEXT NOT NULL`, `age_at_request SMALLINT NOT NULL`, `model_version TEXT NOT NULL`,
`feature_version TEXT NOT NULL`,
`decision TEXT NOT NULL CHECK (decision IN ('self_care','see_doctor_today','emergency'))`,
`confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1)`,
`escalated BOOLEAN NOT NULL DEFAULT FALSE`, `escalated_at TIMESTAMPTZ NULL`,
`refused BOOLEAN NOT NULL DEFAULT FALSE`, `refusal_reason TEXT NULL`,
`human_override TEXT NULL`, `latency_ms INTEGER NOT NULL`,
`tool_calls SMALLINT NOT NULL DEFAULT 0`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

Defaults taken. Override any of them and I will change it:

- ASSUMED: `decision` and `confidence` become **nullable**, because a refusal has neither
  and a sentinel would corrupt every aggregate over the column. This amends
  docs/03-data-contract.md.
- ASSUMED: the split above — `patient_features` holds stored patient-level features;
  per-request symptom features are computed in-process by the same module (rule 2 holds
  either way).
- ASSUMED: `days_since_last_visit` is recomputed on read, not stored-and-refreshed. A stored
  value is wrong by construction after midnight.
- ASSUMED: the validator fails the whole load. With 20 rows and one file, a partial load is
  worse than a blocked one.
- ASSUMED: no symptom text is stored, accepting that a specific bad decision can never be
  investigated afterwards. This is the strictest reading of rule 4 and the safest default.

BLOCKERS — this step cannot start until you answer:

1. **Is the `inference_log` write on the critical path?** If Postgres is unreachable, does an
   emergency still get answered unlogged, or does the request fail? Both readings are
   defensible and both are clinically consequential, so this one is yours.
2. **`inference_log` retention**, and whether a deletion job exists. Unbounded retention of
   clinical decisions is a data-protection decision.

## Acceptance criteria

- [ ] `python -m src.pipeline.ingest` against the real file exits 0, leaves 20 rows in
      `patients` and 20 in `patient_features`.
- [ ] Ingest against a fixture with a malformed `patient_id` exits non-zero, prints the
      offending row and column, and leaves the `patients` table unchanged — proven by an
      identical `md5(string_agg(...))` checksum of all rows taken before and after.
- [ ] Ingest against a fixture with `age = 200` and a duplicate `patient_id` reports
      **both** violations, not just the first.
- [ ] `pytest tests/test_single_source_features.py` passes: no module outside
      `src/pipeline/build_features.py` computes a feature, and both training and serving
      import it.
- [ ] One `POST /triage` writes exactly one `inference_log` row with a valid `request_id`,
      `model_version`, `feature_version`, `decision`, and `latency_ms`.
- [ ] One `POST /intake` returning `emergency` writes a row with `escalated=true` and a
      non-null `escalated_at`.
- [ ] A guardrail refusal writes a row with `refused=true` and a `refusal_reason` code.
- [ ] No `inference_log` row contains a patient name, a note, or any raw symptom text —
      verified by querying every text column for a known name from the file (rule 4).

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **`model_registry`, the quality gate, shadow or canary serving, rollback, drift
  detection, the model card** — step 09. `inference_log.model_version` is a plain string
  this session; the foreign key to `model_registry` is added in step 09, not stubbed now.
- **Retraining from `inference_log`** — step 09 at the earliest, and it is blocked on the
  open question about storing symptom text. Do not build a training-data exporter.
- **The 30 eval cases or the eval harness** — step 10. The broken-fixture tests here are
  pipeline tests, not the `safety_pass` gate.
- **New Prometheus metrics for ingest or feature freshness** — step 06 defined exactly
  five. A sixth is a rule-1 conversation.
- **Alert rules for a failed ingest** — step 07 defined exactly three. A loud failure exits
  non-zero; it does not page.
- **JSON logging with `request_id`, Filebeat, Elasticsearch** — step 11. The `request_id`
  written here has nothing to join against until then.
- **Scheduled or automated re-ingest** — no step covers it, and whether the Excel is
  re-loaded on a schedule is an open decision.
- **Backfilling `inference_log` for decisions made in steps 02–03** — those were never
  persisted and cannot be recovered. Do not invent history.

## Manual verification

```bash
cp data/raw/patients.xlsx /tmp/good.xlsx
CK="SELECT md5(string_agg(t::text, '' ORDER BY t.patient_id)) FROM patients t;"

echo "=== checksum before the bad ingest ==="
psql "$DATABASE_URL" -t -c "$CK"
python -m src.pipeline.ingest --file tests/fixtures/bad_patient_id.xlsx; echo "expect non-zero: $?"
echo "=== checksum after: must be identical ==="
psql "$DATABASE_URL" -t -c "$CK"

echo "=== good file: all 20 patients and 20 feature rows ==="
python -m src.pipeline.ingest --file /tmp/good.xlsx; echo "expect 0: $?"
psql "$DATABASE_URL" -c "SELECT (SELECT count(*) FROM patients) AS patients, (SELECT count(*) FROM patient_features) AS features;"

echo "=== a decision is logged, with no patient text anywhere in the row ==="
curl -s -X POST localhost:7860/triage -H 'Content-Type: application/json' \
  -d '{"symptoms":"fever and body ache","age":25}' > /dev/null
psql "$DATABASE_URL" -c "SELECT request_id, endpoint, decision, confidence, latency_ms FROM inference_log ORDER BY id DESC LIMIT 1;"
psql "$DATABASE_URL" -t -c "SELECT count(*) AS leaks FROM inference_log l WHERE l::text ~* '(Ayesha|Bilal|Sana|fever|body ache|wheez)';"
```

Expected: the two checksums are **identical strings** — that is the proof that a failed
ingest wrote nothing; the bad ingest exits non-zero; the good file exits 0 with
`patients=20` and `features=20`; one fresh `inference_log` row; and `leaks = 0` from a scan
of the entire row cast to text, not just one column.
