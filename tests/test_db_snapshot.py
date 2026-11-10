"""The database snapshot: a lost database is restored instead of re-seeded (it was, on 2026-10-31)."""
import hashlib
import os
import sqlite3
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db_snapshot  # noqa: E402
from db_health import database_status  # noqa: E402
from models import Base, Node  # noqa: E402

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def make_database(path, nodes=3):
    """A real pipeline database: the application's own schema with `nodes` companies in it."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add_all(Node(name=f"Company {i} Inc.", ticker=f"T{i}", sector="Technology", entity_type="Company") for i in range(nodes))
        session.commit()
    engine.dispose()
    return str(path)


def count_nodes(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hours_ago(hours):
    return NOW - timedelta(hours=hours)


@pytest.fixture
def folder(tmp_path):
    return str(tmp_path / "snapshots")


@pytest.fixture
def live(tmp_path):
    return make_database(tmp_path / "supply_chain.db", nodes=5)


def corrupt(path):
    data = bytearray(Path(path).read_bytes())
    for offset in range(4096, len(data), 4096):
        data[offset:offset + 64] = b"\xff" * 64
    Path(path).write_bytes(bytes(data))


def test_a_snapshot_is_a_complete_copy_and_the_live_database_is_untouched(live, folder):
    before = digest(live)
    path, message = db_snapshot.take_snapshot(live, folder, now=NOW)
    assert os.path.basename(path) == "supply_chain-20261108T120000Z.db"
    assert count_nodes(path) == 5 and database_status(path, require_nodes=True)[0]
    assert digest(live) == before
    assert "keeping 1" in message and "MB" in message and "free" in message
    assert os.stat(folder).st_mode & 0o777 == 0o700


def test_only_the_newest_few_snapshots_are_kept(live, folder):
    for hours in (50, 40, 30, 20, 10):
        db_snapshot.take_snapshot(live, folder, keep=3, now=hours_ago(hours))
    assert db_snapshot.list_snapshots(folder) == [
        "supply_chain-20261108T020000Z.db",  # 10 hours before NOW
        "supply_chain-20261107T160000Z.db",  # 20
        "supply_chain-20261107T060000Z.db",  # 30; the 40 and 50 hour copies are gone
    ]


def test_the_default_number_kept_is_small_and_can_be_changed(live, folder, monkeypatch):
    assert db_snapshot.keep_count() == 3
    monkeypatch.setenv("HEPHAESTUS_SNAPSHOT_KEEP", "5")
    assert db_snapshot.keep_count() == 5
    monkeypatch.setenv("HEPHAESTUS_SNAPSHOT_KEEP", "many")
    assert db_snapshot.keep_count() == 3
    monkeypatch.setenv("HEPHAESTUS_SNAPSHOT_KEEP", "0")
    assert db_snapshot.keep_count() == 1  # never prune down to nothing


@pytest.mark.parametrize("state", ["missing", "empty", "garbage", "no_nodes"])
def test_an_unhealthy_database_is_never_snapshotted_and_never_costs_a_good_copy(tmp_path, folder, live, state):
    good, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(24))
    bad = tmp_path / "bad.db"
    if state == "empty":
        bad.write_bytes(b"")
    elif state == "garbage":
        bad.write_bytes(b"this is not a database" * 500)
    elif state == "no_nodes":
        make_database(bad, nodes=0)
    path, message = db_snapshot.take_snapshot(str(bad), folder, keep=1, now=NOW)
    assert path is None and message.startswith("not snapshotting")
    assert db_snapshot.list_snapshots(folder) == [os.path.basename(good)]  # keep=1 did not prune the good one either


def test_a_copy_that_fails_its_own_check_is_discarded(live, folder, monkeypatch):
    good, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(24))
    real = db_snapshot.integrity_problem
    monkeypatch.setattr(db_snapshot, "integrity_problem", lambda path: "page 7: btree corruption" if ".snapshot-" in path else real(path))
    path, message = db_snapshot.take_snapshot(live, folder, keep=1, now=NOW)
    assert path is None and "failed its own check" in message and "btree corruption" in message
    assert db_snapshot.list_snapshots(folder) == [os.path.basename(good)]
    assert not [name for name in os.listdir(folder) if name.startswith(".snapshot-")]


def test_a_snapshot_is_refused_when_the_disk_cannot_hold_it(live, folder, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(db_snapshot.shutil, "disk_usage", lambda _path: usage(10**9, 10**9 - 100, 100))
    path, message = db_snapshot.take_snapshot(live, folder, now=NOW)
    assert path is None and "free where the copy needs" in message
    assert db_snapshot.list_snapshots(folder) == []


def test_a_killed_run_leaves_no_partial_copy_behind(live, folder):
    os.makedirs(folder)
    Path(folder, ".snapshot-abc123.tmp").write_bytes(b"half a copy")
    Path(folder, "notes.txt").write_text("someone's file")
    db_snapshot.take_snapshot(live, folder, now=NOW)
    assert sorted(os.listdir(folder)) == ["notes.txt", "supply_chain-20261108T120000Z.db"]


def test_a_missing_database_is_restored_from_the_newest_snapshot(live, tmp_path, folder):
    db_snapshot.take_snapshot(live, folder, now=hours_ago(30))
    with sqlite3.connect(live) as connection:
        connection.execute("DELETE FROM nodes WHERE ticker IN ('T0', 'T1')")
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    os.remove(live)

    restored, message = db_snapshot.restore_latest(live, folder, now=NOW)
    assert restored and "supply_chain-20261108T060000Z.db" in message and "6.0 hours old" in message
    assert count_nodes(live) == 3
    assert not os.path.exists(live + ".restoring")


def test_an_unusable_newest_snapshot_is_skipped_for_an_older_good_one(live, folder):
    old, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(30))
    newest, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    corrupt(newest)
    os.remove(live)

    restored, message = db_snapshot.restore_latest(live, folder, now=NOW)
    assert restored and os.path.basename(old) in message and "skipped 1 unusable newer snapshot" in message
    assert count_nodes(live) == 5


def test_nothing_is_restored_when_there_is_no_usable_snapshot(live, folder):
    only, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    corrupt(only)
    os.remove(live)
    restored, message = db_snapshot.restore_latest(live, folder, now=NOW)
    assert not restored and "no usable snapshot" in message and not os.path.exists(live)
    restored, message = db_snapshot.restore_latest(live, str(Path(folder).parent / "never-made"), now=NOW)
    assert not restored and "no snapshot in" in message


def test_a_usable_database_is_left_alone(live, folder):
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    before = digest(live)
    restored, message = db_snapshot.restore_latest(live, folder, now=NOW)
    assert not restored and "nothing to restore" in message and digest(live) == before


def test_a_damaged_file_in_the_way_is_set_aside_not_deleted(live, folder):
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    empty_of_nodes = make_database(Path(live).parent / "other.db", nodes=0)
    os.replace(empty_of_nodes, live)
    damaged = digest(live)

    restored, _ = db_snapshot.restore_latest(live, folder, now=NOW)
    assert restored and count_nodes(live) == 5
    assert digest(live + ".unusable") == damaged


def test_a_journal_left_by_the_lost_file_is_not_replayed_onto_the_restored_one(live, folder):
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    os.remove(live)
    for sidecar in ("-wal", "-shm", "-journal"):
        Path(live + sidecar).write_bytes(b"left over from the lost database")
    restored, _ = db_snapshot.restore_latest(live, folder, now=NOW)
    assert restored
    assert not any(os.path.exists(live + sidecar) for sidecar in ("-wal", "-shm", "-journal"))
    assert database_status(live, require_nodes=True)[0]


def test_files_that_are_not_snapshots_are_ignored(folder):
    os.makedirs(folder)
    for name in ("supply_chain-latest.db", "supply_chain-20261108.db", "other.db", "supply_chain-20261108T120000Z.db.bak"):
        Path(folder, name).write_bytes(b"x")
    assert db_snapshot.list_snapshots(folder) == []
    assert db_snapshot.list_snapshots(str(Path(folder) / "missing")) == []


def test_the_command_line_exit_codes_say_whether_anything_happened(live, tmp_path, folder, capsys, monkeypatch):
    assert db_snapshot.main(["restore", "--db", live, "--dir", folder]) == 1  # usable: nothing to do
    assert db_snapshot.main(["snapshot", "--db", live, "--dir", folder]) == 0
    assert db_snapshot.main(["snapshot", "--db", str(tmp_path / "missing.db"), "--dir", folder]) == 1
    os.remove(live)
    assert db_snapshot.main(["restore", "--db", live, "--dir", folder]) == 0
    assert db_snapshot.main(["list", "--dir", folder]) == 0
    out = capsys.readouterr().out
    assert "restored supply_chain-" in out and "1 snapshot(s) in" in out

    monkeypatch.setenv("HEPHAESTUS_SNAPSHOT_DIR", str(tmp_path / "from-env"))
    assert db_snapshot.main(["snapshot", "--db", live]) == 0
    assert db_snapshot.list_snapshots(str(tmp_path / "from-env"))


def test_the_default_folder_is_outside_the_checkout(monkeypatch):
    monkeypatch.delenv("HEPHAESTUS_SNAPSHOT_DIR", raising=False)
    folder = Path(db_snapshot.snapshot_dir()).resolve()
    assert ROOT not in folder.parents and folder != ROOT


class TestPipelineWiring:
    workflow = (ROOT / ".github" / "workflows" / "gpu_pipeline.yml").read_text(encoding="utf-8")

    def step(self, name):
        block = self.workflow.split(f"      - name: {name}", 1)[1]
        return block.split("\n      - name:", 1)[0]

    def test_a_lost_database_is_restored_before_it_is_rebuilt(self):
        metrics = self.step("Update Market Metrics")
        health = metrics.index("db_health.py --require-nodes")
        restore = metrics.index("db_snapshot.py restore")
        migrate = metrics.index("backend/database.py", restore)
        recheck = metrics.index("db_health.py --require-nodes", migrate)
        bootstrap = metrics.index("Bootstrapping from scratch")
        assert health < restore < migrate < recheck < bootstrap < metrics.index("backend/seed_db.py")
        assert "seed_private_companies.py" in metrics[metrics.index("seed_db.py"):]

    def test_the_snapshot_is_taken_after_the_run_and_can_never_fail_the_publish(self):
        names = [line.split("name:", 1)[1].strip() for line in self.workflow.splitlines() if line.strip().startswith("- name:")]
        position = names.index("Snapshot the database")
        assert names.index("Execute AI Review Pipeline & Export") < position < names.index("Mint fresh publish token")
        step = self.step("Snapshot the database")
        assert "continue-on-error: true" in step and "db_snapshot.py snapshot" in step
