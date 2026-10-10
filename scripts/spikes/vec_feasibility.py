"""Spike, step 1: can sqlite-vec load in this Python on Windows? (throwaway)

    uv run --no-sync python scripts/spikes/vec_feasibility.py

Opens an in-memory SQLite database, loads the sqlite-vec extension, creates a vec0 table, inserts
four 3-d vectors and runs a KNN query. Prints each step so a failure says exactly where it broke.
"""

import platform
import sqlite3
import sys

import sqlite_vec

print(f"Python {sys.version.split()[0]} ({platform.machine()}), SQLite {sqlite3.sqlite_version}")
db = sqlite3.connect(":memory:")
print("1. enable_load_extension available:", hasattr(db, "enable_load_extension"))
db.enable_load_extension(True)
sqlite_vec.load(db)
db.enable_load_extension(False)  # load once, then lock it again
print("2. sqlite-vec loaded, vec_version() =", db.execute("select vec_version()").fetchone()[0])

db.execute("create virtual table v using vec0(embedding float[3])")
rows = {1: [1.0, 0.0, 0.0], 2: [0.0, 1.0, 0.0], 3: [0.0, 0.0, 1.0], 4: [0.9, 0.1, 0.0]}
db.executemany(
    "insert into v(rowid, embedding) values (?, ?)",
    [(i, sqlite_vec.serialize_float32(vec)) for i, vec in rows.items()],
)
print("3. vec0 table created, inserted", db.execute("select count(*) from v").fetchone()[0], "vectors")

query = sqlite_vec.serialize_float32([1.0, 0.05, 0.0])
hits = db.execute(
    "select rowid, distance from v where embedding match ? order by distance limit 2", (query,)
).fetchall()
print("4. KNN (k=2) for [1, 0.05, 0]:", [(r, round(d, 4)) for r, d in hits])
assert [r for r, _ in hits] == [1, 4], hits
print("PASS: sqlite-vec works here.")
