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
curl -s http://127.0.0.1:8000/healthz   # 200 from the unauthenticated probe
```

Everything lands in a per-user data folder: `~/.local/share/bench-recon` on
Linux, `~/Library/Application Support/bench-recon` on macOS. `bench-recon paths`
prints it, and `--data-dir` (or `BENCHRECON_DATA_DIR`) points it elsewhere. It
holds `settings.env` (owner-only, `KEY=value` lines using the variable names
below), `bench-recon.db` and `artifacts/`.

For scripts and `bench-setup`, which never put a secret in argv:
`set-password --stdin` reads it once from a pipe, `set-password --generate`
makes one and prints it once, and `new-token --print` prints only the token.

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
| `BENCHRECON_SOURCE_URL` | Where the **Source** link in every page's footer points. Defaults to this repository. If you run a modified copy for other people, point it at your own code — that's the offer of source AGPL section 13 asks for. It wants an `http://` or `https://` address; anything else is ignored and the link stays on this repository. |

## Install as a service (Debian/Ubuntu)

Each release carries a `.deb` for Ubuntu 26.04, amd64 and arm64, that runs
bench-recon as a hardened systemd service on `127.0.0.1:8403`. It installs
**dormant**: the unit is neither enabled nor started, because this service
holds client engagement data and nothing should run or store data until you
say so. Turn it on with `bench-setup`, or by hand:

```bash
sudo apt install ./bench-recon_*.deb
sudo bench-recon set-password
sudo systemctl enable --now bench-recon
```

**Or from the bench apt repo (optional).** The same package is in the signed
apt repo that [bench-suite](https://github.com/IamMrCupp/bench-suite#readme)
publishes, so updates arrive with `apt upgrade`:

```bash
sudo curl -fsSLo /usr/share/keyrings/bench-archive-keyring.gpg https://apt.yolorepair.com/bench-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/bench-archive-keyring.gpg] https://apt.yolorepair.com stable main" \
  | sudo tee /etc/apt/sources.list.d/bench.list
sudo apt update
sudo apt install bench-recon
```

If the same machine runs the rest of the bench, `sudo apt install bench-suite`
installs the lot, and `sudo bench-setup` does the setup above for every service
at once: one password, a token each, and the URLs and tokens written into
benchhud's config. `bench-setup doctor` checks the result. bench-recon stays off there too until you say yes to it.

On a Mac there's no bench-recon app. It runs in a container from bench-suite's
`compose.yaml`, off until you turn it on with
`bench-setup services --compose --recon`. `bench-setup` comes from the
[Homebrew tap](https://github.com/IamMrCupp/homebrew-bench); see [bench-suite](https://github.com/IamMrCupp/bench-suite#on-a-mac).

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

Open source under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html)
or later — see [LICENSE](LICENSE). Use it, change it, ship it. If you distribute
a modified build, or run one as a service other people use, your changes go out
under the same licence.

Copyright © 2026 Aaron Cupp. If the AGPL doesn't fit how you want to use it, a
commercial licence is available — get in touch.

Third-party dependencies and their licenses are listed in [NOTICES](NOTICES).
