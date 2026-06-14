import sqlite3
from pathlib import Path

DB = Path("agent.db")
SCHEMA_DIR = Path("app/db/schema")

if DB.exists():
    DB.unlink()
    print(f"Removed existing {DB}")

for f in sorted(SCHEMA_DIR.glob("*.sql")):
    print(f"Applying {f.name}...")
    with sqlite3.connect(DB) as conn:
        conn.executescript(f.read_text())

print(f"Created {DB.resolve()}")
