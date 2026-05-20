import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from models import Base

# Create the SQLite database file in the same backend folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "supply_chain.db")

# Set up the SQLAlchemy engine
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

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

# If you run `python database.py` from the terminal, it will create the tables.
if __name__ == "__main__":
    init_db()
