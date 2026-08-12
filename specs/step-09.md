# Spec: Step 09 - MLOps

## Objective

No model version can reach `status = 'production'` unless its `safety_pass` is exactly 1.00
and its `emergency_recall` is at least 0.95.

(Shadow and canary serving, drift detection, the model card, and sub-60-second rollback are
also built this session — see Deliverables and Acceptance criteria — but the promotion gate is
the one outcome that defines "done".)

## Depends on

- **Step 01** — `train.py`, `predict.py`, and the model artifact exist.
- **Step 06** — metrics exist, so shadow and canary comparisons are observable.
- **Step 08** — `inference_log` exists, so shadow predictions have somewhere to be
  recorded and drift has a baseline to measure against.

## Deliverables

| File | Purpose |
| --- | --- |
| `scripts/schema.sql` | Extended with the `model_registry` table. |
| `src/ml/registry.py` | Register a candidate, query versions, promote, mark rolled back. |
| `src/ml/quality_gate.py` | Evaluates a candidate against the thresholds; returns pass/fail with reasons. |
| `src/ml/serving.py` | Resolves which model serves a request: production, shadow, or canary at a percentage. |
| `src/ml/drift.py` | Compares the live decision distribution and feature distribution against the production baseline. |
| `scripts/rollback_model.py` | Promotes the previous production version. Timed, and must complete in under 60 s. |
| `scripts/generate_model_card.py` | Writes `docs/model-cards/<version>.md` from the registry row. |
| `tests/test_quality_gate.py` | A candidate with `safety_pass < 1.00` is blocked; one meeting both thresholds passes. |
| `tests/test_rollback.py` | Rollback restores the previous version and completes inside the budget. |

## Interface contract

**`model_registry` columns** — per docs/03-data-contract.md: `model_version TEXT PK`,
`created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `algorithm TEXT NOT NULL`,
`feature_version TEXT NOT NULL`, `training_rows INTEGER NOT NULL`,
`emergency_recall NUMERIC(4,3) NOT NULL`, `accuracy NUMERIC(4,3) NOT NULL`,
`safety_pass NUMERIC(4,3) NOT NULL`,
`status TEXT NOT NULL CHECK (status IN ('candidate','shadow','canary','production','rolled_back','archived'))`,
`promoted_at TIMESTAMPTZ NULL`, `git_sha TEXT NOT NULL`, `model_card TEXT NULL`,
`artifact_uri TEXT NULL`.

**Quality gate** — `python -m src.ml.quality_gate --version <v>`:

```json
{
  "version": "<v>",
  "verdict": "BLOCK",
  "checks": [
    { "name": "safety_pass",      "value": 0.967, "threshold": 1.0,  "pass": false },
    { "name": "emergency_recall", "value": 0.96,  "threshold": 0.95, "pass": true }
  ]
}
```

`safety_pass` must be **exactly 1.00**. No override, no exception, no flag that bypasses
it. Exit 0 on `PASS`, 1 on `BLOCK`.

**Serving modes:**

| Mode | Behaviour |
| --- | --- |
| `production` | Serves the response. |
| `shadow` | Runs alongside production; its prediction is recorded but **never returned** to the caller. |
| `canary` | Serves a configured percentage of live traffic. |

**Drift** — `python -m src.ml.drift` compares the last N decisions against the production
baseline and reports per-label rate change plus a drift verdict.

**Rollback** — `python scripts/rollback_model.py` sets the current `production` row to
`rolled_back`, promotes the previous one, and prints elapsed seconds. Budget: **under 60 s**.

Defaults taken. Override any of them and I will change it:

- ASSUMED: exactly one row may hold `status='production'`, enforced by a unique partial index.
  `shadow` and `canary` are separate statuses, so "live" can mean up to three rows with
  distinct roles.
- ASSUMED: `model_version` is `m<YYYYMMDD>-<git-sha7>`, matching step 01.
- ASSUMED: the canary serves 10% of traffic, split by hashing `request_id` so a single
  stateless container needs no shared state.
- ASSUMED: drift is reported, never alerted — step 07 fixed the alert set at three rules.
  Threshold: any label's rate moving more than 10 percentage points against the production
  baseline over a 7-day window.
- ASSUMED: `safety_pass` is read from a results file this session and step 10 wires the real
  harness into it. The gate is therefore tested against fixed values here, which is exactly
  what its unit tests need.
- ASSUMED: the model card is generated from the registry row, then reviewed by a human before
  promotion.

BLOCKERS — this step cannot start until you answer:

1. **Where `artifact_uri` points.** The Space is stateless and `models/*.pkl` is gitignored,
   so the artifact is bytes in Postgres (a `BYTEA` column this table lacks), baked into the
   image, or in external storage the approved list does not name. **Sub-60-second rollback is
   only achievable for some of those answers** — baked-into-the-image means a rollback is an
   image redeploy, which will not make 60 s.
2. **Who approves a promotion** — engineer or doctor? `promote_model` is `NEEDS_APPROVAL` in
   step 07's whitelist and it changes clinical behaviour.

## Acceptance criteria

- [ ] `python -m src.ml.registry --register` inserts a `candidate` row with all `NOT NULL`
      columns populated, including `git_sha`.
- [ ] A candidate with `safety_pass = 0.967` is blocked by the gate, which exits 1 and names
      `safety_pass` in its reasons.
- [ ] A candidate with `safety_pass = 1.000` and `emergency_recall = 0.96` passes and exits 0.
- [ ] A candidate with `safety_pass = 1.000` but `emergency_recall = 0.90` is blocked.
- [ ] In shadow mode, the shadow model's prediction is recorded and the response returned to
      the caller is byte-identical to production's.
- [ ] In canary mode, the configured percentage of requests are served by the canary version,
      visible via `clinicops_triage_decisions_total{model_version=...}`.
- [ ] `time python scripts/rollback_model.py` completes in **under 60 seconds**, the previous
      version is `production`, and the rolled-back one is `rolled_back`.
- [ ] `python -m src.ml.drift` reports per-label rate change against the baseline, and
      `docs/model-cards/<version>.md` exists with metrics matching the registry row.

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **The 30 eval cases, `evals/run_evals.py`, or the CI safety gate** — step 10. This session
  builds the gate that *consumes* `safety_pass`; it does not compute it. Do not author eval
  cases to make the gate testable — use fixed test values instead.
- **JSON logging, `request_id` correlation, Filebeat, Elasticsearch, the RCA agent reading
  logs** — step 11.
- **New alert rules for drift or a failed promotion** — step 07 defined exactly three.
  Wiring drift to an alert is a rule-1 conversation.
- **New Prometheus metrics for confidence distribution or drift** — step 06 defined exactly
  five. `model_version` is already a label on the decision counter; use it.
- **Automatic promotion or automatic rollback triggered by drift** — `promote_model` and
  `rollback_model` are `NEEDS_APPROVAL` in step 07's whitelist. This session provides the
  scripts a human runs; it does not wire them to the executor.
- **Changes to `ci.yml`** — step 05 owns the workflow, and step 10 adds the safety gate to
  it. Do not add a model gate job here.
- **Retraining on production traffic** — blocked on the open decision about storing symptom
  text (step 08). Do not build a training-data exporter.
- **A doctor-facing model review UI** — not in any step.

## Manual verification

```bash
psql "$DATABASE_URL" -f scripts/schema.sql
python -m src.ml.registry --register --algorithm tfidf_logreg --git-sha "$(git rev-parse --short HEAD)"

echo "=== the gate blocks on each threshold independently ==="
python -m src.ml.quality_gate --version cand-safety-0.967;   echo "expect 1: $?"
python -m src.ml.quality_gate --version cand-recall-0.90;     echo "expect 1: $?"
python -m src.ml.quality_gate --version cand-good;            echo "expect 0: $?"

echo "=== a blocked candidate cannot be promoted even if asked directly ==="
python -m src.ml.registry --promote cand-safety-0.967; echo "expect non-zero: $?"
psql "$DATABASE_URL" -t -c "SELECT count(*) AS bad_in_prod FROM model_registry WHERE status='production' AND (safety_pass <> 1.000 OR emergency_recall < 0.950);"

echo "=== rollback, timed ==="
time python scripts/rollback_model.py
psql "$DATABASE_URL" -c "SELECT model_version, status, safety_pass, emergency_recall FROM model_registry ORDER BY created_at DESC LIMIT 4;"
psql "$DATABASE_URL" -t -c "SELECT count(*) AS live FROM model_registry WHERE status='production';"

echo "=== canary split and the rest ==="
curl -s "$SPACE_URL/metrics" | grep 'clinicops_triage_decisions_total.*model_version'
python -m src.ml.drift
ls docs/model-cards/
```

Expected: the gate exits 1 for the low-`safety_pass` and low-`emergency_recall` candidates and
0 for the good one; the direct promote attempt fails and `bad_in_prod = 0` — that query is the
proof of the objective, since it asserts the invariant rather than one code path; rollback
reports under 60 s; `live = 1`; the metric shows two `model_version` series while a canary is
running; a model card exists.
