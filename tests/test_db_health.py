import sqlite3
import subprocess

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from db_health import database_status


def test_database_status_rejects_empty_file(tmp_path):
    db_path = tmp_path / "empty.db"
    db_path.write_bytes(b"")

    ok, message = database_status(db_path)

    assert ok is False
    assert "empty" in message


def test_db_health_cli_exits_nonzero_for_empty_file(tmp_path):
    db_path = tmp_path / "empty.db"
    db_path.write_bytes(b"")

    result = subprocess.run(
        [sys.executable, str(ROOT / "backend" / "db_health.py"), "--path", str(db_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "empty" in result.stdout


def test_database_status_rejects_missing_tables(tmp_path):
    db_path = tmp_path / "missing_tables.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    ok, message = database_status(db_path)

    assert ok is False
    assert "missing table" in message


def test_database_status_can_require_seeded_nodes(tmp_path):
    db_path = tmp_path / "ready.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY)")

    ok, message = database_status(db_path, require_nodes=True)
    assert ok is False
    assert "no seeded nodes" in message

    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO nodes (id) VALUES (1)")

    ok, message = database_status(db_path, require_nodes=True)
    assert ok is True
    assert message == "database is ready"
