from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.database import get_db
from models.apero import Apero, ParticipationStatus
from services.minigames.registry import GAME_REGISTRY, get_game_instance

router = APIRouter()


@router.post("/{apero_id}/game/start")
def start_game_session(apero_id: int, db: Session = Depends(get_db)):
    apero = db.query(Apero).get(apero_id)

    # 1. Si la partie existe déjà, on ne fait rien, on renvoie l'état
    if apero.current_game_id:
        return get_game_instance(apero.current_game_id).get_sdui_payload(apero, db)

    # 2. On recrute les gens présents dans le Bar
    active_participants = [
        p.user_id for p in apero.participants
        if p.status == ParticipationStatus.JOINED  # Enum défini dans apero.py
    ]

    if len(active_participants) < 2:
        raise HTTPException(status_code=400, detail="Il faut au moins 2 personnes au Bar pour lancer le jeu !")

    # 3. Initialisation de la machine à états (MODE PICOLO)
    apero.current_game_id = "TURN_TRANSITION"
    apero.current_game_state = {
        "player_ids": active_participants,
        "turn_index": 0  # On commence au premier joueur
    }

    db.commit()
    return get_game_instance("TURN_TRANSITION").get_sdui_payload(apero, db)


@router.get("/{apero_id}/game/state")
def get_game_state(apero_id: int, db: Session = Depends(get_db)):
    apero = db.query(Apero).get(apero_id)
    game = GAME_REGISTRY.get(apero.current_game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Pas de jeu en cours")
    return game.get_sdui_payload(apero, db)


@router.post("/{apero_id}/game/action")
def post_game_action(apero_id: int, action: Dict[str, Any], db: Session = Depends(get_db)):
    apero = db.query(Apero).get(apero_id)

    # 1. On charge le jeu actuel (ex: TURN_TRANSITION)
    current_game = GAME_REGISTRY.get(apero.current_game_id)
    if not current_game:
        raise HTTPException(status_code=404, detail="Pas de jeu en cours")

    # 2. Le jeu traite l'action (C'est ICI que apero.current_game_id change vers AVATAR_ROULETTE)
    current_game.handle_action(apero, db, action)
    db.commit()

    # 3. LE CORRECTIF : On recharge le NOUVEAU jeu depuis le registre !
    new_game = GAME_REGISTRY.get(apero.current_game_id)
    if not new_game:
        raise HTTPException(status_code=500, detail="Le jeu suivant n'est pas enregistré")

    # 4. On renvoie l'écran généré par ce NOUVEAU jeu
    return new_game.get_sdui_payload(apero, db)
