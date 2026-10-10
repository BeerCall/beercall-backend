import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.database import Base
from models.user import User
from models.squad import Squad
from models.apero import Apero, AperoStatus, AperoParticipant, ParticipationStatus
from services.apero_lifecycle import validate_distance, APERO_DURATION, MAX_START_DISTANCE_METERS, get_apero_for_squad, get_squad_member
from services.apero_lifecycle import schedule_apero
from schemas.apero import ScheduledAperoCreate
from sqlalchemy.orm import Session
from pathlib import Path
from unittest.mock import Mock
from services.apero_lifecycle import AperoStartConflict, start_scheduled_apero_orchestrated

def test_apero_lifecycle_methods(db_session):
    user = User(username="lifecycleuser", hashed_password="pw", avatar_config={})
    db_session.add(user)
    db_session.add(Squad(id=999, name="Lifecycle", invite_code="lifecycle"))
    db_session.commit()
    
    apero = Apero(
        squad_id=999,
        creator_id=user.id,
        location_name="Test Loc",
        latitude=48.8,
        longitude=2.3,
        status=AperoStatus.SCHEDULED,
        scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    db_session.add(apero)
    db_session.commit()
    
    dist = validate_distance(apero, 48.8, 2.3)
    assert dist < MAX_START_DISTANCE_METERS

    dist2 = validate_distance(apero, 48.9, 2.4)
    assert dist2 > MAX_START_DISTANCE_METERS


@pytest.fixture
def scheduled_context(db_session: Session) -> tuple[User, Squad]:
    user = User(username="scheduler", hashed_password="unused")
    squad = Squad(name="Schedule", invite_code="SCHED123")
    squad.members.append(user)
    db_session.add(squad)
    db_session.commit()
    return user, squad


def schedule_data(hours: int = 2, latitude: float = 48.8) -> ScheduledAperoCreate:
    return ScheduledAperoCreate(
        location_name="Bar", latitude=latitude, longitude=2.3,
        scheduled_for=datetime.now(timezone.utc) + timedelta(hours=hours),
    )


def test_schedule_commits_nominal(db_session: Session, scheduled_context: tuple[User, Squad]) -> None:
    user, squad = scheduled_context
    data = schedule_data()
    apero = schedule_apero(db_session, user, squad.id, data)
    apero_id = apero.id
    db_session.rollback()
    db_session.expire_all()
    stored = db_session.get(Apero, apero_id)
    assert stored.status == AperoStatus.SCHEDULED
    assert stored.creator_id == user.id
    assert stored.location_name == data.location_name
    assert stored.scheduled_for.replace(tzinfo=timezone.utc) == data.scheduled_for


def test_schedule_rejects_past(db_session: Session, scheduled_context: tuple[User, Squad]) -> None:
    user, squad = scheduled_context
    with pytest.raises(ValueError, match="^La date doit être dans le futur$"):
        schedule_apero(db_session, user, squad.id, schedule_data(-1))
    assert db_session.query(Apero).count() == 0


@pytest.mark.parametrize("status", [AperoStatus.SCHEDULED, AperoStatus.ACTIVE])
def test_schedule_rejects_nearby_conflict(db_session: Session, scheduled_context: tuple[User, Squad], status: AperoStatus) -> None:
    user, squad = scheduled_context
    data = schedule_data()
    db_session.add(Apero(
        squad_id=squad.id, creator_id=user.id, latitude=data.latitude, longitude=data.longitude,
        status=status, scheduled_for=data.scheduled_for, started_at=data.scheduled_for,
    ))
    db_session.commit()
    label = "programmé" if status == AperoStatus.SCHEDULED else "en cours"
    with pytest.raises(ValueError, match=f"^Un apéro est déjà {label} à cet endroit dans ce créneau horaire$"):
        schedule_apero(db_session, user, squad.id, data)
    assert db_session.query(Apero).count() == 1


@pytest.mark.parametrize("hours, latitude", [(6, 48.8), (2, 48.9)])
def test_schedule_allows_nonconflicting_time_or_location(db_session: Session, scheduled_context: tuple[User, Squad], hours: int, latitude: float) -> None:
    user, squad = scheduled_context
    first = schedule_apero(db_session, user, squad.id, schedule_data())
    second = schedule_apero(db_session, user, squad.id, schedule_data(hours, latitude))
    assert first.id != second.id


def test_schedule_rejects_nonmember(db_session: Session, scheduled_context: tuple[User, Squad]) -> None:
    _, squad = scheduled_context
    outsider = User(username="outsider", hashed_password="unused")
    db_session.add(outsider)
    db_session.commit()
    with pytest.raises(PermissionError, match="^Tu ne fais pas partie de cette Squad$"):
        schedule_apero(db_session, outsider, squad.id, schedule_data())
    assert db_session.query(Apero).count() == 0


def test_schedule_rejects_missing_squad(db_session: Session, scheduled_context: tuple[User, Squad]) -> None:
    user, squad = scheduled_context
    with pytest.raises(LookupError, match="^Squad introuvable$"):
        schedule_apero(db_session, user, squad.id + 1, schedule_data())


def test_start_orchestration_commits_rewards_and_photo(db_session: Session, scheduled_context: tuple[User, Squad], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user, squad = scheduled_context
    apero = schedule_apero(db_session, user, squad.id, schedule_data())
    monkeypatch.chdir(tmp_path)
    detector = Mock(return_value=True)
    started = start_scheduled_apero_orchestrated(db_session, apero, user.id, b"photo", detector, "jpg")
    detector.assert_called_once_with(b"photo")
    assert started.status == AperoStatus.ACTIVE
    assert Path(started.photo_path).read_bytes() == b"photo"
    assert started.ended_at - started.started_at == APERO_DURATION
    db_session.rollback()
    db_session.expire_all()
    assert user.capsules == 150
    assert user.consecutive_joins == 1
    assert db_session.query(AperoParticipant).count() == 1


def test_start_orchestration_rejects_ai_without_photo(db_session: Session, scheduled_context: tuple[User, Squad], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user, squad = scheduled_context
    apero = schedule_apero(db_session, user, squad.id, schedule_data())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="^Pas de boisson, pas de démarrage$"):
        start_scheduled_apero_orchestrated(db_session, apero, user.id, b"photo", lambda _: False, "jpg")
    db_session.expire_all()
    assert user.capsules == 85
    assert user.ia_fraud_count == 1
    assert apero.status == AperoStatus.SCHEDULED
    assert db_session.query(AperoParticipant).count() == 0
    assert not list(tmp_path.rglob("*.jpg"))


def test_start_orchestration_conflict_removes_only_new_photo(db_session: Session, scheduled_context: tuple[User, Squad], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user, squad = scheduled_context
    apero = schedule_apero(db_session, user, squad.id, schedule_data())
    monkeypatch.chdir(tmp_path)
    started = start_scheduled_apero_orchestrated(db_session, apero, user.id, b"photo", lambda _: True, "jpg")
    original_path = Path(started.photo_path)
    with pytest.raises(AperoStartConflict, match="^Cet apéro a déjà été démarré$"):
        start_scheduled_apero_orchestrated(db_session, apero, user.id, b"second", lambda _: True, "jpg")
    assert list(tmp_path.rglob("*.jpg")) == [tmp_path / original_path]
    assert user.capsules == 150
    assert db_session.query(AperoParticipant).count() == 1


@pytest.mark.parametrize("failure", ["rewards", "commit"])
def test_start_orchestration_rolls_back_and_cleans_photo(db_session: Session, scheduled_context: tuple[User, Squad], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str) -> None:
    user, squad = scheduled_context
    apero = schedule_apero(db_session, user, squad.id, schedule_data())
    monkeypatch.chdir(tmp_path)
    fail = Mock(side_effect=RuntimeError("storage failure"))
    if failure == "rewards":
        monkeypatch.setattr("services.apero_lifecycle.apply_apero_start_rewards", fail)
    else:
        monkeypatch.setattr(db_session, "commit", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        start_scheduled_apero_orchestrated(db_session, apero, user.id, b"photo", lambda _: True, "jpg")
    assert apero.status == AperoStatus.SCHEDULED
    assert user.capsules == 100
    assert db_session.query(AperoParticipant).count() == 0
    assert not list(tmp_path.rglob("*.jpg"))
