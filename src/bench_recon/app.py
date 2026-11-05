"""FastAPI application for bench-recon.

Scaffold phase: just the health probe. The store, web UI, the deny-by-default
auth gate and the benchhud projection land in later todos — but the app, the
image and the deploy paths already say ``bench-recon``, so everything after this
has a home.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="bench-recon",
    description=(
        "Self-hosted engagement/target system of record for hardware/automotive/IoT "
        "pentesting. Holds sensitive recon data — LAN-only, never publicly exposed."
    ),
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe.

    This route stays **unauthenticated** when the password gate lands — it's what
    Kubernetes hits, and it must answer before a session can exist. It is the one
    documented exception to "all routes protected", so it must never return
    anything about an engagement or a target.
    """
    return {"status": "ok"}
