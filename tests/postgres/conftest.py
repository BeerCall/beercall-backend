import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture(scope="session")
def postgres_url():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if "beercall_test" not in url:
        raise ValueError("TEST_DATABASE_URL must contain 'beercall_test' to avoid destroying production data")
    if "sqlite" in url:
        pytest.skip("TEST_DATABASE_URL is a sqlite URL, skipping postgres tests")
    return url

@pytest.fixture(scope="session")
def postgres_engine(postgres_url):
    engine = create_engine(postgres_url)
    yield engine
    engine.dispose()
