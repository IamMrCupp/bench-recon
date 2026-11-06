"""Projecting an engagement into the shape benchhud consumes.

**This module is the sensitivity boundary, made mechanical.** bench-recon's
``Engagement`` is a superset carrying everything a recon job needs — client
identity, scope, the authorization text, and beneath it targets, findings and
artifacts. benchhud gets a deliberately lossy view: four ref-safe fields and
nothing else.

Why here, and why so strict: benchhud is the bench HUD, and it can composite any
of its surfaces to a virtual camera for streaming. So the rule can't be "hide the
sensitive bits when streaming" — it has to be "the sensitive bits never reach
benchhud at all", enforced once, in code, rather than trusted to a dozen render
sites on the other side. Everything this function omits is a thing that can't leak
because benchhud never receives it: no client ref, no scope, no authorization, no
target identity, no findings, no credentials.

This mirrors ``benchhud-intake``'s ``projection.py`` — the one PII choke point
that whole app is built around — applied to recon's wider sensitive set. The leak
test (``tests/test_projection.py``) scans the entire serialized output for every
sensitive value the engagement carried and fails on any match.

**Artifacts are the one deliberate exception**, and a narrow one: the capture
panel needs to show *what has been dumped* — a name, a kind, a size. So artifact
*metadata* crosses, never contents and never a path. The bytes stay behind
bench-recon's own gate.
"""

from __future__ import annotations

from bench_recon.store import Artifact, Engagement

#: The exact keys benchhud's ``Engagement`` expects. Anything not in here does not
#: cross the boundary — full stop. Matches ``benchhud.recon.source.Engagement``.
ENGAGEMENT_KEYS = ("ref", "title", "target_class", "status")

#: The exact keys of the artifact metadata benchhud's capture panel reads.
#: Metadata only — never the stored path, never the bytes.
ARTIFACT_KEYS = ("name", "kind", "size", "status")


def _status_value(status: object) -> str:
    return status.value if hasattr(status, "value") else str(status)


def project_engagement(engagement: Engagement) -> dict[str, str]:
    """Reduce an engagement to what benchhud may see.

    Exactly four fields. ``title`` is the operator-set ``safe_title`` — explicitly
    non-sensitive, the handle that shows on the HUD. ``target_class`` is a
    category ("automotive-ecu"), not an identity. ``client_ref``, ``scope``,
    ``authorization`` and every target/finding/artifact are dropped on the floor
    here and never leave this service.
    """
    return {
        "ref": engagement.ref,
        "title": engagement.safe_title,
        "target_class": engagement.target_class,
        "status": _status_value(engagement.status),
    }


def project_artifact(artifact: Artifact) -> dict[str, object]:
    """Reduce an artifact to the metadata the capture panel shows.

    Name, kind and size — enough to say "a 4 MB firmware dump landed" on the HUD.
    ``status`` is always ``"stored"``: a recorded artifact is a complete file on
    disk (bench-recon doesn't stream partial captures through its index). The
    ``content_type``, the generated ``stored_name`` path and the bytes themselves
    never cross — only the display name does.
    """
    return {
        "name": artifact.original_name or artifact.stored_name,
        "kind": artifact.kind,
        "size": artifact.size,
        "status": "stored",
    }
