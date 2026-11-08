"""The ``bench-recon`` command: run it, and set it up, without a cluster.

    bench-recon set-password     # once: the login password (required)
    bench-recon new-token        # optional: the API token benchhud uses
    bench-recon set-password --stdin | --generate   # the same, for a setup tool
    bench-recon new-token --print                   # only the token on stdout
    bench-recon serve            # http://127.0.0.1:8000/
    bench-recon paths            # where the data and settings live
    bench-recon version

Everything lands in the per-OS data folder (see :mod:`bench_recon.settings`), and
environment variables still override it, so the container and the Kubernetes
Secret are unaffected. The container image runs ``serve`` on 0.0.0.0:8000 with
its data on the ``/data`` volume.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from bench_recon import __version__, auth, settings

#: Addresses that keep the server on this machine.
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _data(args: argparse.Namespace) -> Path:
    return Path(args.data_dir).expanduser() if args.data_dir else settings.data_dir()


def cmd_serve(args: argparse.Namespace, run: Callable[..., None] | None = None) -> int:
    data = _data(args)
    data.mkdir(parents=True, exist_ok=True)
    settings.apply(data)
    if not os.environ.get(auth.ENV_VAR, "").strip():
        # Fail closed, as the app itself does: no password, no engagement records.
        # Exit 2 is "not configured": the .deb's unit won't restart-loop on it.
        print(
            "bench-recon has no password yet, and won't serve engagement records "
            "without one. Set it with:\n    bench-recon set-password",
            file=sys.stderr,
        )
        return 2
    if args.host not in _LOOPBACK:
        print(
            f"Listening on {args.host}: anyone on this network can reach the login "
            "page. This service holds sensitive engagement records; keep it off "
            "public networks.",
            file=sys.stderr,
        )
    from bench_recon.app import app  # imported late: it reads the settings at import

    if run is None:
        import uvicorn

        run = uvicorn.run
    print(f"bench-recon on http://{args.host}:{args.port}/  (data: {data})")
    run(app, host=args.host, port=args.port)
    return 0


def _read_stdin_line() -> str:
    """One line from stdin, minus only its line ending: inner and edge spaces stay."""
    return sys.stdin.readline().removesuffix("\n").removesuffix("\r")


def cmd_set_password(args: argparse.Namespace, ask: Callable[[str], str] = getpass.getpass) -> int:
    """Set the login password.

    Interactive by default (asked twice, never echoed). For a setup tool,
    ``--stdin`` reads it once from a pipe and ``--generate`` makes one and prints
    it once. Neither takes the password as an argument, which would put it in
    ``ps`` and shell history.
    """
    generated = False
    if args.generate:
        password = secrets.token_urlsafe(18)
        generated = True
    elif args.stdin:
        password = _read_stdin_line()
    else:
        password = ask("New bench-recon password: ")
        if password != ask("Again: "):
            print("The passwords didn't match.", file=sys.stderr)
            return 1
    if not password.strip():
        print("The password can't be blank.", file=sys.stderr)
        return 1
    path = settings.settings_path(_data(args))
    settings.write_setting(path, auth.ENV_VAR, auth.hash_password(password))
    saved = f"Saved to {path}. Restart bench-recon if it's running."
    if generated:
        print(password)  # stdout carries the password alone, once
    # Non-interactive runs keep stdout for the caller; the note goes to stderr.
    print(saved, file=sys.stderr if (args.stdin or generated) else sys.stdout)
    return 0


def cmd_new_token(args: argparse.Namespace) -> int:
    token = secrets.token_urlsafe(32)
    path = settings.settings_path(_data(args))
    settings.write_setting(path, auth.API_TOKEN_ENV_VAR, token)
    print(token)
    if not args.print:
        print(
            f"Saved to {path}. Paste it into benchhud: Preferences → Recon → Token. "
            "The old token, if any, stops working when bench-recon restarts.",
            file=sys.stderr,
        )
    return 0


def cmd_paths(args: argparse.Namespace) -> int:
    data = _data(args)
    print(f"data:      {data}")
    print(f"settings:  {settings.settings_path(data)}")
    print(f"database:  {os.environ.get(settings.DB_PATH_ENV) or data / settings.DB_FILE}")
    print(
        f"artifacts: {os.environ.get(settings.ARTIFACTS_PATH_ENV) or data / settings.ARTIFACTS_DIR}"
    )
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench-recon",
        description="Self-hosted recon engagement record. Sensitive — keep it on your LAN.",
    )
    parser.add_argument("--data-dir", help="data folder (default: per-user app data)")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to share on the LAN")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    set_password = sub.add_parser("set-password", help="set the login password")
    how = set_password.add_mutually_exclusive_group()
    how.add_argument(
        "--stdin", action="store_true", help="read it once from stdin, without prompting"
    )
    how.add_argument("--generate", action="store_true", help="make a random one and print it once")
    set_password.set_defaults(func=cmd_set_password)

    new_token = sub.add_parser("new-token", help="make the API token benchhud uses")
    new_token.add_argument(
        "--print", action="store_true", help="print only the token (for scripts)"
    )
    new_token.set_defaults(func=cmd_new_token)
    sub.add_parser("paths", help="show where data and settings live").set_defaults(func=cmd_paths)
    sub.add_parser("version", help="print the version").set_defaults(func=cmd_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
