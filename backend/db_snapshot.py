#!/usr/bin/env python3
"""Keep a copy of the SQLite graph outside the checkout, and put it back when the file is gone.

On 2026-10-31 the pipeline runner found backend/supply_chain.db missing and re-seeded from scratch.
Everything the database holds that git does not was lost with it: the review queue (543 pending links
became 27), when each company's filings were last read (the sweep started over), and the links found but
not yet decided. Decided links survive because data/edge_review_decisions.json restores them.

`snapshot` writes a consistent copy next to nothing the checkout can delete (default ~/.hephaestus/snapshots,
or HEPHAESTUS_SNAPSHOT_DIR) and keeps the newest few. `restore` puts the newest copy that is intact and
seeded back in place of a missing or unusable database. A snapshot that fails its own checks is never kept,
and a database that is not healthy is never snapshotted, so a bad night cannot overwrite a good copy.

    python3 backend/db_snapshot.py snapshot    # after a run; exit 1 when nothing was written
    python3 backend/db_snapshot.py restore     # before a re-seed; exit 0 only when a copy was put back
    python3 backend/db_snapshot.py list
"""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

from db_health import DEFAULT_DB_PATH, SQLITE_TIMEOUT_SECONDS, database_status

DEFAULT_SNAPSHOT_DIR = os.path.join(os.path.expanduser("~"), ".hephaestus", "snapshots")
DEFAULT_KEEP = 3
PREFIX = "supply_chain-"
SUFFIX = ".db"
TEMP_PREFIX = ".snapshot-"
# Free space the target disk must keep beyond the copy itself (the copy is written before the old ones go).
FREE_SPACE_FACTOR = 2


def snapshot_dir(path=None):
    return path or os.environ.get("HEPHAESTUS_SNAPSHOT_DIR") or DEFAULT_SNAPSHOT_DIR


def keep_count(value=None):
    try:
        keep = int(value if value is not None else os.environ.get("HEPHAESTUS_SNAPSHOT_KEEP") or DEFAULT_KEEP)
    except ValueError:
        return DEFAULT_KEEP
    return max(1, keep)


def snapshot_name(now):
    return f"{PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}{SUFFIX}"


def snapshot_time(name):
    """When a snapshot file was taken, from its name; None for any other file."""
    if not (name.startswith(PREFIX) and name.endswith(SUFFIX)):
        return None
    try:
        return datetime.strptime(name[len(PREFIX):-len(SUFFIX)], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def list_snapshots(directory):
    """Snapshot file names, newest first."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted((name for name in names if snapshot_time(name) is not None), key=snapshot_time, reverse=True)


def integrity_problem(path):
    """Why SQLite itself does not trust this file, or '' when it does.

    quick_check reads every page like integrity_check but skips the index cross-checks, which on a
    database of this size would take minutes for nothing a backup needs.
    """
    try:
        with sqlite3.connect(path, timeout=SQLITE_TIMEOUT_SECONDS) as connection:
            rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError as exc:
        return str(exc)
    return "" if rows == [("ok",)] else "; ".join(str(row[0]) for row in rows[:3])


def usable_problem(path):
    """Why this file cannot serve as the pipeline's database, or '' when it can."""
    ok, message = database_status(path, require_nodes=True)
    if not ok:
        return message
    return integrity_problem(path)


def megabytes(size):
    return f"{size / 1_000_000:,.1f} MB"


def prune(directory, keep):
    """Delete all but the newest `keep` snapshots and any partial copy a killed run left behind."""
    names = list_snapshots(directory)
    for name in names[keep:]:
        os.remove(os.path.join(directory, name))
    for name in os.listdir(directory):
        if name.startswith(TEMP_PREFIX):
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass
    return names[:keep], names[keep:]


def take_snapshot(db_path=DEFAULT_DB_PATH, directory=None, keep=None, now=None):
    """Copy a healthy database into the snapshot folder. Returns (path or None, message)."""
    directory, keep, now = snapshot_dir(directory), keep_count(keep), now or datetime.now(timezone.utc)
    ready, message = database_status(db_path, require_nodes=True)  # the copy gets the full check below
    if not ready:
        return None, f"not snapshotting: {message}"
    size = os.path.getsize(db_path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < FREE_SPACE_FACTOR * size:
        return None, f"not snapshotting: {megabytes(free)} free where the copy needs {megabytes(FREE_SPACE_FACTOR * size)}"

    handle, partial = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".tmp", dir=directory)
    os.close(handle)
    try:
        # SQLite's own backup, from a read-only connection: consistent even if something is writing, and
        # the live database is never modified.
        source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=SQLITE_TIMEOUT_SECONDS)
        target = sqlite3.connect(partial)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        problem = usable_problem(partial)
        if problem:
            return None, f"not snapshotting: the copy failed its own check ({problem})"
        final = os.path.join(directory, snapshot_name(now))
        os.replace(partial, final)
    except (OSError, sqlite3.Error) as exc:
        return None, f"not snapshotting: {exc}"
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    kept, _removed = prune(directory, keep)
    total = sum(os.path.getsize(os.path.join(directory, name)) for name in kept)
    return final, (
        f"snapshot {os.path.basename(final)}: {megabytes(os.path.getsize(final))}; keeping {len(kept)} "
        f"({megabytes(total)} in {directory}); {megabytes(shutil.disk_usage(directory).free)} free"
    )


def restore_latest(db_path=DEFAULT_DB_PATH, directory=None, now=None):
    """Put the newest intact, seeded snapshot where the database should be. Returns (restored, message).

    A database that is already usable is left alone. A damaged file that is in the way is kept as
    `<name>.unusable` (one copy, replaced next time) rather than deleted.
    """
    directory, now = snapshot_dir(directory), now or datetime.now(timezone.utc)
    ready, _message = database_status(db_path, require_nodes=True)
    if ready:
        return False, "the database is already usable; nothing to restore"
    names = list_snapshots(directory)
    if not names:
        return False, f"no snapshot in {directory}"
    skipped = []
    for name in names:
        candidate = os.path.join(directory, name)
        problem = usable_problem(candidate)
        if problem:
            skipped.append(f"{name}: {problem}")
            continue
        staging = f"{db_path}.restoring"
        try:
            shutil.copyfile(candidate, staging)
            if os.path.exists(db_path) and os.path.getsize(db_path):
                os.replace(db_path, f"{db_path}.unusable")
            # A leftover journal from the lost file would be replayed onto the restored one.
            for sidecar in ("-wal", "-shm", "-journal"):
                if os.path.exists(db_path + sidecar):
                    os.remove(db_path + sidecar)
            os.replace(staging, db_path)
        except OSError as exc:
            skipped.append(f"{name}: {exc}")
            if os.path.exists(staging):
                os.remove(staging)
            continue
        age = now - snapshot_time(name)
        note = f"; skipped {len(skipped)} unusable newer snapshot(s) ({'; '.join(skipped)})" if skipped else ""
        return True, f"restored {name} ({age.total_seconds() / 3600:.1f} hours old){note}"
    return False, f"no usable snapshot in {directory} ({'; '.join(skipped)})"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Snapshot and restore the local SQLite database.")
    parser.add_argument("command", choices=("snapshot", "restore", "list"))
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--dir", default=None, help="snapshot folder (default: HEPHAESTUS_SNAPSHOT_DIR or ~/.hephaestus/snapshots)")
    parser.add_argument("--keep", type=int, default=None, help=f"snapshots to keep (default: HEPHAESTUS_SNAPSHOT_KEEP or {DEFAULT_KEEP})")
    args = parser.parse_args(argv)

    if args.command == "list":
        directory = snapshot_dir(args.dir)
        names = list_snapshots(directory)
        for name in names:
            print(f"{name}  {megabytes(os.path.getsize(os.path.join(directory, name)))}")
        print(f"{len(names)} snapshot(s) in {directory}")
        return 0
    if args.command == "snapshot":
        path, message = take_snapshot(args.db, args.dir, args.keep)
        print(message)
        return 0 if path else 1
    restored, message = restore_latest(args.db, args.dir)
    print(message)
    return 0 if restored else 1


if __name__ == "__main__":
    sys.exit(main())
