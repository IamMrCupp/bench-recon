# bench-recon

Self-hosted **engagement/target system of record** for hardware, automotive, and
IoT pentesting at the bench. An engagement — scope, authorization, client
reference, dates, status — holds one or more **targets** (the devices under
test), which accumulate an **append-only findings log** and on-disk **artifacts**
(firmware dumps, captures). A small FastAPI + SQLite web app and LAN REST API,
built as a sibling to [benchhud-intake](https://github.com/IamMrCupp/benchhud-intake).

> **Sensitive by design.** A running bench-recon holds client and target
> identities, scope and authorization text, findings, and credentials, so every
> deployment must keep it **LAN-only and never publicly exposed** — no public
> ingress, no port forward. [benchhud](https://github.com/IamMrCupp/benchhud)
> is handed only a `ref` and a **ref-safe projection** (`ref`, safe title, target
> class, status) over `/api/engagements`. The sensitive detail never leaves the
> app, so nothing identifying can reach a surface that composites to a stream.
> The code is public; your engagement data stays on your bench.

## Status

Usable. Engagements, targets, the append-only findings log and the artifact
store work through the web UI. Everything sits behind a deny-by-default auth
gate (a scrypt-hashed shared password, a bearer token for `/api/`, CSRF on every
form, a login throttle). benchhud reads the ref-safe projection over a read-only
`/api/engagements` API. The schema is at version 1.

## Run it locally

The app refuses to start without a password, so set one first:

```bash
uv sync
uv run bench-recon set-password     # prompts twice, saves the hash to settings.env
uv run bench-recon new-token        # optional: the bearer token benchhud's recon panel uses
uv run bench-recon serve            # http://127.0.0.1:8000/
curl -s http://127.0.0.1:8000/healthz   # {"status":"ok"}
```

Everything lands in a per-user data folder: `~/.local/share/bench-recon` on
Linux, `~/Library/Application Support/bench-recon` on macOS. `bench-recon paths`
prints it, and `--data-dir` (or `BENCHRECON_DATA_DIR`) points it elsewhere. It
holds `settings.env` (owner-only, `KEY=value` lines using the variable names
below), `bench-recon.db` and `artifacts/`.

**Environment variables always win** over `settings.env`, so a Kubernetes Secret
or an explicit `BENCHRECON_PASSWORD_HASH=…` behaves as before.
`BENCHRECON_DB_PATH` and `BENCHRECON_ARTIFACTS_PATH` move the database and the
artifact folder on their own. For a hash to paste into a Secret,
`uv run python -m bench_recon.auth` prompts twice and prints the line.

Optional settings:

| Variable | What it does |
|---|---|
| `BENCHRECON_SESSION_SECRET` | Signs the session cookie. Derived from the password hash if unset, so changing the password logs everyone out. |
| `BENCHRECON_API_TOKEN` | The bearer token for `/api/`, used by benchhud's recon panel. Unset means bearer auth is refused and `/api/` only answers a signed-in session. |
| `BENCHRECON_COOKIE_SECURE` | `1` once TLS fronts the app. Leave it off over plain HTTP, or login silently fails. |
| `BENCHRECON_SQLITE_NFS` | `1` when the database sits on NFS; it swaps WAL for NFS-safe journaling. |
| `BENCHRECON_ARTIFACT_MAX_BYTES` | Upload size cap for artifacts, in bytes. Defaults to 256 MiB. |

## Install as a service (Debian/Ubuntu)

CI builds a `.deb` (Ubuntu 26.04, amd64; arm64 on a run by hand) that runs
bench-recon as a hardened systemd service on `127.0.0.1:8403`. It installs
**dormant**: the unit is neither enabled nor started, because this service
holds client engagement data and nothing should run or store data until you
say so. Turn it on with `bench-setup`, or by hand:

```bash
sudo apt install ./bench-recon_*.deb
sudo bench-recon set-password
sudo systemctl enable --now bench-recon
```

Its data and `settings.env` live in `/var/lib/bench-recon` (`settings.env` is
root-only; systemd reads it). With no password the service exits 2 and stays
down rather than restart-looping. Removing the package keeps the data.

## Develop

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Deploy

Each release publishes a multi-arch image (linux/amd64 and linux/arm64) to
`ghcr.io/iammrcupp/bench-recon`, tagged with the version (`0.2.0`), the minor
line (`0.2`) and `latest`. It carries LICENSE and NOTICES at `/app`, and keeps
its data on a volume mounted at `/data`.

- **Docker Compose:** [`compose.yaml`](compose.yaml) runs it on this machine.
  The comments at the top cover the password hash.
- **Kubernetes:** [`deploy/kubernetes/`](deploy/kubernetes/) is a kustomize
  base. Its README covers the overlay you'll need.

Whichever you use, keep it single-writer (one replica: SQLite has one writer)
and LAN-only.

The maintainer's own deployment runs on a Raspberry Pi Kubernetes cluster: SQLite on
an NFS volume, a LAN-only LoadBalancer IP, Flux image automation, and a nightly
backup.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every commit needs a DCO sign-off
(`git commit -s`), and anything large starts with an issue.

## License

Source-available under the [PolyForm Small Business License 1.0.0](https://polyformproject.org/licenses/small-business/1.0.0/)
— see [LICENSE](LICENSE). Free to use, modify and distribute if your company has
fewer than 100 people and under $1M in annual revenue, which covers personal use
and most small shops. Above that, a commercial licence is required — get in touch.

Note this is *source-available*, not open source: the code is readable and
freely usable within those limits, but it isn't an OSI-approved licence.

Third-party dependencies and their licenses are listed in [NOTICES](NOTICES).
