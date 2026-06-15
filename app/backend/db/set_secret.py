"""set_secret.py — small CLI to set/get/delete encrypted secrets.

Examples
--------
    # Set the HuggingFace token (the most common use case)
    python -m app.backend.db.set_secret hf.token hf_xxxxxxxxxxxxx

    # Get the current value (for verification)
    python -m app.backend.db.set_secret --get hf.token

    # Delete a secret
    python -m app.backend.db.set_secret --delete hf.token

    # List all secret keys (values are NEVER printed)
    python -m app.backend.db.set_secret --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.backend.db.connection import Database
from app.backend.db.facade import BridgeRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage encrypted secrets in agent.db.")
    parser.add_argument("key", nargs="?", help="Secret key (e.g. hf.token)")
    parser.add_argument("value", nargs="?", help="Secret value (omit for --get)")
    parser.add_argument("--get", action="store_true", help="Read and print the value")
    parser.add_argument("--delete", action="store_true", help="Delete the secret")
    parser.add_argument("--list", action="store_true", help="List all secret keys")
    parser.add_argument(
        "--check", action="store_true",
        help="Check whether a key exists (prints 1/0, useful for the UI)",
    )
    args = parser.parse_args(argv)

    db = Database()
    bridge = BridgeRepository(db)
    try:
        if args.list:
            rows = db.conn.execute("SELECT key FROM secrets ORDER BY key").fetchall()
            for r in rows:
                print(r["key"])
            return 0
        if not args.key:
            parser.error("provide a key (or use --list)")
        if args.get:
            value = bridge.secrets.get(args.key)
            if value is None:
                print(f"(not set)", file=sys.stderr)
                return 2
            print(value)
            return 0
        if args.delete:
            bridge.secrets.delete(args.key)
            bridge.audit.log(actor="cli", action="secret.delete", target_id=args.key)
            print(f"deleted {args.key}")
            return 0
        if args.check:
            print(1 if bridge.secrets.has(args.key) else 0)
            return 0
        if not args.value:
            parser.error("provide a value (or use --get / --delete / --list)")
        bridge.secrets.set(args.key, args.value)
        bridge.audit.log(
            actor="cli", action="secret.set",
            target_id=args.key,
            details={"length": len(args.value)},
        )
        print(f"set {args.key} (length={len(args.value)})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
