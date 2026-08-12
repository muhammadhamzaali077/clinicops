# 01 — Product Requirements

## Problem

A two-doctor clinic keeps its entire patient record in one Excel file
(`data/raw/patients.xlsx`, 20 patients, 11 columns). Two things are slow and
error-prone as a result:

1. **History lookup.** When a patient walks in, the receptionist opens a
   spreadsheet, scrolls, and reads across eleven columns to find allergies,
   chronic conditions, current medications, and which doctor owns the patient.
   Nothing prevents reading the wrong row, and nothing surfaces "this patient is
   allergic to penicillin" at the moment it matters.

2. **Triage.** There is no consistent way to decide who is seen now, who is seen
   today, and who can be sent home with first-aid guidance. The decision depends
   on who is at the front desk. Emergencies depend on a receptionist recognising
   an emergency.

ClinicOps replaces both with a platform: instant history lookup keyed on patient
ID, and a triage decision (`self_care` | `see_doctor_today` | `emergency`)
derived from symptoms and age, paired with pre-approved first-aid guidance and an
audit trail of every decision made.

The clinical judgement stays with Dr. Ali and Dr. Sara. ClinicOps routes,
prioritises, records, and escalates. It does not practise medicine.

## The three users

### 1. Receptionist — the primary user

Sits at the front desk. Not clinically trained. Under time pressure with a queue.

- Looks up a walk-in's history by patient ID before the doctor sees them.
- Enters the symptoms the patient describes, plus their age, into an intake form.
- Receives a triage decision, the approved first-aid guidance to read out, and —
  when the decision is `emergency` — an unambiguous instruction to act on.
- Needs the answer to be fast enough to use while the patient is still standing
  there, and plain enough to read aloud without interpretation.

DECISION NEEDED: Can the receptionist look a patient up by name, or by patient ID
only? Name lookup is the natural front-desk workflow (a walk-in knows their name,
not "P014"), but it means accepting a name as a query parameter and matching on
it. Rule 4 forbids logging names; it does not say whether names may be accepted
as input or stored. Which is it?

### 2. Doctor (Dr. Ali, Dr. Sara) — the clinical authority

Two doctors, each owning a fixed subset of the 20 patients (`doctor` column;
10 patients each in the current file).

- Receives the triage queue for their own patients, ordered so that the most
  urgent is first.
- Is notified — both doctors, always — whenever a case is labelled `emergency`.
- Overrides any ClinicOps decision. The platform records the override; it never
  argues with it.
- Owns the content of the approved first-aid knowledge base (`kb/`). ClinicOps
  only serves what a doctor has approved.

DECISION NEEDED: Do doctors interact with ClinicOps through an interface of its
own, or only through notifications plus the n8n workflow layer? Step 03 covers
"doctor routing" and an "n8n walk-in flow" but names no doctor-facing UI.

DECISION NEEDED: Who signs off the `kb/` first-aid content clinically, and is
that sign-off recorded in the repo (e.g. a reviewer name and date per entry) or
managed outside it?

### 3. Platform engineer — the operator

Runs the platform. Not present in the clinic.

- Owns the three tiers: the Space, Postgres, and the local observability stack.
- Watches the Prometheus metrics, the Grafana panels, and Alertmanager.
- Responds to incidents, with a whitelisted action set (rule 7) and an AI triage
  and RCA assistant.
- Ships changes through GitHub Actions with a change-risk score, a quality gate,
  and auto-rollback.
- Retrains, evaluates, shadows, canaries, and rolls back the triage model.

## In scope

- Patient history lookup by ID, served from Postgres.
- Triage classification: symptoms + age → `self_care` | `see_doctor_today` |
  `emergency`, trained with scikit-learn, features from
  `src/pipeline/build_features.py` and nowhere else.
- An agent (OpenAI Agents SDK) with exactly four tools — `get_patient`,
  `get_history`, `triage`, `first_aid` — behind guardrails, exposed as `POST /ask`.
- Approved first-aid guidance retrieved from `kb/`, never generated freely.
- Intake workflow (`POST /intake`), routing to the owning doctor, and an n8n
  walk-in flow.
- Emergency escalation to **both** doctors.
- Every decision written to `inference_log` in Postgres, IDs and decisions only.
- The full platform layer: IaC, CI/CD, observability, incident response, MLOps,
  evaluation, and structured logging, per the 12 build steps in CLAUDE.md.

## NON-SCOPE — explicit

ClinicOps does not do any of the following. These are not deferred features;
they are permanent exclusions, and the agent must refuse rather than attempt them.

| Excluded | Why | Required behaviour |
| --- | --- | --- |
| **Diagnosis** | Naming a condition is practising medicine. Rule 5. | Refuse. Return the triage label and route to a doctor. Never state or imply a condition. |
| **Prescriptions** | Prescribing requires a licensed prescriber. Rule 5. | Refuse. Never suggest starting, stopping, or changing a medication. |
| **Dosages** | A wrong dose harms. Rule 5. | Refuse. Never state an amount, frequency, or duration for any medication — including over-the-counter ones, and including a medication already in the patient's `current_medications`. |
| **Drug interactions** | Interaction checking is a regulated clinical decision-support function. | Refuse. `get_patient` may return the stored `allergies` and `current_medications` fields as recorded data; the agent must not reason about how they interact. |
| **Children under 5** | Paediatric triage differs materially from adult triage, and the model is not trained for it. | Refuse to triage. Route directly to a doctor. |
| **Emergency dispatch** | ClinicOps cannot call an ambulance, and must never let anyone believe it has. | Refuse. Instruct the human to call emergency services themselves, and notify both doctors. |

Consequences of the under-5 exclusion, given the actual data:

DECISION NEEDED: The under-5 rule excludes nobody in the current file — the
youngest patients are aged 7 (P020) and 8 (P005) — yet both are flagged
paediatric in their notes ("Paediatric - under 8", "Paediatric dosing - doctor
only"), and P017 is 12. Is the triage cutoff genuinely 5, or should ClinicOps
refuse to triage everyone under some higher paediatric age (8? 12? 16?) and route
them to a doctor? The file's own notes imply the clinic already treats under-8 as
doctor-only.

DECISION NEEDED: When a patient is under the cutoff, does `POST /intake` refuse
the triage but still record the encounter and notify the owning doctor, or refuse
the request outright? A silent refusal loses the walk-in.

DECISION NEEDED: Are these exclusions also enforced at the model layer, or only
at the agent and API layer? A model that emits `self_care` for a 3-year-old is a
latent hazard even if the API blocks it.

## Success criteria

Every criterion below must be measurable from the Prometheus metrics
(docs/05-ops-spec.md) or the eval harness (docs/06-eval-spec.md). A criterion
nobody can measure is not a criterion.

| # | Criterion | Target | Measured by |
| --- | --- | --- | --- |
| SC-1 | History lookup latency | under 5 s | Prometheus histogram on the lookup endpoint |
| SC-2 | Triage latency, p95 | under 2 s | Prometheus histogram on the triage path |
| SC-3 | Emergency escalation coverage | 100% of `emergency` decisions escalated to **both** doctors | Count of escalations vs count of `emergency` decisions in `inference_log` |
| SC-4 | `emergency_recall` | ≥ 0.95 | Eval harness |
| SC-5 | Guardrail violations on the evals | exactly zero | Eval harness, `safety_pass` = 1.00 |

Notes and open questions on each:

- **SC-1 / SC-2.** DECISION NEEDED: Is latency measured server-side (time inside
  the Space, which is what Prometheus can see) or end-to-end from the
  receptionist's browser? A public Hugging Face Space can cold-start in tens of
  seconds; server-side timing hides that entirely. Do cold starts count against
  SC-1 and SC-2?
- **SC-2.** DECISION NEEDED: Does the p95 window cover the whole triage path
  including the agent's LLM calls (up to 6 tool calls, 15 s hard timeout per
  docs/04-agent-spec.md), or only the scikit-learn inference call? These differ by
  an order of magnitude, and a 15 s agent timeout cannot satisfy a 2 s p95 —
  those two numbers are in direct tension and one of them has to give.
- **SC-3.** "Escalated" needs a definition. DECISION NEEDED: Does escalation count
  as successful when the notification is *dispatched*, or when it is *delivered*
  (or acknowledged) by each doctor? Dispatch is measurable in the Space; delivery
  is not, without a receipt from the notification channel.
- **SC-4.** DECISION NEEDED: `emergency_recall` is measured on which dataset — the
  30 eval cases in docs/06-eval-spec.md, a held-out split of a labelled training
  set, or both? The 20 rows in `patients.xlsx` carry no triage labels at all, so
  the label source for training and for measuring recall is not yet decided.
- **SC-5.** Defined as zero violations across all 30 eval cases, with
  `safety_pass` exactly 1.00 (docs/06-eval-spec.md).

DECISION NEEDED: What is the availability target for the Space, if any? None of
the five criteria above mentions uptime, but docs/05-ops-spec.md needs an
availability SLO and Alertmanager needs a threshold to page on.

DECISION NEEDED: What is the acceptable rate of over-triage — cases sent to a
doctor that a doctor judges could have been `self_care`? `emergency_recall ≥ 0.95`
constrains misses in one direction only; with no precision floor, a model that
labels everything `emergency` scores a perfect recall and makes the clinic
unworkable.

## Non-functional requirements

- The compute tier is stateless (rule 6). No durable state on the Space; `/tmp`
  only; all persistence via `src/common/db.py` to Postgres.
- No secrets in code (rule 3). `DATABASE_URL`, `OPENAI_API_KEY`, `ENVIRONMENT`
  from the environment.
- No patient names, notes, or raw symptom text in any log line (rule 4).
- One tool per job (rule 1). The approved list in CLAUDE.md is closed.
- Every endpoint ships with a metric, a log line, and a test.

DECISION NEEDED: The Space is public and the data is patient data. What
authentication and authorisation sit in front of these endpoints, and who issues
the credentials? Without an answer, anyone on the internet can read patient
history from `GET`-able endpoints. This blocks any real deployment and needs to be
settled before step 00 puts real records into Postgres.

DECISION NEEDED: Which data-protection regime governs this data (HIPAA, GDPR,
Pakistan's PDPA, clinic-internal policy only)? It determines retention limits,
audit requirements, and whether patient names may leave the clinic's premises at
all — including into a managed Postgres and an LLM provider's API.

DECISION NEEDED: Patient-supplied symptom text is sent to the OpenAI API. Is that
acceptable to the clinic, and is there a data-processing agreement covering it?
