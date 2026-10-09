import os
import pytest

# Ensure we use the test database before anything else is imported
if "TEST_DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "sqlite:///./test_db.sqlite"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typing import Generator
import asyncio
from db.database import Base
from fastapi.testclient import TestClient

from main import app

# Utilisation d'une base SQLite en mǸmoire pour les tests par dǸfaut
# Cela garantit que les tests s'exǸcutent trs vite et sont isolǸs.
# Note : SQLite ne supporte pas l'async natif simplement avec sqlalchemy standard,
# on utilise donc un driver synchrone pour les tests.

SQLALCHEMY_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///./test_db.sqlite")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    if os.path.exists("./test_db.sqlite"):
        try:
            os.remove("./test_db.sqlite")
        except Exception:
            pass

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="function")
def db_session() -> Generator:
    # Nettoyer les donnǸes entre chaque test
    for table in reversed(Base.metadata.sorted_tables):
        with engine.begin() as conn:
            conn.execute(table.delete())
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="function", autouse=True)
def override_get_db(db_session):
    from db.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    yield
    app.dependency_overrides.pop(get_db, None)

@pytest.fixture(scope="function")
def client() -> Generator:
    with TestClient(app) as c:
        yield c
