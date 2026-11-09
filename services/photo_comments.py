import logging

from sqlalchemy.orm import Session

from models.apero import AperoParticipant, ParticipationStatus
from models.photo_comment import ProofPhotoComment
from models.realtime_event import RealtimeEvent
from models.user import User
from schemas.photo_comment import PhotoCommentOut
from services.apero_lifecycle import get_apero_for_squad, get_squad_member
from services.notifications import send_push_notifications

logger = logging.getLogger(__name__)


def _member_squad(db: Session, squad_id: int, current_user: User):
    try:
        return get_squad_member(db, squad_id, current_user.id)
    except ValueError as exc:
        raise LookupError(str(exc)) from exc


def _photo_participant(db: Session, beer_call_id: int, photo_owner_id: int) -> AperoParticipant:
    participant = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == beer_call_id,
        AperoParticipant.user_id == photo_owner_id,
        AperoParticipant.status == ParticipationStatus.JOINED,
        AperoParticipant.photo_path.isnot(None),
    ).first()
    if not participant:
        raise LookupError("Preuve photo introuvable pour ce membre")
    return participant


def _serialize(comment: ProofPhotoComment, author_username: str, current_user: User) -> PhotoCommentOut:
    return PhotoCommentOut(
        id=comment.id,
        author_id=comment.author_id,
        author_username=author_username,
        content=comment.content,
        created_at=comment.created_at,
        can_delete=comment.author_id == current_user.id,
    )


def list_photo_comments(
    db: Session,
    squad_id: int,
    beer_call_id: int,
    photo_owner_id: int,
    current_user: User,
) -> list[PhotoCommentOut]:
    _member_squad(db, squad_id, current_user)
    get_apero_for_squad(db, squad_id, beer_call_id)
    _photo_participant(db, beer_call_id, photo_owner_id)
    rows = (
        db.query(ProofPhotoComment, User.username)
        .join(User, User.id == ProofPhotoComment.author_id)
        .filter(
            ProofPhotoComment.apero_id == beer_call_id,
            ProofPhotoComment.photo_owner_id == photo_owner_id,
        )
        .order_by(ProofPhotoComment.created_at.asc(), ProofPhotoComment.id.asc())
        .all()
    )
    return [_serialize(comment, username, current_user) for comment, username in rows]


def create_photo_comment(
    db: Session,
    squad_id: int,
    beer_call_id: int,
    photo_owner_id: int,
    current_user: User,
    content: str,
) -> PhotoCommentOut:
    _member_squad(db, squad_id, current_user)
    get_apero_for_squad(db, squad_id, beer_call_id)
    _photo_participant(db, beer_call_id, photo_owner_id)

    trimmed = content.strip()
    if not trimmed:
        raise ValueError("Le commentaire ne peut pas être vide.")
    if len(trimmed) > 500:
        raise ValueError("Le commentaire ne peut pas dépasser 500 caractères.")

    comment = ProofPhotoComment(
        apero_id=beer_call_id,
        photo_owner_id=photo_owner_id,
        author_id=current_user.id,
        content=trimmed,
    )
    try:
        db.add(comment)
        db.flush()
        db.add(RealtimeEvent(
            squad_id=squad_id,
            payload={
                "type": "PHOTO_COMMENT",
                "action": "CREATE",
                "beer_call_id": beer_call_id,
                "photo_owner_id": photo_owner_id,
                "comment_id": comment.id,
            },
        ))
        db.commit()
        db.refresh(comment)
    except Exception:
        db.rollback()
        raise

    if current_user.id != photo_owner_id:
        owner = db.get(User, photo_owner_id)
        if owner and owner.push_token:
            excerpt = trimmed if len(trimmed) <= 80 else f"{trimmed[:77]}..."
            try:
                send_push_notifications(
                    tokens=[owner.push_token],
                    title="💬 Nouveau commentaire sur ta preuve photo",
                    body=f"{current_user.username} a commenté : \"{excerpt}\"",
                    data={
                        "type": "photo_comment",
                        "beer_call_id": beer_call_id,
                        "photo_owner_id": photo_owner_id,
                        "comment_id": comment.id,
                    },
                )
            except Exception:
                logger.exception("Échec de la notification de commentaire photo %s", comment.id)

    return _serialize(comment, current_user.username, current_user)


def delete_photo_comment(
    db: Session,
    squad_id: int,
    beer_call_id: int,
    comment_id: int,
    current_user: User,
) -> None:
    _member_squad(db, squad_id, current_user)
    get_apero_for_squad(db, squad_id, beer_call_id)
    comment = db.query(ProofPhotoComment).filter(
        ProofPhotoComment.id == comment_id,
        ProofPhotoComment.apero_id == beer_call_id,
    ).first()
    if not comment:
        raise LookupError("Commentaire introuvable")
    if comment.author_id != current_user.id:
        raise PermissionError("Tu ne peux supprimer que tes propres commentaires")
    try:
        db.add(RealtimeEvent(
            squad_id=squad_id,
            payload={
                "type": "PHOTO_COMMENT",
                "action": "DELETE",
                "beer_call_id": beer_call_id,
                "photo_owner_id": comment.photo_owner_id,
                "comment_id": comment.id,
            },
        ))
        db.delete(comment)
        db.commit()
    except Exception:
        db.rollback()
        raise
