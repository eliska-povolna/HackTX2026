"""Verify TIMESCALE_SERVICE_URL primary + in-memory fallback. No secrets printed."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import db as db_mod  # noqa: E402


def main() -> None:
    has_ts = bool((os.getenv("TIMESCALE_SERVICE_URL") or "").strip())
    has_db = bool((os.getenv("DATABASE_URL") or "").strip())
    print("TIMESCALE_SERVICE_URL_SET=", has_ts)
    print("DATABASE_URL_SET=", has_db)
    print("PRIMARY_RESOLVED=", "TIMESCALE" if has_ts else ("DATABASE_URL" if has_db else "NONE"))

    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.db_available = False
    ok = db_mod.init_db()
    print("INIT_WITH_ENV=", ok, "db_available=", db_mod.db_available)
    if ok and db_mod.get_engine() is not None:
        from sqlalchemy import text

        with db_mod.get_engine().connect() as conn:
            n = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalar()
            print("PUBLIC_TABLES=", n)

    # fallback without either URL
    saved_ts = os.environ.pop("TIMESCALE_SERVICE_URL", None)
    saved_db = os.environ.pop("DATABASE_URL", None)
    try:
        db_mod._engine = None
        db_mod._SessionLocal = None
        db_mod.db_available = False
        ok2 = db_mod.init_db()
        print("INIT_WITHOUT_URL=", ok2, "db_available=", db_mod.db_available)
        assert ok2 is False
    finally:
        if saved_ts is not None:
            os.environ["TIMESCALE_SERVICE_URL"] = saved_ts
        if saved_db is not None:
            os.environ["DATABASE_URL"] = saved_db

    cors = os.getenv("CORS_ORIGINS") or "http://localhost:5173"
    origins = [o.strip() for o in cors.split(",") if o.strip()]
    print("CORS_ORIGINS_COUNT=", len(origins))
    print("CORS_ORIGINS=", origins)
    print("DB_CONFIG_VERIFY PASS")


if __name__ == "__main__":
    main()
