from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from core.config import config


engine = create_engine(config.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class PostgreSQLDB:
    """Temporary adapter for repositories retained from the legacy layout."""

    Base = Base

    @property
    def session(self):
        return SessionLocal()


postgresql = PostgreSQLDB()
