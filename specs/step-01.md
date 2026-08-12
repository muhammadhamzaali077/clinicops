# Spec: Step 01 - Triage classifier

## Objective

Every `POST /triage` request is either classified into exactly one of
`self_care` | `see_doctor_today` | `emergency` with a confidence in `[0, 1]`, or rejected
with `400` — there is no third outcome.

## Depends on

- **Step 00** — `src/common/config.py` and `src/common/db.py` work, and `patients`
  holds 20 rows.

## Deliverables

| File | Purpose |
| --- | --- |
| `src/pipeline/build_features.py` | **The only place features are computed.** TF-IDF vectorisation of symptom text plus the age feature. Imported by both training and serving. |
| `src/ml/train.py` | Trains TF-IDF + LogisticRegression from labelled data, writes the artifact, prints per-class metrics. Runnable as `python -m`. |
| `src/ml/predict.py` | Loads the artifact once at import, exposes `predict(symptoms, age) -> (label, confidence)`. |
| `src/api/main.py` | The FastAPI app. Binds `0.0.0.0:7860`. |
| `src/api/schemas.py` | `TriageRequest`, `TriageResponse`, `ErrorResponse` as Pydantic models. |
| `src/api/routes/triage.py` | The `POST /triage` router. |
| `Dockerfile` | Python 3.12, runs as UID 1000, `CMD` serves port 7860, writes nothing outside `/tmp`. |
| `tests/test_build_features.py` | The feature function is deterministic and shape-stable. |
| `tests/test_triage_endpoint.py` | Valid input → 200 with a legal label; each invalid case → 400. |

## Interface contract

**`POST /triage`**

Request:

```json
{ "symptoms": "wheezing and shortness of breath", "age": 34 }
```

Response `200`:

```json
{
  "decision": "see_doctor_today",
  "confidence": 0.71,
  "model_version": "<version>",
  "feature_version": "<version>"
}
```

`decision` ∈ `{self_care, see_doctor_today, emergency}`. `confidence` ∈ `[0, 1]`.

Response `400` — the error envelope from docs/03-data-contract.md, never echoing
input:

```json
{ "code": "invalid_request", "message": "Fixed wording." }
```

`400` is returned when: `symptoms` is missing, empty, or whitespace only; `age` is
missing, non-integer, negative, or above 120; or the body is not JSON.

**`src/pipeline/build_features.py`** — the single feature source (rule 2):

```python
FEATURE_VERSION: str
def build_features(symptoms: str, age: int) -> FeatureVector: ...
def fit_vectorizer(corpus: list[str]) -> TfidfVectorizer: ...
```

Training imports it. Serving imports it. Nothing else computes a feature, ever.

Defaults taken. Override any of them and I will change it:

- ASSUMED: `model_version` is `m<YYYYMMDD>-<git-sha7>`; `feature_version` is `f1`, bumped
  by hand whenever `build_features.py` changes its output shape.
- ASSUMED: `confidence` is the raw `predict_proba` maximum, uncalibrated. Step 02's 0.5
  threshold therefore carries no probabilistic meaning, and the model card (step 09) must
  say so.
- ASSUMED: the artifact is baked into the Docker image at build time — the only option
  needing no storage the approved list lacks. Step 09 revisits it.
- ASSUMED: `/triage` does **not** enforce the paediatric cutoff; it is a raw model
  endpoint. `/intake` and `/ask` enforce it (steps 03 and 02).

BLOCKERS — this step cannot start until you answer:

1. **The training labels do not exist.** The 20 rows carry no triage label, so there is no
   target to fit. A doctor-labelled CSV, labels attached to the eval cases, or something
   else?
2. Is the paediatric assumption above acceptable? As specified, `/triage` will classify a
   3-year-old for anything that calls it directly.

## Acceptance criteria

- [ ] `python -m src.ml.train` exits 0, writes a model artifact, and prints
      per-class precision and recall including `emergency`.
- [ ] `curl -X POST /triage` with valid input returns `200` and a `decision` that is
      one of exactly the three legal labels.
- [ ] Empty `symptoms`, missing `age`, `age: -1`, `age: 200`, and a non-JSON body
      each return `400` with the error envelope and no stack trace.
- [ ] `confidence` is in `[0, 1]` on every `200` response.
- [ ] `grep -rn "TfidfVectorizer" src/` matches only `src/pipeline/build_features.py`
      — no second feature path exists.
- [ ] `docker build` succeeds and the container runs as UID 1000, serving 7860.
- [ ] `pytest tests/` passes.
- [ ] No `400` or `500` response body contains any of the submitted symptom text
      (rule 4).

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **The agent, the four tools, guardrails, or `POST /ask`** — step 02. `/triage` is a
  plain model endpoint this session: no LLM call, no refusal logic, no `kb/` lookup,
  no confidence-based escalation.
- **Prometheus metrics or `GET /metrics`** — step 06. This means `/triage` ships
  without a metric and is therefore incomplete against CLAUDE.md's "metric + log line
  + test" rule until step 06 retrofits it. That gap is deliberate and scheduled.
- **Any write to Postgres** — step 08. `/triage` answers and forgets; `inference_log`
  does not exist yet, so no decision is persisted this session.
- **`POST /intake`, doctor routing, `GET /morning-brief`, `GET /followups`** — step 03.
- **Emergency escalation or notifying either doctor** — step 03. `/triage` may return
  the `emergency` label; it must not try to tell anyone.
- **`model_registry`, the quality gate, shadow/canary serving, drift, model card** —
  step 09. One artifact, one version, loaded directly.
- **Loud Excel validation** — step 08.
- **The 30 eval cases or the eval harness** — step 10. Step 01's own tests are unit
  tests, not the safety gate.
- **JSON logging with `request_id`, Filebeat, Elasticsearch** — step 11.

## Manual verification

```bash
python -m src.ml.train || exit 1
uvicorn src.api.main:app --host 0.0.0.0 --port 7860 &
sleep 3
echo "--- valid input: must match one of exactly three labels ---"
curl -s -X POST localhost:7860/triage -H 'Content-Type: application/json' \
  -d '{"symptoms":"wheezing and shortness of breath","age":34}' \
  | grep -oE '"decision":"(self_care|see_doctor_today|emergency)"'
echo "--- malformed input: expect 400 four times ---"
for body in '{"symptoms":"","age":34}' '{"symptoms":"cough","age":-1}' \
            '{"symptoms":"cough","age":200}' 'not json'; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:7860/triage \
    -H 'Content-Type: application/json' -d "$body"
done
echo "--- features exist in exactly one place (rule 2) ---"
grep -rln "TfidfVectorizer" src/
```

Expected: exactly one matched `"decision"` line proving the label is legal; `400` printed
four times with no other status code; and the grep printing exactly
`src/pipeline/build_features.py` and nothing else.
