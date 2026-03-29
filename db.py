"""
db.py — Cloud SQL connection via Python Connector + IAM auth.
"""

import os
from google.cloud.sql.connector import Connector
from sqlalchemy import create_engine

_connector = Connector()

INSTANCE_CONNECTION_NAME = os.environ.get(
    "INSTANCE_CONNECTION_NAME", "kerjasama-dev:europe-west2:kerjasama-db"
)
DB_NAME = os.environ.get("DB_NAME", "cobra-ma")
DB_USER = os.environ.get("DB_USER", "cobra-ma-api@kerjasama-dev.iam")


def _get_conn():
    return _connector.connect(
        INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=DB_USER,
        db=DB_NAME,
        enable_iam_auth=True,
    )


_engine = create_engine("postgresql+pg8000://", creator=_get_conn, pool_size=3)


def get_engine():
    return _engine


def cleanup():
    _connector.close()
