"""Fresh MySQL storage and concurrent stable-number allocation."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.db.datasets import DatasetRepository
from app.db.users import UserRepository


def test_existing_platform_sqlite_is_left_untouched(tmp_path: Path) -> None:
    old_file = tmp_path / "system/platform.sqlite"
    old_file.parent.mkdir(parents=True)
    with sqlite3.connect(old_file) as old:
        old.execute("CREATE TABLE users (username TEXT)")
        old.execute("INSERT INTO users VALUES ('old_account')")
    before = old_file.read_bytes()

    assert UserRepository(tmp_path).user_count() == 0
    UserRepository(tmp_path).create_user("new_account", "test-password")
    assert old_file.read_bytes() == before
    with sqlite3.connect(old_file) as old:
        assert old.execute("SELECT username FROM users").fetchone()[0] == "old_account"


def test_concurrent_dataset_registration_has_unique_stable_numbers(tmp_path: Path) -> None:
    repository = DatasetRepository(tmp_path)
    repository.initialize()

    def register(number: int) -> dict:
        return DatasetRepository(tmp_path).register(
            "raw", f"sample_{number}", f"Sample {number}", "test_robot", "a" * 64,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        rows = list(executor.map(register, range(24)))

    assert {row["id"] for row in rows} == {f"DS{number:06d}" for number in range(1, 25)}
    assert len(repository.list()) == 24
