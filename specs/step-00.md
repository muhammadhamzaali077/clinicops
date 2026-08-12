# Spec: Step 00 - Foundation

## Objective

`python -m src.pipeline.load_patients` loads all 20 rows of
`data/raw/patients.xlsx` into the `patients` table in Postgres, and running it
twice leaves exactly 20 rows.

## Depends on

Nothing. This is the first step. It requires only a reachable Postgres instance
and `DATABASE_URL` set in the environment.

## Deliverables

| File | Purpose |
| --- | --- |
| `src/common/config.py` | Reads `DATABASE_URL`, `OPENAI_API_KEY`, `ENVIRONMENT` from the environment; fails loudly at import if a required one is missing. |
| `src/common/db.py` | The only path to Postgres: connection handling, parameterised query and execute helpers. |
| `src/pipeline/load_patients.py` | Reads the Excel file, decodes the 11 columns, writes them to `patients`. Runnable as `python -m`. |
| `scripts/schema.sql` | DDL for the `patients` table only. |
| `tests/test_config.py` | Config raises on a missing variable and never returns a default secret. |
| `tests/test_db.py` | `db.py` connects, round-trips a query, and closes cleanly. |
| `tests/test_load_patients.py` | The loader produces 20 rows with the expected types, and is idempotent. |
| `requirements.txt` | Pinned dependencies for this step. |

## Interface contract

No HTTP surface in this step.

**`src/common/config.py`**

```python
DATABASE_URL: str      # required, no default
OPENAI_API_KEY: str    # required, no default — unused until step 02
ENVIRONMENT: str       # required, no default
```

**`src/common/db.py`** — the only module that touches Postgres (rule 6).

```python
def get_connection() -> Connection: ...
def query(sql: str, params: tuple) -> list[dict]: ...
def execute(sql: str, params: tuple) -> int: ...
```

**`patients` table** — per docs/03-data-contract.md:

| Column | Type | Constraints |
| --- | --- | --- |
| `patient_id` | `TEXT` | `PRIMARY KEY`, `CHECK (patient_id ~ '^P\d{3}$')` |
| `name` | `TEXT` | `NOT NULL` |
| `age` | `SMALLINT` | `NOT NULL`, `CHECK (age BETWEEN 0 AND 120)` |
| `gender` | `TEXT` | `NOT NULL` |
| `doctor` | `TEXT` | `NOT NULL` |
| `last_visit` | `DATE` | `NOT NULL` — decoded from the Excel serial, base 1899-12-30 |
| `chronic_conditions` | `TEXT` | `NOT NULL DEFAULT 'none'` |
| `allergies` | `TEXT` | `NOT NULL DEFAULT 'none'` |
| `current_medications` | `TEXT` | `NOT NULL DEFAULT 'none'` |
| `past_symptoms` | `TEXT` | `NOT NULL` |
| `notes` | `TEXT` | `NULL` — the 7 blank cells become `NULL` |
| `loaded_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |
| `source_file` | `TEXT` | `NOT NULL` |

Source-data facts the loader must handle: `last_visit` arrives as an integer
serial (46044–46240 → 2026-01-22 to 2026-08-06); `notes` is blank in 7 of 20 rows;
`none` in the three list columns is a sentinel meaning "no known items"; list
columns are `; `-delimited.

Defaults taken so this step is implementable. Override any of them and I will change it:

- ASSUMED: re-load is an `UPSERT` on `patient_id` — satisfies the idempotency criterion.
- ASSUMED: `last_visit` is `DATE`. Every value in the file is a whole-day serial, so no
  time and no timezone is stored.
- ASSUMED: `scripts/schema.sql` is applied by hand with `psql -f` before the first load.
- ASSUMED: `name` is stored, because instant history lookup is one of the clinic's two
  stated needs. Rule 4 still forbids it in every log line.

BLOCKER — this step cannot start until you answer:

1. **The Excel reader.** Python's stdlib cannot parse `.xlsx` directly; `openpyxl` is
   implied but is not on the approved tool list (rule 1). Grant it, or the loader parses
   the zip/XML with stdlib — which is proven to work, since that is how the 11 columns in
   docs/03-data-contract.md were read.

## Acceptance criteria

- [ ] `python -m src.pipeline.load_patients` exits 0 and
      `SELECT count(*) FROM patients` returns exactly `20`.
- [ ] Running the loader a second time still leaves exactly `20` rows — no
      duplicates, no error.
- [ ] `SELECT last_visit FROM patients WHERE patient_id='P001'` returns
      `2026-01-22`, not `46217` — the serial is decoded, not stored raw.
- [ ] `SELECT count(*) FROM patients WHERE notes IS NULL` returns exactly `7`.
- [ ] `SELECT count(DISTINCT doctor) FROM patients` returns `2`, and each doctor
      owns 10 patients.
- [ ] Importing `src.common.config` with `DATABASE_URL` unset raises immediately
      with a message naming the missing variable, and no secret appears anywhere in
      the source (`grep -rn "postgres://" src/` finds nothing).
- [ ] `pytest tests/` passes, and no module other than `src/common/db.py` imports a
      database driver.

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **The FastAPI app, `src/api/`, or the Dockerfile** — step 01. No HTTP surface
  exists in this session, so there is nothing to serve and nothing to containerise.
- **`src/pipeline/build_features.py` or any feature computation** — step 01. Tempting
  because the loader already holds the parsed rows, but a feature computed here would
  be a second home for features and a direct rule-2 violation.
- **The `patient_features` and `inference_log` tables** — step 08. `scripts/schema.sql`
  contains `patients` and nothing else this session.
- **The triage model or any scikit-learn import** — step 01.
- **The agent, tools, or `OPENAI_API_KEY` usage** — step 02. Config reads the variable
  now; nothing consumes it until step 02.
- **Validation that fails loudly on a malformed Excel file** — step 08. This session
  assumes the file is well-formed; step 08 makes that an enforced contract.
- **Prometheus metrics or `/metrics`** — step 06.
- **JSON structured logging with `request_id`** — step 11. Plain logging is fine here.

## Manual verification

```bash
python -m src.pipeline.load_patients \
  && python -m src.pipeline.load_patients \
  && psql "$DATABASE_URL" -c "SELECT count(*) AS rows, count(DISTINCT doctor) AS doctors, count(*) FILTER (WHERE notes IS NULL) AS blank_notes FROM patients;" \
  && psql "$DATABASE_URL" -c "SELECT patient_id, age, doctor, last_visit FROM patients ORDER BY patient_id LIMIT 3;"
```

Expected: `rows=20`, `doctors=2`, `blank_notes=7`, and `P001 | 34 | Dr. Ali | 2026-01-22`.
