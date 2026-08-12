"""ClinicOps API.

Only `GET /health` exists at this point. It is deliberately the whole surface: enough
to prove the Space builds, starts, and answers before any real code is written. Real
endpoints arrive with specs/step-01.md onward.
"""

from fastapi import FastAPI

app = FastAPI(title="ClinicOps API")


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is up and serving.

    Takes nothing and returns a fixed body. It deliberately checks nothing else — no
    database, no model, no agent — so a failure here means the container itself is not
    serving, and nothing more ambiguous than that.
    """
    return {"status": "ok"}
