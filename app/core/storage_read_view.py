"""Independent SQLite snapshots for threaded public reads; no schema or writes."""
import copy
import sqlite3
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

@contextmanager
def read_view(storage):
    path = getattr(storage, 'path', None)
    # In-memory stores and doubles are local test fixtures; production stores are files.
    if not path or path == ':memory:' or getattr(storage, '_read_only_snapshot', False):
        yield storage
        return
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True,
                                 timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    snapshot = copy.copy(storage)
    snapshot.conn = connection
    snapshot._read_only_snapshot = True
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        yield snapshot
    finally:
        connection.close()

def isolated_read(function):
    @wraps(function)
    def wrapper(storage, *args, **kwargs):
        with read_view(storage) as snapshot:
            return function(snapshot, *args, **kwargs)
    return wrapper
