# bench-recon

Self-hosted **engagement/target system of record** for hardware, automotive, and
IoT pentesting at the bench. An engagement — scope, authorization, client
reference, dates, status — holds one or more **targets** (the devices under
test), which accumulate an **append-only findings log** and on-disk **artifacts**
(firmware dumps, captures). A small FastAPI + SQLite web app and LAN REST API,
built as a sibling to [benchhud-intake](https://github.com/IamMrCupp/benchhud-intake).

> **Sensitive by design.** This service holds client and target identities,
> scope and authorization text, findings, and credentials. It is **private,
> LAN-only, and never publicly exposed.** [benchhud](https://github.com/IamMrCupp/benchhud)
> is handed only a `ref` and a **ref-safe projection** (`ref`, safe title, target
> class, status) over `/api/engagements` — the sensitive detail never leaves this
> app, so nothing identifying can reach a surface that composites to a stream.

## Status

Early scaffold. The health probe is up; the store, web UI, the deny-by-default
auth gate, the benchhud projection, and the cluster deploy land in subsequent
pull requests (see the per-repo plan).

## Run it locally

```bash
uv sync
uv run bench-recon serve        # http://127.0.0.1:8000/
curl -s http://127.0.0.1:8000/healthz   # {"status":"ok"}
```

## Develop

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Deploy

An arm64 image publishes to `ghcr.io/iammrcupp/bench-recon` on merge to `main`.
It runs as a fourth app on the `rpi-k8s` cluster — LAN-only, single writer
(`replicas: 1` + `strategy: Recreate`), SQLite on an NFS PVC. Manifests live in
`cluster-management/apps/bench-recon/`.

## Licence

Proprietary — all rights reserved. See [LICENSE](LICENSE).
