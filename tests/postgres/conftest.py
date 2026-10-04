import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine.url import make_url

@pytest.fixture(scope="session")
def postgres_url():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    parsed_url = make_url(url)
    if "sqlite" in parsed_url.drivername:
        pytest.skip("TEST_DATABASE_URL is a sqlite URL, skipping postgres tests")
    if not (parsed_url.database == "beercall_test" or parsed_url.database.endswith("_test")):
        raise ValueError("TEST_DATABASE_URL database name must end with '_test' to avoid destroying production data")
    return url

@pytest.fixture(scope="session")
def postgres_engine(postgres_url):
    engine = create_engine(postgres_url)
    yield engine
    engine.dispose()
