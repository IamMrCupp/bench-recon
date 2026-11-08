"""The ``bench-recon`` command.

    bench-recon serve            # http://127.0.0.1:8000/
    bench-recon version

``serve`` refuses to start unless ``BENCHRECON_PASSWORD_HASH`` is set; generate
one with ``python -m bench_recon.auth``. The container image runs ``serve`` on
0.0.0.0:8000 with its data on the ``/data`` volume.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from bench_recon import __version__

#: Addresses that keep the server on this machine.
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def cmd_serve(args: argparse.Namespace, run: Callable[..., None] | None = None) -> int:
    from bench_recon.app import app

    if args.host not in _LOOPBACK:
        print(
            f"Listening on {args.host}: anyone on this network can reach it. This "
            "service holds sensitive engagement records; keep it off public networks.",
            file=sys.stderr,
        )

    if run is None:
        import uvicorn

        run = uvicorn.run
    print(f"bench-recon on http://{args.host}:{args.port}/")
    run(app, host=args.host, port=args.port)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench-recon",
        description="Self-hosted recon engagement record. Sensitive — keep it on your LAN.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to share on the LAN")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    sub.add_parser("version", help="print the version").set_defaults(func=cmd_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
