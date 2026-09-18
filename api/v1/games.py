from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.security import get_current_user
from db.database import get_db
from models.apero import Apero, AperoStatus, ParticipationStatus
from models.user import User
from services.minigames.registry import GAME_REGISTRY, get_game_instance

router = APIRouter()


def authorized_apero(apero_id: int, db: Session, current_user: User):
    apero = db.query(Apero).get(apero_id)
    if not apero:
        raise HTTPException(status_code=404, detail="Apéro introuvable")
    if apero.status != AperoStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Le jeu exige un apéro actif")
    if not any(member.id == current_user.id for member in apero.squad.members):
        raise HTTPException(status_code=403, detail="Accès refusé")
    if not any(p.user_id == current_user.id and p.status == ParticipationStatus.JOINED for p in apero.participants):
        raise HTTPException(status_code=403, detail="Tu dois être présent au Bar")
    return apero


@router.post("/{apero_id}/game/start")
def start_game_session(apero_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    apero = authorized_apero(apero_id, db, current_user)
    if apero.current_game_id:
        return get_game_instance(apero.current_game_id).get_sdui_payload(apero, db)
    active_participants = [p.user_id for p in apero.participants if p.status == ParticipationStatus.JOINED]
    if len(active_participants) < 2:
        raise HTTPException(status_code=400, detail="Il faut au moins 2 personnes au Bar pour lancer le jeu !")
    apero.current_game_id = "TURN_TRANSITION"
    apero.current_game_state = {"player_ids": active_participants, "turn_index": 0}
    db.commit()
    return get_game_instance("TURN_TRANSITION").get_sdui_payload(apero, db)


@router.get("/{apero_id}/game/state")
def get_game_state(apero_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    apero = authorized_apero(apero_id, db, current_user)
    game = GAME_REGISTRY.get(apero.current_game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Pas de jeu en cours")
    return game.get_sdui_payload(apero, db)


@router.post("/{apero_id}/game/action")
def post_game_action(apero_id: int, action: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    apero = authorized_apero(apero_id, db, current_user)
    current_game = GAME_REGISTRY.get(apero.current_game_id)
    if not current_game:
        raise HTTPException(status_code=404, detail="Pas de jeu en cours")
    current_game.handle_action(apero, db, action)
    db.commit()
    new_game = GAME_REGISTRY.get(apero.current_game_id)
    if not new_game:
        raise HTTPException(status_code=500, detail="Le jeu suivant n'est pas enregistré")
    return new_game.get_sdui_payload(apero, db)
