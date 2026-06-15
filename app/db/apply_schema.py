import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "agent.db"
SCHEMA_DIR = Path(__file__).parent / "schema"

if DB.exists():
    DB.unlink()

for f in sorted(SCHEMA_DIR.glob("*.sql")):
    print(f"Applying {f.name}...")
    with sqlite3.connect(DB) as conn:
        conn.executescript(f.read_text())

print(f"Created {DB}")
