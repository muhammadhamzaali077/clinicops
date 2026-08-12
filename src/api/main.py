"""ClinicOps API.

Only `GET /health` exists at this point. It is deliberately the whole surface: enough
to prove the deployment pipe works before any real code is written. Real endpoints
arrive with specs/step-01.md onward.

Runs as a Vercel Python serverless function. Every request may be served by a fresh
instance with no memory of previous requests, so nothing here may hold state between
calls: no module-level caches, no counters, no accumulating lists, and no writes to
disk outside /tmp. All durable state goes to Postgres.

Routes are declared at their real paths (`/health`, not `/api/index/health`). The
Vercel rewrite prefix is stripped in `api/index.py` before requests reach this app.
"""

from fastapi import FastAPI

app = FastAPI(title="ClinicOps API")


@app.get("/health")
def health() -> dict[str, str | int]:
    """Report that the function is up and serving.

    Takes nothing and returns a fixed body. It deliberately checks nothing else — no
    database, no model, no agent — so a failure here means the function itself is not
    serving, and nothing more ambiguous than that.
    """
    return {"status": "ok", "v": 2}
