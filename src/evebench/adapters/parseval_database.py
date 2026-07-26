from __future__ import annotations

from pathlib import Path

from parseval.db_manager import dispose_all
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PROTECTED_DATABASES = {
    "mysql": {"information_schema", "mysql", "performance_schema", "sys"},
    "postgres": {"postgres", "template0", "template1"},
    "postgresql": {"postgres", "template0", "template1"},
}


def validate_connection_string(
    connection_string: str,
    dialect: str,
) -> str | None:
    try:
        url = make_url(connection_string)
        backend = url.get_backend_name()
    except Exception:
        return "ParSEval connection_string is not a valid SQLAlchemy URL"

    expected_backends = {
        "sqlite": {"sqlite"},
        "postgres": {"postgres", "postgresql"},
        "postgresql": {"postgres", "postgresql"},
        "mysql": {"mysql"},
    }
    expected = expected_backends.get(dialect.lower())
    if expected is None:
        return f"ParSEval does not support dialect {dialect!r}"
    if backend not in expected:
        return (
            f"Connection backend {backend!r} does not match "
            f"dialect {dialect!r}"
        )

    database = url.database
    if backend != "sqlite" and not database:
        return "ParSEval connection_string must name a disposable database"
    if database and database.lower() in PROTECTED_DATABASES.get(backend, set()):
        return f"Refusing to use protected database {database!r}"
    return None


def redact_connection(text_value: str, connection_string: str) -> str:
    try:
        redacted = make_url(connection_string).render_as_string(
            hide_password=True
        )
    except Exception:
        redacted = "[redacted connection string]"
    return text_value.replace(connection_string, redacted)


def cleanup_database(connection_string: str) -> None:
    """Drop the disposable database named by a ParSEval connection URL."""
    url = make_url(connection_string)
    backend = url.get_backend_name()
    database = url.database
    if not database or database == ":memory:":
        return

    if database.lower() in PROTECTED_DATABASES.get(backend, set()):
        raise ValueError(f"refusing to drop protected database {database!r}")

    if backend == "sqlite":
        dispose_all()
        database_path = Path(database)
        for path in (
            database_path,
            Path(f"{database_path}-shm"),
            Path(f"{database_path}-wal"),
        ):
            path.unlink(missing_ok=True)
        return

    if backend in {"postgres", "postgresql"}:
        admin_url = url.set(database="postgres")
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            quoted = engine.dialect.identifier_preparer.quote(database)
            with engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = :database "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))
        finally:
            engine.dispose()
        return

    if backend == "mysql":
        admin_url = url.set(database=None)
        engine = create_engine(admin_url)
        try:
            quoted = engine.dialect.identifier_preparer.quote(database)
            with engine.begin() as connection:
                connection.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))
        finally:
            engine.dispose()
        return

    raise ValueError(f"unsupported cleanup backend {backend!r}")
