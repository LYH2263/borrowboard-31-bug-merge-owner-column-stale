import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "borrowboard.db"

def connect():
    # isolation_level=None => autocommit; writers must open an explicit BEGIN IMMEDIATE
    # so that validation reads happen only after the RESERVED lock is held.
    c = sqlite3.connect(db_path(), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    return c

@contextmanager
def txn():
    """Serialize writers: acquire the write lock first, commit on success, rollback on error."""
    c = connect()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
