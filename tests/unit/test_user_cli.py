"""User administration command tests with an isolated SQLite database."""

from pathlib import Path

import pytest

from app.cli import create_user, list_users
from app.core.config import Settings
from app.db.users import UserRepository


def test_create_user_command_prompts_for_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    isolated_settings = Settings(data_root=tmp_path)
    passwords = iter(["safe-test-password", "safe-test-password"])
    monkeypatch.setattr(create_user, "settings", isolated_settings)
    monkeypatch.setattr(create_user.getpass, "getpass", lambda _prompt: next(passwords))

    assert create_user.main(["--username", "operator_1"]) == 0
    assert UserRepository(tmp_path).authenticate("operator_1", "safe-test-password")
    assert "用户已创建：operator_1" in capsys.readouterr().out


def test_create_user_command_rejects_mismatched_passwords(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    isolated_settings = Settings(data_root=tmp_path)
    passwords = iter(["safe-test-password", "different-password"])
    monkeypatch.setattr(create_user, "settings", isolated_settings)
    monkeypatch.setattr(create_user.getpass, "getpass", lambda _prompt: next(passwords))

    with pytest.raises(SystemExit) as error:
        create_user.main(["--username", "operator_1"])

    assert error.value.code == 2
    assert UserRepository(tmp_path).user_count() == 0
    assert "两次输入的密码不一致" in capsys.readouterr().err


def test_list_users_command_does_not_print_authentication_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    isolated_settings = Settings(data_root=tmp_path)
    UserRepository(tmp_path).create_user("operator_1", "safe-test-password")
    monkeypatch.setattr(list_users, "settings", isolated_settings)

    assert list_users.main() == 0
    output = capsys.readouterr().out
    assert "operator_1" in output
    assert "safe-test-password" not in output
    assert "scrypt$" not in output
