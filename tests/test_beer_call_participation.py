from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.apero import Apero, AperoParticipant, AperoStatus, ParticipationStatus
from models.squad import Squad
from models.user import User
from services.beer_call_participation import ParticipationConflict, join_beer_call
from services.beer_call_participation import decline_beer_call
from models.gamification import Badge


@pytest.fixture
def participation_context(db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[User, Squad, Apero]:
    creator = User(username="creator", hashed_password="unused")
    user = User(username="joiner", hashed_password="unused")
    squad = Squad(name="Squad", invite_code="JOIN1234", members=[creator, user])
    db_session.add(squad)
    db_session.flush()
    apero = Apero(squad_id=squad.id, creator_id=creator.id, status=AperoStatus.ACTIVE, latitude=48.8, longitude=2.3, location_name="Bar")
    db_session.add(apero)
    db_session.commit()
    monkeypatch.chdir(tmp_path)
    return user, squad, apero


def test_join_commits_photo_participant_and_rewards(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    detector = Mock(return_value=True)
    bonus = join_beer_call(db_session, user, squad.id, apero.id, 48.8, 2.3, b"photo", detector)
    detector.assert_called_once_with(b"photo")
    db_session.rollback()
    db_session.expire_all()
    participant = db_session.query(AperoParticipant).one()
    assert participant.user_id == user.id
    assert participant.status == ParticipationStatus.JOINED
    assert Path(participant.photo_path).read_bytes() == b"photo"
    assert user.capsules == 100 + bonus
    assert user.consecutive_joins == 1
    assert user.consecutive_piscine == 0


def test_join_controls_before_inference(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    detector = Mock(return_value=True)
    with pytest.raises(LookupError, match="Apéro introuvable dans cette squad"):
        join_beer_call(db_session, user, squad.id, apero.id + 1, 48.8, 2.3, b"photo", detector)
    with pytest.raises(LookupError, match="Apéro introuvable dans cette squad"):
        join_beer_call(db_session, user, squad.id + 1, apero.id, 48.8, 2.3, b"photo", detector)
    squad.members.remove(user)
    db_session.commit()
    with pytest.raises(PermissionError, match="Tu ne fais pas partie de cette Squad"):
        join_beer_call(db_session, user, squad.id, apero.id, 48.8, 2.3, b"photo", detector)
    squad.members.append(user)
    apero.status = AperoStatus.ENDED
    db_session.commit()
    with pytest.raises(ParticipationConflict, match="Cet apéro n'est pas actif"):
        join_beer_call(db_session, user, squad.id, apero.id, 48.8, 2.3, b"photo", detector)
    db_session.add(AperoParticipant(apero_id=apero.id, user_id=user.id, status=ParticipationStatus.DECLINED))
    db_session.commit()
    with pytest.raises(ValueError, match="Tu as déjà répondu à cet appel de la bière !"):
        join_beer_call(db_session, user, squad.id, apero.id, 48.8, 2.3, b"photo", detector)
    detector.assert_not_called()
    assert user.capsules == 100
    assert not list(Path.cwd().rglob("*.jpg"))


def test_join_distance_penalty_commits_without_inference(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    detector = Mock(return_value=True)
    with pytest.raises(PermissionError, match="Triche détectée !"):
        join_beer_call(db_session, user, squad.id, apero.id, 48.9, 2.3, b"photo", detector)
    detector.assert_not_called()
    db_session.rollback()
    db_session.expire_all()
    assert user.capsules == 85
    assert user.ia_fraud_count == 1
    assert db_session.query(AperoParticipant).count() == 0
    assert not list(Path.cwd().rglob("*.jpg"))


def test_join_ai_penalty_commits_without_photo(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    with pytest.raises(ValueError, match="Pas de boisson, pas de Bar ! -15 Caps"):
        join_beer_call(db_session, user, squad.id, apero.id, 48.8, 2.3, b"photo", lambda _: False)
    db_session.rollback()
    db_session.expire_all()
    assert user.capsules == 85
    assert user.ia_fraud_count == 1
    assert db_session.query(AperoParticipant).count() == 0
    assert not list(Path.cwd().rglob("*.jpg"))


def test_join_integrity_conflict_rolls_back_rewards_and_photo(db_session: Session, participation_context: tuple[User, Squad, Apero], monkeypatch: pytest.MonkeyPatch) -> None:
    user, squad, apero = participation_context
    monkeypatch.setattr(db_session, "commit", Mock(side_effect=IntegrityError("insert", {}, Exception("duplicate"))))
    with pytest.raises(ParticipationConflict, match="Tu as déjà rejoint cet apéro !"):
        join_beer_call(db_session, user, squad.id, apero.id, 48.8, 2.3, b"photo", lambda _: True)
    assert user.capsules == 100
    assert user.consecutive_joins == 0
    assert db_session.query(AperoParticipant).count() == 0
    assert not list(Path.cwd().rglob("*.jpg"))


def test_join_unexpected_failure_rolls_back_and_cleans_photo(db_session: Session, participation_context: tuple[User, Squad, Apero], monkeypatch: pytest.MonkeyPatch) -> None:
    user, squad, apero = participation_context
    monkeypatch.setattr("services.beer_call_participation.apply_beer_call_join_rewards", Mock(side_effect=RuntimeError("reward failure")))
    with pytest.raises(RuntimeError, match="reward failure"):
        join_beer_call(db_session, user, squad.id, apero.id, 48.8, 2.3, b"photo", lambda _: True)
    assert user.capsules == 100
    assert db_session.query(AperoParticipant).count() == 0
    assert not list(Path.cwd().rglob("*.jpg"))


def test_decline_commits_participant_capsules_and_streaks(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    user.consecutive_joins = 3
    db_session.commit()
    decline_beer_call(db_session, user, squad.id, apero.id, "Tired")
    db_session.rollback()
    db_session.expire_all()
    participant = db_session.query(AperoParticipant).one()
    assert participant.user_id == user.id
    assert participant.status == ParticipationStatus.DECLINED
    assert participant.excuse == "Tired"
    assert user.capsules == 115
    assert (user.consecutive_joins, user.consecutive_piscine, user.consecutive_declines) == (0, 1, 1)


def test_decline_rejects_duplicate(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    decline_beer_call(db_session, user, squad.id, apero.id, None)
    with pytest.raises(ValueError, match="Tu as déjà répondu à cet appel de la bière !"):
        decline_beer_call(db_session, user, squad.id, apero.id, "Again")
    assert user.capsules == 115
    assert db_session.query(AperoParticipant).count() == 1


def test_decline_rejects_nonmember(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    squad.members.remove(user)
    db_session.commit()
    with pytest.raises(PermissionError, match="Tu ne fais pas partie de cette Squad"):
        decline_beer_call(db_session, user, squad.id, apero.id, None)
    assert user.capsules == 100
    assert db_session.query(AperoParticipant).count() == 0


def test_decline_rejects_missing_or_inactive_apero(db_session: Session, participation_context: tuple[User, Squad, Apero]) -> None:
    user, squad, apero = participation_context
    with pytest.raises(LookupError, match="Apéro introuvable dans cette squad"):
        decline_beer_call(db_session, user, squad.id, apero.id + 1, None)
    apero.status = AperoStatus.ENDED
    db_session.commit()
    with pytest.raises(ParticipationConflict, match="Cet apéro n'est pas actif"):
        decline_beer_call(db_session, user, squad.id, apero.id, None)
    assert user.capsules == 100
    assert db_session.query(AperoParticipant).count() == 0


@pytest.mark.parametrize("excuse, expected_badges, declines", [("Tired", {"NAGEUR", "CASANIER"}, 5), (None, {"NAGEUR"}, 0)])
def test_decline_awards_badges_and_resets_missing_excuse(db_session: Session, participation_context: tuple[User, Squad, Apero], excuse: str | None, expected_badges: set[str], declines: int) -> None:
    user, squad, apero = participation_context
    db_session.add_all([Badge(id=badge, name=badge, description=badge) for badge in ("NAGEUR", "CASANIER")])
    user.consecutive_piscine = 4
    user.consecutive_declines = 4
    db_session.commit()
    decline_beer_call(db_session, user, squad.id, apero.id, excuse)
    db_session.expire_all()
    assert {badge.id for badge in user.badges} == expected_badges
    assert user.consecutive_declines == declines
    assert user.consecutive_piscine == 5


def test_decline_commit_failure_rolls_back_rewards(db_session: Session, participation_context: tuple[User, Squad, Apero], monkeypatch: pytest.MonkeyPatch) -> None:
    user, squad, apero = participation_context
    monkeypatch.setattr(db_session, "commit", Mock(side_effect=RuntimeError("commit failure")))
    with pytest.raises(RuntimeError, match="commit failure"):
        decline_beer_call(db_session, user, squad.id, apero.id, "Tired")
    assert user.capsules == 100
    assert user.consecutive_piscine == 0
    assert db_session.query(AperoParticipant).count() == 0
