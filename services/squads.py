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
