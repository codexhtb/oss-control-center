"""SQLAlchemy session dependency for future domain repositories."""

from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import get_engine


def get_session() -> Iterator[Session]:
    factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session
