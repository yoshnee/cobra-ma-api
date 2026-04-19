"""
db.py — Cloud SQL connection via Python Connector + IAM auth.

Connector and engine are created lazily so the module can be imported
without GCP credentials (e.g. in tests).
"""

import os

from sqlalchemy import create_engine

INSTANCE_CONNECTION_NAME = os.environ.get(
    "INSTANCE_CONNECTION_NAME", "kerjasama-dev:europe-west2:kerjasama-db"
)
DB_NAME = os.environ.get("DB_NAME", "cobra-ma")
DB_USER = os.environ.get("DB_USER", "cobra-ma-api@kerjasama-dev.iam")

_connector = None
_engine = None


def _get_connector():
    global _connector
    if _connector is None:
        from google.cloud.sql.connector import Connector
        _connector = Connector()
    return _connector


def _get_conn():
    return _get_connector().connect(
        INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=DB_USER,
        db=DB_NAME,
        enable_iam_auth=True,
    )


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine("postgresql+pg8000://", creator=_get_conn, pool_size=3)
    return _engine


def cleanup():
    if _connector is not None:
        _connector.close()
