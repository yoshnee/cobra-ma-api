"""
db.py — Postgres (Neon) connection via SQLAlchemy + pg8000.

The engine is created lazily so the module can be imported without a
database configured (e.g. in tests).
"""

import os
import ssl

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not configured")

        # pg8000 does not understand libpq query params (sslmode, channel_binding),
        # so drop them and always connect over verified TLS instead.
        url = make_url(database_url).set(drivername="postgresql+pg8000", query={})
        _engine = create_engine(
            url,
            connect_args={"ssl_context": ssl.create_default_context()},
            pool_size=3,
            # Neon suspends when idle and drops pooled connections; check each
            # connection before use so the first request after a pause succeeds.
            pool_pre_ping=True,
            pool_recycle=240,
        )
    return _engine


def cleanup():
    if _engine is not None:
        _engine.dispose()
