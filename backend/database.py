import os
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from models import Base

# Create the SQLite database file in the same backend folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "supply_chain.db")

# Allow short-lived readers/writers from scheduled jobs to finish instead of
# failing an otherwise healthy SQLite database immediately.
SQLITE_TIMEOUT_SECONDS = 30
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"timeout": SQLITE_TIMEOUT_SECONDS},
)


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, _connection_record):
    dbapi_connection.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
    # SQLite ignores foreign keys unless asked; without this, deleting a company
    # leaves edges pointing at a node id that no longer exists.
    dbapi_connection.execute("PRAGMA foreign_keys = ON")

# Create a configured "Session" class to interact with the DB
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """
    Creates the tables if they don't already exist.
    """
    Base.metadata.create_all(bind=engine)
    apply_lightweight_migrations()
    print(f"Database successfully initialized at {DB_PATH}")

def apply_lightweight_migrations():
    """
    Applies small SQLite-safe migrations for local databases created before
    SQLAlchemy models gained new nullable columns.
    """
    inspector = inspect(engine)
    if "edges" not in inspector.get_table_names():
        return

    edge_columns = {column["name"] for column in inspector.get_columns("edges")}
    with engine.begin() as connection:
        if "product" not in edge_columns:
            connection.execute(text("ALTER TABLE edges ADD COLUMN product VARCHAR"))
        if "source_title" not in edge_columns:
            connection.execute(text("ALTER TABLE edges ADD COLUMN source_title VARCHAR"))
        if "evidence_excerpt" not in edge_columns:
            connection.execute(text("ALTER TABLE edges ADD COLUMN evidence_excerpt TEXT"))
        if "review_status" not in edge_columns:
            connection.execute(text("ALTER TABLE edges ADD COLUMN review_status VARCHAR NOT NULL DEFAULT 'pending'"))
            connection.execute(text("""
                UPDATE edges
                SET review_status = CASE
                    WHEN source_url LIKE '%Manual%' THEN 'approved'
                    ELSE 'pending'
                END
            """))
        if "review_note" not in edge_columns:
            connection.execute(text("ALTER TABLE edges ADD COLUMN review_note TEXT"))
        if "reviewed_at" not in edge_columns:
            connection.execute(text("ALTER TABLE edges ADD COLUMN reviewed_at DATETIME"))
        if "revenue_share" not in edge_columns:
            connection.execute(text("ALTER TABLE edges ADD COLUMN revenue_share FLOAT"))
        # create_all() skips tables that already exist, so indexes added to the
        # models later must be created here for databases built before them.
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_edges_source_id ON edges (source_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_edges_target_id ON edges (target_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_edges_review_status ON edges (review_status)"))

# If you run `python database.py` from the terminal, it will create the tables.
if __name__ == "__main__":
    init_db()
