"""FastAPI application for bench-recon.

The web UI for the engagement/target system of record: create and list
engagements, record targets, keep an append-only findings log, and store
artifacts (firmware dumps, captures) on disk beside the database.

**No auth gate yet.** The deny-by-default session/bearer middleware lands in a
later todo; until then every route here is open, exactly as the scaffold phase
left ``/healthz``. The benchhud projection and its read-only ``/api`` land later
too — nothing here exposes an engagement to a machine. What this module does
guarantee today is the list view's discipline: the engagements table shows only
the four ref-safe fields, never a client or a target identity.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bench_recon import auth
from bench_recon.artifacts import ArtifactError, ArtifactStore
from bench_recon.auth import verify_password
from bench_recon.db import connect
from bench_recon.projection import project_artifact, project_engagement
from bench_recon.ratelimit import RateLimiter
from bench_recon.schema import initialise
from bench_recon.status import Severity, Status
from bench_recon.store import Engagement, EngagementStore

app = FastAPI(
    title="bench-recon",
    description=(
        "Self-hosted engagement/target system of record for hardware/automotive/IoT "
        "pentesting. Holds sensitive recon data — LAN-only, never publicly exposed."
    ),
)

#: The only paths reachable without a session.
#:
#: **Deny by default.** Protection is applied to everything and lifted here,
#: rather than added per-route — the inverse arrangement means a route added in a
#: later todo ships unprotected the moment somebody forgets a decorator, and a
#: silent leak of sensitive engagement data is a much worse failure than a route
#: that's visibly unreachable.
#:
#: ``/healthz`` is the Kubernetes probe and must answer before a session can
#: exist; ``/login`` obviously cannot require being logged in. Nothing else
#: belongs here, and ``tests/test_auth_required.py`` enumerates the routing table
#: to keep that honest.
EXEMPT_PATHS = frozenset({"/healthz", "/login"})

#: Methods that change state and therefore need a CSRF token.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Failed-login throttle. In-memory and per-process, which is fine while the
#: Deployment stays at ``replicas: 1`` — see bench_recon/ratelimit.py.
login_limiter = RateLimiter()


def _client_ip(request: Request) -> str | None:
    """Best-effort client address.

    Deliberately ignores ``X-Forwarded-For``: nothing terminates in front of this
    service today, so honouring a client-supplied header would let anyone reset
    their own throttle bucket by changing it. Revisit if a proxy ever lands.
    """
    return request.client.host if request.client else None


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Demand a session for everything outside ``EXEMPT_PATHS``, and a valid CSRF
    token on every state-changing request.

    Both checks are deny-by-default for the same reason: a form added in a later
    todo should be safe without anyone remembering to opt it in. ``samesite=lax``
    on the session cookie already blocks cross-site POSTs in current browsers;
    this is the server-side half, because one mechanism enforced by the client
    isn't where you want the only defence for sensitive records.
    """
    path = request.url.path

    # The machine-facing API is gated too — by a bearer token or a session, since
    # benchhud can't do the browser login. On failure it gets a 401 status code,
    # not a 302 to an HTML page a service can't use. Still deny-by-default: a
    # second gate, never an exemption. (The /api routes themselves land in the
    # projection todo; this branch is ready for them.)
    if path.startswith("/api/"):
        if auth.valid_api_token(request.headers.get("authorization")) or request.session.get(
            "authenticated"
        ):
            return await call_next(request)
        return JSONResponse({"detail": "unauthorized"}, status_code=401)

    if path not in EXEMPT_PATHS and not request.session.get("authenticated"):
        target = quote(path, safe="/")
        return RedirectResponse(url=f"/login?next={target}", status_code=303)

    if request.method in _MUTATING_METHODS:
        # Reading the form here consumes the request stream, so the body has to be
        # replayed or the endpoint downstream sees an empty form and every
        # submission looks blank. Found the hard way in intake: without this,
        # login failed as "wrong password" with a perfectly correct password.
        body = await request.body()

        async def _replay() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _replay

        form = await request.form()
        if not auth.csrf_valid(request.session, form.get("csrf_token")):
            return PlainTextResponse(
                "Request could not be verified. Reload the page and try again.",
                status_code=403,
            )

    return await call_next(request)


# Added last, so it wraps the auth middleware — the session has to be decoded
# before anything can check it.
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.session_secret(),
    session_cookie="session",
    https_only=auth.cookie_secure(),
    same_site="lax",
)

# Resolved relative to this module, not the working directory — the app runs from
# a container image where cwd is not the repo root.
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _csrf_context(request: Request) -> dict[str, object]:
    """Make ``csrf_token`` available to every template.

    A context processor rather than a per-route context entry, for the same
    deny-by-default reason as the auth middleware: a template added later gets the
    token without anyone remembering to pass it, and a form that omits the hidden
    field fails visibly at the first POST instead of silently dropping protection.
    """
    return {
        "csrf_token": auth.csrf_token(request.session),
        # Distinct from the token: a token exists *before* sign-in, because the
        # login form itself needs one. Keying UI on the token would show a "Sign
        # out" button to someone who has never signed in.
        "authenticated": bool(request.session.get("authenticated")),
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_csrf_context])

#: Engagement fields the create form posts, used to echo a rejected attempt back
#: so nothing typed is lost.
_NEW_FIELDS = ("safe_title", "target_class", "client_ref", "scope", "authorization")


def get_db_path() -> Path:
    """Where the database lives.

    On the cluster this points into the NFS PVC; locally it defaults to a file
    beside the repo, which ``.gitignore`` already covers.
    """
    return Path(os.environ.get("BENCHRECON_DB_PATH", "bench-recon.db"))


def get_store() -> Iterator[EngagementStore]:
    """A store for one request.

    A fresh connection per request rather than a shared one: FastAPI runs sync
    endpoints in a threadpool, and SQLite connections aren't safe to share across
    threads. ``initialise()`` short-circuits on an up-to-date ``user_version``, so
    the database is always ready regardless of how the app was started.
    """
    with connect(get_db_path()) as conn:
        initialise(conn)
        yield EngagementStore(conn)


StoreDep = Annotated[EngagementStore, Depends(get_store)]


def get_artifacts_path() -> Path:
    """Where artifact blobs live — beside the database on the NFS PVC in
    production, a local directory otherwise (``.gitignore`` covers it)."""
    return Path(os.environ.get("BENCHRECON_ARTIFACTS_PATH", "artifacts"))


def get_artifact_store() -> ArtifactStore:
    return ArtifactStore(get_artifacts_path())


ArtifactStoreDep = Annotated[ArtifactStore, Depends(get_artifact_store)]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe.

    Stays **unauthenticated** when the password gate lands — it's what Kubernetes
    hits, and it must answer before a session exists. The one documented
    exception to "all routes protected", so it must never return anything about
    an engagement or a target.
    """
    return {"status": "ok"}


@app.get("/")
def index() -> RedirectResponse:
    """Straight to the engagement list — the app's home."""
    return RedirectResponse(url="/engagements", status_code=303)


def _safe_next(target: str) -> str:
    """Reduce ``?next=`` to a path on this host, or fall back to the list.

    An unvalidated ``next`` turns a genuine login into a bounce to somebody else's
    page — the classic phishing shape. Only a single-slash absolute path is
    allowed: ``//host`` and ``/\\host`` are both browser-recognised ways of naming
    another origin, and a scheme (``https:``, ``javascript:``) obviously leaves.
    """
    target = (target or "").strip()
    if (
        not target.startswith("/")
        or target.startswith("//")
        or target.startswith("/\\")
        or ":" in target.split("/")[0]
    ):
        return "/engagements"
    return target


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "") -> HTMLResponse:
    """The sign-in page. Reachable without a session, by definition."""
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": _safe_next(next), "error": None},
    )


@app.post("/login")
def login(
    request: Request,
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    """Check the shared password and start a session.

    A failure says only that the password is wrong — never whether one is
    configured, nor anything about its shape. There's one password here; every
    extra detail narrows a guess.
    """
    client = _client_ip(request)

    if not login_limiter.allowed(client):
        # Checked before the password so a locked-out client can't keep probing,
        # and so lockout costs no scrypt work — otherwise the throttle becomes its
        # own CPU amplifier on a Pi.
        wait = login_limiter.retry_after(client)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next": _safe_next(next),
                # Ceiling division: a 300s wait is "5 minutes", not 6.
                "error": f"Too many attempts. Try again in {-(-wait // 60)} minute(s).",
            },
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    if not verify_password(password, auth.configured_hash()):
        login_limiter.record_failure(client)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": _safe_next(next), "error": "That password is not right."},
            status_code=401,
        )

    login_limiter.record_success(client)
    request.session["authenticated"] = True
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/logout")
def logout(request: Request) -> Response:
    """End the session."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/engagements", response_class=HTMLResponse)
def engagement_list(request: Request, store: StoreDep, status: str | None = None) -> HTMLResponse:
    """The engagement queue.

    Open engagements by default, built from the derived ``OPEN_STATUSES`` so a
    newly added status can't quietly disappear. Only ref-safe fields render here.
    """
    try:
        engagements = (
            store.list_engagements(status=status)
            if status
            else store.list_engagements(open_only=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    return templates.TemplateResponse(
        request=request,
        name="engagements.html",
        context={
            "engagements": engagements,
            "status": status,
            "all_statuses": [s.value for s in Status],
        },
    )


@app.get("/engagements/new", response_class=HTMLResponse)
def new_engagement_form(request: Request) -> HTMLResponse:
    """The create form."""
    return _render_new(request)


@app.post("/engagements")
def create_engagement(
    request: Request,
    store: StoreDep,
    safe_title: Annotated[str, Form()] = "",
    target_class: Annotated[str, Form()] = "",
    client_ref: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "",
    authorization: Annotated[str, Form()] = "",
) -> Response:
    """Open a new engagement from the submitted form.

    Redirects on success so a refresh doesn't re-create. On a validation error
    the form comes back with everything still in it.
    """
    submitted = {
        "safe_title": safe_title,
        "target_class": target_class,
        "client_ref": client_ref,
        "scope": scope,
        "authorization": authorization,
    }
    try:
        engagement = store.create_engagement(**submitted)
    except ValueError as exc:
        return _render_new(request, values=submitted, error=str(exc), status_code=400)
    return RedirectResponse(url=f"/engagements/{engagement.ref}", status_code=303)


@app.get("/engagements/{ref}", response_class=HTMLResponse)
def engagement_detail(
    request: Request, ref: str, store: StoreDep, artifact_store: ArtifactStoreDep
) -> HTMLResponse:
    """Everything about one engagement: the authorization banner, its sensitive
    fields, targets, the findings log, and artifacts."""
    engagement = _require_engagement(store, ref)
    return _render_detail(request, store, engagement)


@app.post("/engagements/{ref}")
def edit_engagement(
    request: Request,
    ref: str,
    store: StoreDep,
    safe_title: Annotated[str, Form()] = "",
    target_class: Annotated[str, Form()] = "",
    client_ref: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "",
    authorization: Annotated[str, Form()] = "",
) -> Response:
    """Edit an engagement's fields (status moves through its own guarded path)."""
    engagement = _require_engagement(store, ref)
    try:
        store.update_engagement(
            ref,
            safe_title=safe_title,
            target_class=target_class,
            client_ref=client_ref,
            scope=scope,
            authorization=authorization,
        )
    except ValueError as exc:
        return _render_detail(request, store, engagement, error=str(exc), status_code=400)
    return RedirectResponse(url=f"/engagements/{ref}", status_code=303)


@app.post("/engagements/{ref}/status")
def set_engagement_status(
    request: Request, ref: str, store: StoreDep, status: Annotated[str, Form()] = ""
) -> Response:
    """Move an engagement through its lifecycle."""
    engagement = _require_engagement(store, ref)
    try:
        store.set_status(ref, status)
    except ValueError as exc:
        return _render_detail(request, store, engagement, error=str(exc), status_code=400)
    return RedirectResponse(url=f"/engagements/{ref}", status_code=303)


@app.post("/engagements/{ref}/targets")
def add_target(
    request: Request,
    ref: str,
    store: StoreDep,
    make: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    firmware_version: Annotated[str, Form()] = "",
    interfaces: Annotated[str, Form()] = "",
    pinout_notes: Annotated[str, Form()] = "",
    serial: Annotated[str, Form()] = "",
) -> Response:
    """Add a device under test to an engagement."""
    engagement = _require_engagement(store, ref)
    try:
        store.add_target(
            ref,
            make=make,
            model=model,
            firmware_version=firmware_version,
            interfaces=interfaces,
            pinout_notes=pinout_notes,
            serial=serial,
        )
    except ValueError as exc:
        return _render_detail(request, store, engagement, error=str(exc), status_code=400)
    return RedirectResponse(url=f"/engagements/{ref}", status_code=303)


@app.post("/engagements/{ref}/targets/{target_id}/delete")
def delete_target(ref: str, target_id: int, store: StoreDep) -> Response:
    """Remove a target. Removing one that's already gone is a no-op."""
    _require_engagement(store, ref)
    store.remove_target(ref, target_id)
    return RedirectResponse(url=f"/engagements/{ref}", status_code=303)


@app.post("/engagements/{ref}/findings")
def add_finding(
    request: Request,
    ref: str,
    store: StoreDep,
    severity: Annotated[str, Form()] = "info",
    title: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
) -> Response:
    """Append a finding. There is no edit or delete — the log is append-only."""
    engagement = _require_engagement(store, ref)
    try:
        store.add_finding(ref, severity=severity, title=title, body=body)
    except ValueError as exc:
        return _render_detail(request, store, engagement, error=str(exc), status_code=400)
    return RedirectResponse(url=f"/engagements/{ref}", status_code=303)


@app.post("/engagements/{ref}/artifacts")
def upload_artifact(
    request: Request,
    ref: str,
    store: StoreDep,
    artifact_store: ArtifactStoreDep,
    kind: Annotated[str, Form()] = "other",
    artifact: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """Store an uploaded artifact and index it.

    The bytes are written first, then the metadata row; if indexing fails the
    file is removed, so an index row can never outlive its file and a stray file
    can't accumulate silently. A rejected upload (empty, too large) comes back on
    the page rather than as a 500.
    """
    engagement = _require_engagement(store, ref)

    if artifact is None or not artifact.filename:
        return _render_detail(
            request, store, engagement, artifact_error="Choose a file to upload.", status_code=400
        )

    try:
        stored = artifact_store.save(ref, artifact.filename, artifact.file.read())
    except ArtifactError as exc:
        return _render_detail(request, store, engagement, artifact_error=str(exc), status_code=400)

    try:
        store.record_artifact(
            ref,
            stored_name=stored.stored_name,
            content_type=stored.content_type,
            size=stored.size,
            kind=kind,
            original_name=artifact.filename,
        )
    except (ValueError, LookupError):
        # The row didn't take — don't leave the bytes orphaned on disk.
        artifact_store.delete(ref, stored.stored_name)
        raise

    return RedirectResponse(url=f"/engagements/{ref}", status_code=303)


@app.get("/engagements/{ref}/artifacts/{artifact_id}/download")
def download_artifact(
    ref: str, artifact_id: int, store: StoreDep, artifact_store: ArtifactStoreDep
) -> FileResponse:
    """Stream a stored artifact.

    **Served through the app on purpose.** A static mount on the artifact
    directory would bypass the auth gate once it lands and put sensitive dumps
    back on the open LAN. The name and ref pass through the store's path guards,
    so a crafted name is a 404, never a file from elsewhere on the box.
    """
    _require_engagement(store, ref)
    record = store.get_artifact(ref, artifact_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such artifact")

    try:
        path = artifact_store.artifact_path(ref, record.stored_name)
    except ValueError:
        raise HTTPException(status_code=404, detail="no such artifact") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such artifact")

    return FileResponse(
        path, media_type=record.content_type, filename=record.original_name or record.stored_name
    )


@app.post("/engagements/{ref}/artifacts/{artifact_id}/delete")
def delete_artifact(
    ref: str, artifact_id: int, store: StoreDep, artifact_store: ArtifactStoreDep
) -> Response:
    """Remove an artifact — both the index row and the bytes."""
    _require_engagement(store, ref)
    record = store.get_artifact(ref, artifact_id)
    if record is not None:
        store.remove_artifact(ref, artifact_id)
        artifact_store.delete(ref, record.stored_name)
    return RedirectResponse(url=f"/engagements/{ref}", status_code=303)


# -- benchhud API (read-only, ref-safe projection) --------------------------
#
# Gated by the ``/api/`` branch of require_session: a bearer token or a browser
# session, 401 on failure. Every payload goes through bench_recon.projection, the
# single choke point — these handlers never serialize an Engagement directly, so
# a sensitive field can't ride along by accident.


@app.get("/api/engagements")
def api_list_engagements(store: StoreDep, status: str | None = None) -> list[dict]:
    """The engagement queue benchhud pulls — ref-safe projection only.

    Defaults to open (anything not closed). ``?status=`` filters; ``open`` is
    accepted as an explicit synonym for the default.
    """
    try:
        engagements = (
            store.list_engagements(status=status)
            if status and status != "open"
            else store.list_engagements(open_only=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return [project_engagement(e) for e in engagements]


@app.get("/api/engagements/{ref}")
def api_get_engagement(ref: str, store: StoreDep) -> dict:
    """One engagement by ref, or 404 — the projection, never the record."""
    return project_engagement(_require_engagement(store, ref))


@app.get("/api/engagements/{ref}/artifacts")
def api_engagement_artifacts(ref: str, store: StoreDep) -> list[dict]:
    """Artifact metadata for the capture panel — names/kinds/sizes, never bytes."""
    _require_engagement(store, ref)
    return [project_artifact(a) for a in store.list_artifacts(ref)]


# -- rendering helpers ------------------------------------------------------


def _render_new(
    request: Request,
    values: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="engagement_new.html",
        context={
            "values": {name: (values or {}).get(name, "") for name in _NEW_FIELDS},
            "error": error,
        },
        status_code=status_code,
    )


def _render_detail(
    request: Request,
    store: EngagementStore,
    engagement: Engagement,
    error: str | None = None,
    artifact_error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    ref = engagement.ref
    return templates.TemplateResponse(
        request=request,
        name="engagement.html",
        context={
            "engagement": engagement,
            "targets": store.list_targets(ref),
            "findings": store.list_findings(ref),
            "artifacts": store.list_artifacts(ref),
            "all_statuses": [s.value for s in Status],
            "all_severities": [s.value for s in Severity],
            "error": error,
            "artifact_error": artifact_error,
        },
        status_code=status_code,
    )


def _require_engagement(store: EngagementStore, ref: str) -> Engagement:
    """Fetch an engagement for a URL path, or 404.

    A malformed ref raises ``ValueError`` from the parser — that's a bad URL, so
    it's a 404 rather than a 500.
    """
    try:
        engagement = store.get_by_ref(ref)
    except ValueError:
        raise HTTPException(status_code=404, detail="no such engagement") from None
    if engagement is None:
        raise HTTPException(status_code=404, detail="no such engagement")
    return engagement
