# Spec: Step 05 - CI/CD

## Objective

A failing CI test job is retried exactly once when the failure analyser classifies it
`FLAKY`, and is never retried when it classifies it `REAL`.

(The change-risk score and the smoke-test rollback are also built this session — see
Deliverables and Acceptance criteria — but the retry gate is the one outcome that defines
"done".)

## Depends on

- **Step 00–03** — there is an application with tests to run and endpoints to smoke-test.
- **Step 04** — the plan reviewer exists and can be invoked from CI.

Note: no earlier step creates `.github/workflows/ci.yml`, so this session writes the
baseline workflow *and* the four upgrades. Flagging that the objective says "upgrade" but
there is nothing yet to upgrade.

## Deliverables

| File | Purpose |
| --- | --- |
| `.github/workflows/ci.yml` | Lint → test → change-risk → build → deploy → smoke → rollback-on-failure. |
| `scripts/change_risk.py` | Scores a diff 0–100 and posts the score with its reasoning as a PR comment. |
| `scripts/analyze_failure.py` | Reads a failed test log, classifies `FLAKY` or `REAL`, emits a machine-readable verdict. |
| `scripts/smoke_test.sh` | Post-deploy checks against the live Space: `/health`, `/triage`, `/intake`. |
| `scripts/rollback.sh` | Redeploys the previous known-good image tag. Supports `--dry-run`. |
| `tests/test_change_risk.py` | Known diffs produce scores in the expected bands. |
| `tests/test_analyze_failure.py` | A timeout log classifies `FLAKY`; an assertion log classifies `REAL`. |

## Interface contract

**`scripts/change_risk.py --base <ref> --head <ref>`**

```json
{
  "score": 72,
  "band": "HIGH",
  "reasons": [
    "src/pipeline/build_features.py changed — features are the single source (rule 2)",
    "src/agents/guardrails.py changed — safety-critical"
  ],
  "files_changed": 4
}
```

`band`: `LOW` 0–33, `MEDIUM` 34–66, `HIGH` 67–100. Paths that raise the score:
`src/pipeline/build_features.py`, `src/agents/guardrails.py`, `src/common/db.py`,
`scripts/schema.sql`, `kb/`.

**`scripts/analyze_failure.py --log <path>`**

```json
{
  "verdict": "FLAKY",
  "confidence": 0.8,
  "evidence": "Connection reset during DB fixture setup; no assertion failed.",
  "retry_recommended": true
}
```

`verdict` ∈ `{FLAKY, REAL}`. Retry happens **only** when `verdict == "FLAKY"`, and at
most once. A `REAL` verdict fails the job immediately with no retry.

**`scripts/smoke_test.sh $SPACE_URL`** — exits 0 only if `/health` returns 200,
`/triage` returns a legal decision for a valid body, and `/triage` returns 400 for an
invalid one. Any failure exits non-zero and the workflow invokes `rollback.sh`.

**`scripts/rollback.sh [--dry-run] <previous_tag>`** — redeploys the previous image tag
and re-runs the smoke test. Prints what it would do and changes nothing under
`--dry-run`.

Defaults taken. Override any of them and I will change it:

- ASSUMED: a `HIGH` change-risk score comments only and does not block the merge. Blocking
  needs a branch-protection rule, which is a repo setting, not code.
- ASSUMED: the failure analyser fails **closed** — an unavailable API is treated as `REAL`,
  so nothing is retried on a guess.
- ASSUMED: the analyser uses the same pinned OpenAI model chosen in step 02.
- ASSUMED: CI runs against a Postgres service container, not a fixture, because step 00's
  tests need a real database.
- ASSUMED: all test fixtures use synthetic patients, so no real name or symptom text can
  reach the analyser's API call (rule 4). This is enforced by a test.

BLOCKERS — this step cannot start until you answer:

1. **How the Space is deployed from CI** — `git push` to the HF Space remote, or the HF API
   with a token? This decides what `rollback.sh` can actually do and which secret CI needs.
2. **Where "the previous known-good image tag" comes from.** Nothing records it; step 09's
   `model_registry` tracks models, not deployments. Without an answer, rollback has no
   target.

## Acceptance criteria

- [ ] `.github/workflows/ci.yml` runs on pull request and on push to the default branch,
      and `pytest` failures fail the job.
- [ ] `python scripts/change_risk.py --base main --head HEAD` prints a score, a band, and
      at least one reason naming a changed file.
- [ ] A diff touching `src/agents/guardrails.py` scores `HIGH`; a docs-only diff scores
      `LOW`.
- [ ] `python scripts/analyze_failure.py --log <timeout log>` returns `FLAKY`, and the
      workflow retries the test job exactly once.
- [ ] `python scripts/analyze_failure.py --log <assertion log>` returns `REAL`, and the
      workflow does **not** retry.
- [ ] `bash scripts/smoke_test.sh $SPACE_URL` exits 0 against a healthy Space and
      non-zero when `/triage` returns the wrong shape.
- [ ] A forced smoke-test failure causes the workflow to invoke `scripts/rollback.sh`,
      visible in the job log.
- [ ] `bash scripts/rollback.sh --dry-run <tag>` prints its plan, changes nothing, and
      exits 0. No secret appears in any workflow file (rule 3).

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **The model quality gate on `emergency_recall` and `safety_pass`** — step 09. The CI
  gate this session is tests + smoke test only. It does not know what a good model is.
- **The eval harness or failing CI when `safety_pass < 1.00`** — step 10. That gate is
  added to this same workflow later; do not stub it now.
- **Model rollback** — step 09. `rollback.sh` here rolls back a *deployment*; rolling back
  a *model version* is a separate script against `model_registry`.
- **Prometheus metrics, `/metrics`, or CI checks that read metrics** — step 06.
- **Alert rules, Alertmanager, or paging on a failed deploy** — step 07. A failed deploy
  fails the job and rolls back; it does not raise an alert.
- **`alert_history` or the audit trail** — step 07. CI actions are not whitelisted ops
  actions and are not subject to the 2-per-hour rate limit.
- **Drift detection or shadow/canary deployment** — step 09. Deploy is all-or-nothing this
  session.
- **JSON logging, Filebeat, or shipping CI logs to Elasticsearch** — step 11.
- **Terraform or Ansible invocation from CI** — step 04 built them for local use; wiring
  them into the pipeline is not in any step and needs asking first.

## Manual verification

```bash
echo "=== the retry gate is wired to the verdict, not to failure alone ==="
grep -n -A2 "analyze_failure" .github/workflows/ci.yml | grep -E "if:|FLAKY"

echo "=== classifier: both verdicts ==="
python scripts/analyze_failure.py --log tests/fixtures/flaky_timeout.log
python scripts/analyze_failure.py --log tests/fixtures/real_assertion.log

echo "=== end to end in CI: one branch per verdict ==="
gh workflow run ci.yml --ref audit/flaky-seed  # a seeded transient failure
gh workflow run ci.yml --ref audit/real-seed   # a seeded assertion failure
gh run list --limit 2
# then, for each: gh run view <id> --log | grep -c "Retrying test job"

echo "=== the rest of the session ==="
python scripts/change_risk.py --base main --head HEAD
bash scripts/smoke_test.sh "$SPACE_URL"; echo "smoke exit: $?"
bash scripts/rollback.sh --dry-run v-previous; echo "rollback exit: $?"
```

Expected: the workflow's retry step is guarded by an `if:` referencing the `FLAKY` verdict;
the classifier prints `FLAKY` then `REAL`; the `flaky-seed` run shows `Retrying test job`
exactly once and the `real-seed` run shows it zero times — that pair is the proof of the
objective. Then a score with a band and reasons, smoke exiting 0, and the dry-run printing
its plan and exiting 0 having changed nothing.
