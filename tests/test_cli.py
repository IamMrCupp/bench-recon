"""Running bench-recon without a cluster: the data folder, the settings file, the CLI.

Mirrors benchhud-intake's and bench-parts' CLI, so one setup tool can drive all three.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from bench_recon import auth, cli, settings

# --- the data folder ------------------------------------------------------------


def test_data_folder_follows_each_platforms_convention(tmp_path) -> None:
    home = tmp_path
    assert settings.data_dir({}, "darwin", home) == (
        home / "Library" / "Application Support" / "bench-recon"
    )
    assert settings.data_dir({"APPDATA": "C:/Users/a/AppData/Roaming"}, "win32", home) == Path(
        "C:/Users/a/AppData/Roaming/bench-recon"
    )
    assert settings.data_dir({}, "linux", home) == home / ".local" / "share" / "bench-recon"
    assert settings.data_dir({"XDG_DATA_HOME": "/srv/x"}, "linux", home) == Path(
        "/srv/x/bench-recon"
    )


def test_data_folder_can_be_pointed_anywhere(tmp_path) -> None:
    env = {"BENCHRECON_DATA_DIR": str(tmp_path / "recon")}
    assert settings.data_dir(env, "darwin") == tmp_path / "recon"


# --- the settings file ------------------------------------------------------------


def test_settings_keep_other_keys_and_take_values_literally(tmp_path) -> None:
    path = tmp_path / "settings.env"
    settings.write_setting(path, "BENCHRECON_PASSWORD_HASH", "scrypt$16384$8$1$ab$cd")
    settings.write_setting(path, "BENCHRECON_API_TOKEN", "tok")
    assert settings.read_settings(path) == {
        "BENCHRECON_PASSWORD_HASH": "scrypt$16384$8$1$ab$cd",  # the $s survive
        "BENCHRECON_API_TOKEN": "tok",
    }


def test_settings_skip_comments_and_blank_lines(tmp_path) -> None:
    path = tmp_path / "settings.env"
    path.write_text("# a comment\n\nBENCHRECON_HOST=0.0.0.0\nnot a setting\n")
    assert settings.read_settings(path) == {"BENCHRECON_HOST": "0.0.0.0"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_an_unreadable_settings_file_counts_as_empty(tmp_path, monkeypatch) -> None:
    """The .deb service: systemd passes the root-owned file's values in itself."""
    path = tmp_path / "settings.env"
    settings.write_setting(path, "BENCHRECON_API_TOKEN", "tok")

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(path), "read_text", denied)
    assert settings.read_settings(path) == {}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_settings_file_is_owner_only(tmp_path) -> None:
    path = tmp_path / "settings.env"
    settings.write_setting(path, "BENCHRECON_API_TOKEN", "tok")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_environment_variables_win_over_the_file(tmp_path) -> None:
    """A Kubernetes Secret (or an explicit env var) beats settings.env."""
    settings.write_setting(settings.settings_path(tmp_path), "BENCHRECON_API_TOKEN", "from-file")
    env = {"BENCHRECON_API_TOKEN": "from-env", "BENCHRECON_DB_PATH": "/data/x.db"}
    settings.apply(tmp_path, env)
    assert env["BENCHRECON_API_TOKEN"] == "from-env"
    assert env["BENCHRECON_DB_PATH"] == "/data/x.db"


def test_apply_fills_in_the_gaps(tmp_path) -> None:
    settings.write_setting(settings.settings_path(tmp_path), "BENCHRECON_API_TOKEN", "from-file")
    env: dict[str, str] = {}
    settings.apply(tmp_path, env)
    assert env["BENCHRECON_API_TOKEN"] == "from-file"
    assert env["BENCHRECON_DB_PATH"] == str(tmp_path / "bench-recon.db")
    assert env["BENCHRECON_ARTIFACTS_PATH"] == str(tmp_path / "artifacts")


# --- the commands --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _own_environment(monkeypatch):
    """``serve`` fills in os.environ; give each test its own copy so nothing leaks."""
    monkeypatch.setattr(os, "environ", dict(os.environ))


def _answers(*replies: str):
    it = iter(replies)
    return lambda _prompt: next(it)


def _args(tmp_path, *argv: str):
    return cli.build_parser().parse_args(["--data-dir", str(tmp_path), *argv])


def test_set_password_stores_a_hash_that_verifies(tmp_path) -> None:
    code = cli.cmd_set_password(_args(tmp_path, "set-password"), _answers("pw one", "pw one"))
    assert code == 0
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password("pw one", stored)
    assert "pw one" not in (tmp_path / "settings.env").read_text()


def test_set_password_refuses_a_mismatch_or_a_blank(tmp_path) -> None:
    args = _args(tmp_path, "set-password")
    assert cli.cmd_set_password(args, _answers("a", "b")) == 1
    assert cli.cmd_set_password(args, _answers("  ", "  ")) == 1
    assert not (tmp_path / "settings.env").exists()


def test_new_token_is_saved_and_printed(tmp_path, capsys) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "new-token"]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 40
    assert settings.read_settings(tmp_path / "settings.env")[auth.API_TOKEN_ENV_VAR] == token


def test_new_token_keeps_the_password(tmp_path) -> None:
    cli.cmd_set_password(_args(tmp_path, "set-password"), _answers("pw", "pw"))
    cli.main(["--data-dir", str(tmp_path), "new-token"])
    assert auth.ENV_VAR in settings.read_settings(tmp_path / "settings.env")


def test_paths_names_the_data_settings_and_database(tmp_path, capsys) -> None:
    os.environ.pop("BENCHRECON_DB_PATH", None)
    os.environ.pop("BENCHRECON_ARTIFACTS_PATH", None)
    assert cli.main(["--data-dir", str(tmp_path), "paths"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "settings.env") in out
    assert str(tmp_path / "bench-recon.db") in out
    assert str(tmp_path / "artifacts") in out


def test_serve_refuses_without_a_password(tmp_path, capsys) -> None:
    """Fail closed, as the app does: never serve engagement records unprotected."""
    os.environ.pop(auth.ENV_VAR, None)
    ran = []
    code = cli.cmd_serve(_args(tmp_path, "serve"), run=lambda *a, **k: ran.append(a))
    assert code == 2
    assert ran == []
    assert "set-password" in capsys.readouterr().err


def test_serve_reads_the_password_from_settings(tmp_path) -> None:
    os.environ.pop(auth.ENV_VAR, None)
    cli.cmd_set_password(_args(tmp_path, "set-password"), _answers("pw", "pw"))
    ran = []
    code = cli.cmd_serve(_args(tmp_path, "serve"), run=lambda *a, **k: ran.append(k))
    assert code == 0
    assert ran
    assert auth.verify_password("pw", os.environ[auth.ENV_VAR])


def test_serve_uses_the_data_folder_and_stays_on_loopback(tmp_path, capsys) -> None:
    os.environ.pop("BENCHRECON_DB_PATH", None)
    os.environ.pop("BENCHRECON_ARTIFACTS_PATH", None)
    calls = []
    code = cli.cmd_serve(
        _args(tmp_path, "serve", "--port", "8123"), run=lambda app, **kw: calls.append(kw)
    )
    assert code == 0
    assert calls == [{"host": "127.0.0.1", "port": 8123}]
    assert os.environ["BENCHRECON_DB_PATH"] == str(tmp_path / "bench-recon.db")
    assert os.environ["BENCHRECON_ARTIFACTS_PATH"] == str(tmp_path / "artifacts")
    assert "anyone on this network" not in capsys.readouterr().err


def test_serve_warns_when_shared_on_the_lan(tmp_path, capsys) -> None:
    cli.cmd_serve(_args(tmp_path, "serve", "--host", "0.0.0.0"), run=lambda *a, **k: None)
    assert "anyone on this network" in capsys.readouterr().err


# --- non-interactive flags (bench-setup drives these; secrets never in argv) ---------


def _stdin(monkeypatch, text: str) -> None:
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _never_ask(_prompt: str) -> str:
    raise AssertionError("a non-interactive flag must not prompt")


def test_set_password_stdin_reads_one_line_without_prompting(tmp_path, monkeypatch, capsys):
    _stdin(monkeypatch, "from a pipe\nsecond line ignored\n")
    code = cli.cmd_set_password(_args(tmp_path, "set-password", "--stdin"), _never_ask)
    assert code == 0
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password("from a pipe", stored)
    assert capsys.readouterr().out == ""  # stdout stays clean for a pipeline


def test_set_password_stdin_keeps_inner_spaces_and_strips_only_the_newline(
    tmp_path, monkeypatch
) -> None:
    _stdin(monkeypatch, " two  spaces \r\n")
    assert cli.cmd_set_password(_args(tmp_path, "set-password", "--stdin"), _never_ask) == 0
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password(" two  spaces ", stored)


@pytest.mark.parametrize("piped", ["", "\n", "   \n"])
def test_set_password_stdin_refuses_a_blank(tmp_path, monkeypatch, capsys, piped) -> None:
    _stdin(monkeypatch, piped)
    assert cli.cmd_set_password(_args(tmp_path, "set-password", "--stdin"), _never_ask) == 1
    assert not (tmp_path / "settings.env").exists()
    assert "blank" in capsys.readouterr().err


def test_set_password_generate_prints_it_once_and_saves_its_hash(tmp_path, capsys) -> None:
    code = cli.cmd_set_password(_args(tmp_path, "set-password", "--generate"), _never_ask)
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # the password, alone, once
    password = out.strip()
    assert len(password) >= 20
    stored = settings.read_settings(tmp_path / "settings.env")[auth.ENV_VAR]
    assert auth.verify_password(password, stored)
    assert password not in (tmp_path / "settings.env").read_text()


def test_set_password_generate_differs_each_time(tmp_path, capsys) -> None:
    args = _args(tmp_path, "set-password", "--generate")
    cli.cmd_set_password(args, _never_ask)
    first = capsys.readouterr().out
    cli.cmd_set_password(args, _never_ask)
    assert capsys.readouterr().out != first


def test_stdin_and_generate_are_exclusive(tmp_path) -> None:
    with pytest.raises(SystemExit) as exit_:
        _args(tmp_path, "set-password", "--stdin", "--generate")
    assert exit_.value.code == 2


def test_new_token_print_writes_it_and_prints_only_the_token(tmp_path, capsys) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "new-token", "--print"]) == 0
    captured = capsys.readouterr()
    token = captured.out.strip()
    assert captured.out == token + "\n"
    assert captured.err == ""
    assert settings.read_settings(tmp_path / "settings.env")[auth.API_TOKEN_ENV_VAR] == token
