from __future__ import annotations

import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, pool
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import Base

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        connect_args = {"check_same_thread": False} if self.settings.database_url.startswith("sqlite") else {}
        # In-memory SQLite needs a static pool so multiple connections share one DB.
        kw = {}
        if self.settings.database_url == "sqlite:///:memory:":
            kw["poolclass"] = pool.StaticPool
        self.engine = create_engine(
            self.settings.database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
            **kw,
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def init_tables(self) -> None:
        Base.metadata.create_all(bind=self.engine)
        logger.info("Database tables initialized.")

    def get_session(self) -> Session:
        return self.SessionLocal()

    @contextmanager
    def session(self):
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


_db: Database | None = None


def get_database(settings: Settings | None = None) -> Database:
    global _db
    if _db is None or settings is not None:
        _db = Database(settings)
    return _db


def init_db(settings: Settings | None = None) -> None:
    get_database(settings).init_tables()
