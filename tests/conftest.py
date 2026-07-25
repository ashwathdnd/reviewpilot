from __future__ import annotations

import pytest
import pytest_asyncio

from app.config import get_test_settings
from app.database import get_database
from app.models import Base


@pytest_asyncio.fixture
async def db_session():
    settings = get_test_settings()
    database = get_database(settings)
    Base.metadata.create_all(bind=database.engine)
    session = database.get_session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=database.engine)


@pytest.fixture
def settings():
    return get_test_settings()
