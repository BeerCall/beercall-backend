from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.beer_call_jobs import IdempotencyConflict, InvalidIdempotencyKey, enqueue_beer_call_job


def enqueue(db: Session, key: str) -> dict:
    return enqueue_beer_call_job(db, 1, 2, key, 48.8, 2.3, "Bar", b"photo", "jpg")


def test_invalid_key_is_domain_error_before_any_database_query() -> None:
    db = Mock(spec=Session)
    with pytest.raises(InvalidIdempotencyKey, match="Format Idempotency-Key invalide"):
        enqueue(db, "invalid")
    db.query.assert_not_called()
    assert issubclass(InvalidIdempotencyKey, ValueError)


def test_divergent_key_is_domain_conflict() -> None:
    db = Mock(spec=Session)
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(squad_id=3)
    with pytest.raises(IdempotencyConflict, match="Idempotency key déjà utilisée pour une requête différente"):
        enqueue(db, str(uuid.uuid4()))
    db.commit.assert_not_called()
    assert issubclass(IdempotencyConflict, ValueError)


def test_concurrent_divergent_key_rolls_back_and_raises_same_domain_conflict() -> None:
    db = Mock(spec=Session)
    filtered = db.query.return_value.filter.return_value
    filtered.first.side_effect = [None, None, SimpleNamespace(squad_id=3)]
    filtered.all.return_value = []
    db.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    with pytest.raises(IdempotencyConflict, match="Idempotency key déjà utilisée pour une requête différente"):
        enqueue(db, str(uuid.uuid4()))
    db.rollback.assert_called_once()
