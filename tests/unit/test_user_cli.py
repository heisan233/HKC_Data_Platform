"""User administration command tests with an isolated MySQL database."""

from pathlib import Path

import pytest

from app.cli import create_user, disable_user, enable_user, list_users, reset_user_password
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
    user = UserRepository(tmp_path).authenticate("operator_1", "safe-test-password")
    assert user and user["must_change_password"]
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


def test_reset_password_command_requires_change_and_revokes_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    isolated_settings = Settings(data_root=tmp_path)
    repository = UserRepository(tmp_path)
    user = repository.create_user(
        "operator_1", "old-password", must_change_password=False
    )
    repository.create_session(user["id"])
    passwords = iter(["new-password", "new-password"])
    monkeypatch.setattr(reset_user_password, "settings", isolated_settings)
    monkeypatch.setattr(
        reset_user_password.getpass,
        "getpass",
        lambda _prompt: next(passwords),
    )

    assert reset_user_password.main(["--username", "operator_1"]) == 0
    current = repository.authenticate("operator_1", "new-password")
    assert current and current["must_change_password"]
    with repository.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0
    assert "下次登录必须修改" in capsys.readouterr().out


def test_disable_and_enable_user_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    isolated_settings = Settings(data_root=tmp_path)
    repository = UserRepository(tmp_path)
    user = repository.create_user(
        "operator_1", "safe-test-password", must_change_password=False
    )
    repository.create_session(user["id"])
    monkeypatch.setattr(disable_user, "settings", isolated_settings)
    monkeypatch.setattr(enable_user, "settings", isolated_settings)

    assert disable_user.main(["--username", "operator_1"]) == 0
    assert repository.authenticate("operator_1", "safe-test-password") is None
    with repository.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0

    assert enable_user.main(["--username", "operator_1"]) == 0
    assert repository.authenticate("operator_1", "safe-test-password")
    output = capsys.readouterr().out
    assert "用户已停用" in output
    assert "用户已启用" in output
