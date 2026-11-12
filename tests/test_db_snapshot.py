"""The database snapshot: a lost database is restored instead of re-seeded (it was, on 2026-10-31)."""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
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
from models import Base, Edge, Node  # noqa: E402

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


def add_review_state(path, pending=2, approved=1, filings_read=2):
    """What only the database holds: links awaiting review and which filings have been read."""
    engine = create_engine(f"sqlite:///{path}")
    with sessionmaker(bind=engine)() as session:
        nodes = session.query(Node).order_by(Node.id).all()
        for index in range(pending + approved):
            session.add(Edge(
                source_id=nodes[0].id, target_id=nodes[index + 1].id, dependency_type="Components",
                review_status="pending" if index < pending else "approved",
            ))
        for node in nodes[:filings_read]:
            node.concentration_checked_at = NOW
        session.commit()
    engine.dispose()


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Where the drill makes its scratch folder, so a test can see that nothing is left in it."""
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def test_a_drill_restores_into_a_scratch_folder_and_reports_what_came_back(live, folder, scratch):
    add_review_state(live)
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    before, snapshots = digest(live), sorted(os.listdir(folder))

    passed, report = db_snapshot.restore_drill(live, folder, now=NOW)

    assert passed, report
    assert "restored supply_chain-20261108T060000Z.db (6.0 hours old) into a scratch folder" in report
    held = "holds 5 companies, 3 links (2 pending review) and 2 filing read-dates"
    assert f"the restored copy {held}" in report and f"the live database {held}" in report
    assert report.splitlines()[-1].startswith("drill passed")
    assert digest(live) == before and sorted(os.listdir(folder)) == snapshots
    assert os.listdir(scratch) == []
    assert sorted(os.listdir(Path(live).parent)) == ["scratch", "snapshots", "supply_chain.db"]


def test_a_drill_reads_the_snapshot_and_not_the_live_database(live, folder, scratch):
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    with sqlite3.connect(live) as connection:
        connection.execute("DELETE FROM nodes WHERE ticker = 'T0'")

    passed, report = db_snapshot.restore_drill(live, folder, now=NOW)

    assert passed
    assert "the restored copy holds 5 companies" in report and "the live database holds 4 companies" in report


def test_a_drill_passes_without_a_live_database_and_does_not_put_one_back(live, folder, scratch):
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    os.remove(live)

    passed, report = db_snapshot.restore_drill(live, folder, now=NOW)

    assert passed and "the live database is not usable right now (database file is missing)" in report
    assert not os.path.exists(live) and os.listdir(scratch) == []


def test_a_drill_fails_when_there_is_nothing_usable_to_restore(live, folder, scratch):
    passed, report = db_snapshot.restore_drill(live, folder, now=NOW)
    assert not passed and report.startswith("drill failed: no snapshot in")

    only, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    corrupt(only)
    passed, report = db_snapshot.restore_drill(live, folder, now=NOW)
    assert not passed and report.startswith("drill failed: no usable snapshot")
    assert os.listdir(scratch) == []


def test_a_drill_falls_back_to_an_older_snapshot_and_says_so(live, folder, scratch):
    old, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(30))
    newest, _ = db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    corrupt(newest)

    passed, report = db_snapshot.restore_drill(live, folder, now=NOW)

    assert passed and os.path.basename(old) in report and "skipped 1 unusable newer snapshot" in report


def test_a_drill_fails_when_the_copy_it_restored_is_damaged(live, folder, scratch, monkeypatch):
    """The snapshot passes its check but the copy does not arrive intact (a full or failing disk)."""
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    real_copy = shutil.copyfile

    def damaged_copy(source, target):
        real_copy(source, target)
        corrupt(target)

    monkeypatch.setattr(db_snapshot.shutil, "copyfile", damaged_copy)

    passed, report = db_snapshot.restore_drill(live, folder, now=NOW)

    assert not passed and "drill failed: the restored copy is not usable" in report
    assert os.listdir(scratch) == []


def test_the_drill_command_exits_zero_only_when_the_rehearsal_worked(live, tmp_path, folder, scratch, capsys):
    assert db_snapshot.main(["drill", "--db", live, "--dir", folder]) == 1
    assert "drill failed" in capsys.readouterr().out
    db_snapshot.take_snapshot(live, folder, now=hours_ago(6))
    assert db_snapshot.main(["drill", "--db", live, "--dir", folder]) == 0
    assert "drill passed" in capsys.readouterr().out


class TestDrillWiring:
    workflow = (ROOT / ".github" / "workflows" / "restore-drill.yml").read_text(encoding="utf-8")
    pipeline = (ROOT / ".github" / "workflows" / "gpu_pipeline.yml").read_text(encoding="utf-8")

    def test_it_only_ever_runs_by_hand(self):
        triggers = self.workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        assert triggers.strip() == "workflow_dispatch:"

    def test_it_runs_where_the_snapshots_are_and_never_beside_the_pipeline(self):
        runner = "runs-on: [self-hosted, nvidia-gpu, hephaestus]"
        assert runner in self.workflow and runner in self.pipeline
        group = "concurrency:\n  group: hephaestus-main-publisher\n  cancel-in-progress: false"
        assert group in self.workflow and group in self.pipeline

    def test_its_checkout_cannot_delete_the_live_database(self):
        checkout = self.workflow.split("uses: actions/checkout@", 1)[1].split("\n      - name:", 1)[0]
        assert "clean: false" in checkout

    def test_it_rehearses_and_never_restores_or_rebuilds_for_real(self):
        commands = [line.strip() for line in self.workflow.splitlines() if line.strip().startswith("run:")]
        assert commands == ["run: python3 backend/db_snapshot.py list", "run: python3 backend/db_snapshot.py drill"]

    def test_it_holds_no_secret_and_no_write_access(self):
        assert "permissions:\n  contents: read" in self.workflow
        assert "secrets." not in self.workflow and "write" not in self.workflow.split("permissions:", 1)[1].split("concurrency:", 1)[0]
