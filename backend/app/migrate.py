"""Schema bootstrap + versioned SQL patches.

``python -m app.migrate`` is idempotent and runs before the API/worker start:
it creates missing tables from the SQLAlchemy metadata (fresh installs) and
applies every ``migrations/*.sql`` file once, tracked in ``schema_migrations``.
"""

from __future__ import annotations

import pathlib

from sqlalchemy import text

from .db import Base, engine
from . import models  # noqa: F401  (register metadata)


def migrate() -> list[str]:
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name VARCHAR(200) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        ))
    Base.metadata.create_all(engine)
    root = pathlib.Path(__file__).resolve().parent.parent / "migrations"
    if root.is_dir():
        with engine.begin() as conn:
            done = {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations"))}
        for path in sorted(root.glob("*.sql")):
            if path.name in done:
                continue
            with engine.begin() as conn:
                conn.execute(text(path.read_text()))
                conn.execute(text("INSERT INTO schema_migrations (name) VALUES (:name)"),
                             {"name": path.name})
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    names = migrate()
    print(f"schema ready; applied {len(names)} migration(s): {', '.join(names) or 'none'}")
