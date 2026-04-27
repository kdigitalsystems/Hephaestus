import os
from sqlalchemy import create_engine
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
    print(f"Database successfully initialized at {DB_PATH}")

# If you run `python database.py` from the terminal, it will create the tables.
if __name__ == "__main__":
    init_db()
