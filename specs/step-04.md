# Spec: Step 04 - IaC

## Objective

`scripts/review_plan.py` exits non-zero with `BLOCK` for every Terraform plan that would
destroy a stateful resource in the local ops stack, and exits zero with `APPROVE` for every
plan that would not.

(Terraform and Ansible are also built this session — see Deliverables and Acceptance
criteria — but the reviewer's verdict is the one outcome that defines "done".)

## Depends on

- **Step 00** — `config.py` reads the environment (Terraform and Ansible consume the same
  variables).
- **Step 03** — `infra/n8n/walk-in-flow.json` exists, so the n8n container has a workflow
  to import.

## Deliverables

| File | Purpose |
| --- | --- |
| `infra/main.tf` | Docker provider; containers, network, and volumes for Prometheus, Grafana, Alertmanager, Elasticsearch, Kibana, n8n. |
| `infra/variables.tf` | Image tags, ports, host paths. No secrets — values come from the environment. |
| `infra/outputs.tf` | The local URL of each service. |
| `infra/ansible/site.yml` | Host prep: directories, permissions, prerequisite checks, `docker compose` availability. |
| `infra/ansible/inventory.ini` | Localhost inventory. |
| `scripts/review_plan.py` | Reads `plan.json`, asks the reviewer agent, prints a verdict, exits non-zero on `BLOCK`. |
| `tests/test_review_plan.py` | A synthetic destructive plan yields `BLOCK`; an additive plan yields `APPROVE`. |
| `.gitignore` | Already ignores `tfplan.bin` and `plan.json` — confirm, do not re-add. |

## Interface contract

**Terraform** — resources for the six Tier 3 services, one Docker network, and named
volumes for Prometheus and Elasticsearch data. Outputs:

```
prometheus_url    = "http://localhost:9090"
grafana_url       = "http://localhost:3000"
alertmanager_url  = "http://localhost:9093"
elasticsearch_url = "http://localhost:9200"
kibana_url        = "http://localhost:5601"
n8n_url           = "http://localhost:5678"
```

**Plan reviewer** — `scripts/review_plan.py plan.json` prints:

```json
{
  "verdict": "BLOCK",
  "reasons": [
    "docker_volume.prometheus_data would be destroyed; it holds metric history."
  ],
  "destructive_changes": 1,
  "additive_changes": 3
}
```

`verdict` ∈ `{APPROVE, BLOCK}`. Exit code `0` on `APPROVE`, `1` on `BLOCK`.

The reviewer blocks on: any `delete` action against a volume; any resource replacement
that loses data; any change removing a published port the clinic depends on. It must
never see or emit a secret — the plan is scanned for credential-shaped values before it
reaches the agent.

**Ansible** — `site.yml` is idempotent: a second run reports zero changed tasks.

Defaults taken. Override any of them and I will change it:

- ASSUMED: Terraform's Docker provider manages the containers directly; no compose file is
  generated or invoked. Rendering compose from Terraform would be two tools for one job
  (rule 1). CLAUDE.md's phrase "via docker compose" is read as describing where the stack
  runs, not mandating the tool.
- ASSUMED: Terraform state is a local file in `infra/`, gitignored. It is the only copy;
  losing it means re-importing, not losing data.
- ASSUMED: Terraform creates the containers, networks, and volumes; Ansible does host prep
  only — directories, permissions, and prerequisite checks. No resource is managed by both.
- ASSUMED: the reviewer fails **closed** — an unavailable API is a `BLOCK`, not a pass.
- ASSUMED: Postgres is created out-of-band, not by this Terraform. "The local ops stack"
  means the six Tier 3 containers.
- ASSUMED: the reviewer uses the same pinned OpenAI model chosen in step 02.

## Acceptance criteria

- [ ] `terraform init && terraform validate` exits 0 with no warnings.
- [ ] `terraform apply` brings all six containers to running, and each output URL
      responds.
- [ ] `terraform apply` a second time reports `0 to add, 0 to change, 0 to destroy`.
- [ ] `ansible-playbook -i infra/ansible/inventory.ini infra/ansible/site.yml` run twice
      reports zero changed tasks on the second run.
- [ ] `scripts/review_plan.py` on a plan that destroys `docker_volume.prometheus_data`
      prints `BLOCK`, names the volume in `reasons`, and exits `1`.
- [ ] `scripts/review_plan.py` on a purely additive plan prints `APPROVE` and exits `0`.
- [ ] `grep -rn -E "(sk-|postgres://|password)" infra/` finds nothing — no secret in any
      `.tf`, `.yml`, or state-adjacent file (rule 3).
- [ ] `tfplan.bin` and `plan.json` remain untracked by git.

## Out of scope

Nothing from a later step. Specifically **do not build**:

- **`prometheus.yml` scrape configuration, Grafana dashboard JSON, or the HTTPS scrape of
  the public Space** — step 06. This session creates the *containers*; step 06 supplies
  their *configuration*. Terraform mounts a config path; the file it points at may be
  empty or a stub this session.
- **Alert rules, `alerts.yml`, Alertmanager routing to n8n** — step 07. Alertmanager runs
  with a default config and routes nothing.
- **Filebeat, the Elasticsearch index template, Kibana saved searches** — step 11.
  Elasticsearch and Kibana run empty.
- **The five Prometheus metrics or `GET /metrics` on the Space** — step 06. There is
  nothing to scrape yet, which is expected.
- **The whitelisted action executor, rate limiting, `alert_history`** — step 07. The plan
  reviewer is an advisory agent, not an action executor, and is not subject to the
  whitelist.
- **Changes to `.github/workflows/`** — step 05. The reviewer runs from the CLI this
  session, not in CI.
- **Terraform for the Hugging Face Space itself** — not in scope for this step; the Space
  is deployed by the CI workflow in step 05.
- **`model_registry` infrastructure or drift jobs** — step 09.

## Manual verification

```bash
cd infra
terraform init && terraform validate && terraform apply -auto-approve
docker ps --format '{{.Names}}\t{{.Status}}'
ansible-playbook -i ansible/inventory.ini ansible/site.yml

echo "=== APPROVE path: a no-op plan ==="
terraform plan -out=tfplan.bin && terraform show -json tfplan.bin > plan.json
python ../scripts/review_plan.py plan.json; echo "expect exit 0: $?"

echo "=== BLOCK path: a plan that destroys the metrics volume ==="
terraform plan -destroy -out=tfplan.destroy.bin \
  && terraform show -json tfplan.destroy.bin > plan.destroy.json
python ../scripts/review_plan.py plan.destroy.json; echo "expect exit 1: $?"
```

Expected: apply succeeds and `docker ps` lists six running containers; the playbook reports
zero changed tasks; the no-op plan prints `APPROVE` and exits `0`; and the destroy plan
prints `BLOCK`, names `docker_volume.prometheus_data` in `reasons`, and exits `1`. Both
branches must be shown — an `APPROVE` alone does not prove the objective.
