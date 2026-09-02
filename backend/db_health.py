import argparse
import os
import sqlite3


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "supply_chain.db")
REQUIRED_TABLES = {"nodes", "edges"}
# Columns the pipeline queries; a database created before they existed must be
# reported unhealthy so the caller runs database.py and its migrations.
REQUIRED_EDGE_COLUMNS = {
    "source_id",
    "target_id",
    "dependency_type",
    "product",
    "source_url",
    "source_title",
    "evidence_excerpt",
    "review_status",
    "review_note",
    "reviewed_at",
}
SQLITE_TIMEOUT_SECONDS = 30


def database_status(path=DEFAULT_DB_PATH, require_nodes=False):
    if not os.path.exists(path):
        return False, "database file is missing"
    if os.path.getsize(path) == 0:
        return False, "database file is empty"

    try:
        with sqlite3.connect(path, timeout=SQLITE_TIMEOUT_SECONDS) as connection:
            connection.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            tables = {row[0] for row in rows}
            missing = sorted(REQUIRED_TABLES - tables)
            if missing:
                return False, f"database schema is missing table(s): {', '.join(missing)}"

            edge_columns = {row[1] for row in connection.execute("PRAGMA table_info(edges)").fetchall()}
            missing_columns = sorted(REQUIRED_EDGE_COLUMNS - edge_columns)
            if missing_columns:
                return False, f"database schema is missing edge column(s): {', '.join(missing_columns)}"

            if require_nodes:
                node_count = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                if node_count == 0:
                    return False, "database has no seeded nodes"
    except sqlite3.DatabaseError as exc:
        return False, f"database is not readable: {exc}"

    return True, "database is ready"


def main():
    parser = argparse.ArgumentParser(description="Check whether the local SQLite database is usable.")
    parser.add_argument("--path", default=DEFAULT_DB_PATH)
    parser.add_argument("--require-nodes", action="store_true", help="Require at least one seeded node.")
    args = parser.parse_args()

    ok, message = database_status(args.path, require_nodes=args.require_nodes)
    print(message)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
