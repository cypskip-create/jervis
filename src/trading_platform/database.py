"""Database engine/session setup. Credentials are supplied only by runtime config."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import Settings


def make_engine(settings: Settings) -> Engine:
    """Create a SQLAlchemy engine without logging the configured database URL."""

    options: dict[str, object] = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(settings.database_url, **options)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session and guarantee close; transaction boundaries stay explicit."""

    session = factory()
    try:
        yield session
    finally:
        session.close()
