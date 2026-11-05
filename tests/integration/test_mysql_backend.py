"""Fresh MySQL storage and concurrent stable-number allocation."""

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mysql.connector

from app.db.datasets import DatasetRepository
from app.db.users import UserRepository, hash_password


def test_existing_v1_user_is_preserved_by_profile_migration(tmp_path: Path) -> None:
    """An existing account must remain usable and must not be forced to change."""
    native = mysql.connector.connect(
        host=os.environ["HKC_MYSQL_HOST"],
        port=int(os.environ["HKC_MYSQL_PORT"]),
        database=os.environ["HKC_MYSQL_DATABASE"],
        user=os.environ["HKC_MYSQL_USER"],
        password=os.environ["HKC_MYSQL_PASSWORD"],
    )
    cursor = native.cursor()
    try:
        cursor.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at VARCHAR(40) NOT NULL) ENGINE=InnoDB"
        )
        migration = (
            Path(__file__).resolve().parents[2] / "migrations/mysql/001_initial.sql"
        )
        for statement in migration.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                cursor.execute(statement)
        cursor.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'legacy')"
        )
        cursor.execute(
            """INSERT INTO users
               (username, password_hash, created_at, updated_at)
               VALUES (%s, %s, %s, %s)""",
            ("hei_san", hash_password("heisan233"), "legacy", "legacy"),
        )
        native.commit()
    finally:
        cursor.close()
        native.close()

    repository = UserRepository(tmp_path)
    user = repository.authenticate("hei_san", "heisan233")
    assert user
    assert user["display_name"] == "hei_san"
    assert user["must_change_password"] == 0
    with repository.connection() as database:
        versions = database.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [row["version"] for row in versions] == [1, 2]


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
