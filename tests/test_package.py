"""The package imports and advertises a version.

Cheap, but it catches a broken ``pyproject``/``__init__`` version wiring (the
``x-release-please-version`` marker release-please rewrites) before CI gets to
anything more interesting.
"""

import bench_recon


def test_version_is_a_nonempty_string() -> None:
    assert isinstance(bench_recon.__version__, str)
    assert bench_recon.__version__


def test_cli_version_matches(capsys) -> None:
    from bench_recon.cli import main

    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == bench_recon.__version__
