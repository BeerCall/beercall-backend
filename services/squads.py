import uuid

from sqlalchemy.orm import Session

from models.squad import Squad
from models.user import User
from schemas.squad import SquadCreate


def create_squad(db: Session, user: User, data: SquadCreate) -> Squad:
    squad = Squad(
        name=data.name,
        icon=data.icon,
        color=data.color,
        invite_code=str(uuid.uuid4())[:8].upper(),
    )
    squad.members.append(user)
    db.add(squad)
    db.commit()
    db.refresh(squad)
    return squad


def join_squad(db: Session, user: User, invite_code: str) -> Squad:
    squad = db.query(Squad).filter(Squad.invite_code == invite_code.upper()).first()
    if not squad:
        raise LookupError("Code d'invitation invalide.")
    if user in squad.members:
        raise ValueError("Tu fais déjà partie de cette Squad !")
    squad.members.append(user)
    db.commit()
    db.refresh(squad)
    return squad
