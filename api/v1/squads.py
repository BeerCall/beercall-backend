import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi import UploadFile, File, Form
from sqlalchemy.orm import Session

from core.security import get_current_user
from db.database import get_db
from models.apero import Apero
from models.apero import AperoParticipant, ParticipationStatus
from models.squad import Squad
from models.user import User
from schemas.apero import AperoDecline, WorldsResponse
from schemas.squad import SquadCreate, SquadDetailsResponse
from schemas.squad import SquadResponse, SquadJoin
from services.gamification import handle_ia_fraud, award_badge
from services.photo_validation import is_drink_detected

router = APIRouter()


# POST : Créer une Squad
@router.post("/", response_model=SquadResponse)
def create_squad(
        squad_data: SquadCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # Création de la squad avec un code d'invitation unique
    new_squad = Squad(
        name=squad_data.name,
        icon=squad_data.icon,
        color=squad_data.color,
        invite_code=str(uuid.uuid4())[:8].upper()
    )

    # On ajoute le créateur comme premier membre
    new_squad.members.append(current_user)

    db.add(new_squad)
    db.commit()
    db.refresh(new_squad)
    return new_squad


# GET : Lister mes Squads
@router.get("/", response_model=List[SquadResponse])
def get_my_squads(current_user: User = Depends(get_current_user)):
    # Grâce à backref="squads" dans le modèle, on accède directement aux squads de l'user
    return current_user.squads


@router.post("/{squad_id}/beer-calls/")
async def create_beer_call(
        squad_id: int,
        file: UploadFile = File(...),
        latitude: float = Form(...),
        longitude: float = Form(...),
        location_name: str = Form(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # 1. Vérifier que la Squad existe et que l'user en fait partie
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    if not squad:
        raise HTTPException(status_code=404, detail="Squad introuvable")

    if current_user not in squad.members:
        raise HTTPException(status_code=403, detail="Tu ne fais pas partie de cette Squad")

    # 2. Lire l'image et l'envoyer à l'IA
    file_bytes = await file.read()
    ia_validation = await is_drink_detected(file_bytes)

    if not ia_validation:
        handle_ia_fraud(current_user, db)
        db.commit()
        raise HTTPException(status_code=400, detail="Photo refusée ! L'IA t'a grillé. -15 Caps 📉")

    # 3. Sauvegarder l'image sur le serveur (en prod, on utiliserait un AWS S3)
    os.makedirs("uploads/aperos", exist_ok=True)
    file_extension = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    file_name = f"{uuid.uuid4()}.{file_extension}"
    file_path = f"uploads/aperos/{file_name}"

    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # 4. Enregistrer l'Apéro en Base de Données
    new_apero = Apero(
        squad_id=squad_id,
        creator_id=current_user.id,
        location_name=location_name,
        latitude=latitude,
        longitude=longitude,
        photo_path=file_path
    )

    db.add(new_apero)
    db.commit()  # On commit ici pour que new_apero génère son ID
    db.refresh(new_apero)

    # --- NOUVEAU : Ajouter le créateur comme 1er participant validé (au Bar) ---
    creator_participant = AperoParticipant(
        apero_id=new_apero.id,
        user_id=current_user.id,
        status=ParticipationStatus.JOINED,
        photo_path=file_path  # On utilise la même photo que celle de l'apéro
    )
    db.add(creator_participant)
    # -------------------------------------------------------------------------

    previous_apero = db.query(Apero).filter(
        Apero.squad_id == squad_id, Apero.location_name == location_name
    ).first()
    bonus_explo = 20 if not previous_apero else 0

    # 3. Récompense de base
    current_user.capsules += (50 + bonus_explo)

    # 4. Badges de création
    created_count = db.query(Apero).filter(Apero.creator_id == current_user.id).count()
    if created_count == 1: award_badge(current_user, "ETINCELLE", db)
    if created_count == 10: award_badge(current_user, "RABATTEUR", db)

    # On remet les compteurs de flemme à zéro
    current_user.consecutive_declines = 0
    current_user.consecutive_piscine = 0

    db.commit()

    return {
        "message": "Beer Call lancé avec succès ! 🍻",
        "apero_id": new_apero.id,
        "bonus_capsules": 50,
        "total_capsules": current_user.capsules
    }


@router.get("/{squad_id}", response_model=SquadDetailsResponse)
def get_squad_details(
        squad_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # 1. Vérifier que la Squad existe et que l'user en fait partie
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    if not squad:
        raise HTTPException(status_code=404, detail="Squad introuvable")

    if current_user not in squad.members:
        raise HTTPException(status_code=403, detail="Tu ne fais pas partie de cette Squad")

    # 2. Récupérer tous les Apéros (du plus récent au plus ancien)
    aperos = db.query(Apero).filter(Apero.squad_id == squad_id).order_by(Apero.created_at.desc()).all()

    active_beer_call = None
    past_beer_calls = []

    now = datetime.now(timezone.utc)

    for apero in aperos:
        # Gestion propre des fuseaux horaires avec SQLAlchemy
        created_at = apero.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        # Compter les participants qui ont rejoint (statut Bar)
        # Note : Si tu as bien ajouté le créateur dans AperoParticipant lors de la création,
        # le joined_count comptera déjà le créateur ! (Tu peux donc potentiellement enlever le "1 +")
        joined_count = db.query(AperoParticipant).filter(
            AperoParticipant.apero_id == apero.id,
            AperoParticipant.status == ParticipationStatus.JOINED
        ).count()

        # NOUVEAU : Vérifier si le current_user a répondu à CET apéro
        user_participant = db.query(AperoParticipant).filter(
            AperoParticipant.apero_id == apero.id,
            AperoParticipant.user_id == current_user.id
        ).first()

        has_responded = user_participant is not None
        user_status = user_participant.status.value if user_participant else None

        apero_item = {
            "id": f"bc_{apero.id}",
            "creator_name": apero.creator.username,
            "location_name": apero.location_name or "Lieu inconnu",
            "longitude": apero.longitude,
            "latitude": apero.latitude,
            "started_at": created_at,
            "participants_count": joined_count,  # Adapté selon ton implémentation du créateur
            "has_responded": has_responded,  # Retourne True ou False
            "user_status": user_status  # Retourne "joined", "declined" ou null
        }

        # 3. Tri : Actif (moins de 4h) vs Historique
        if (now - created_at) < timedelta(hours=4) and active_beer_call is None:
            active_beer_call = apero_item
        else:
            past_beer_calls.append(apero_item)

    return {
        "id": f"sq_{squad.id}",
        "name": squad.name,
        "color": squad.color,
        "icon": squad.icon,
        "invite_code": squad.invite_code,
        "active_beer_call": active_beer_call,
        "past_beer_calls": past_beer_calls
    }


@router.post("/join", response_model=SquadResponse)
def join_squad(
        join_data: SquadJoin,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # 1. Chercher la squad par son code d'invitation
    # On passe le code en majuscules pour éviter les erreurs de saisie
    squad = db.query(Squad).filter(Squad.invite_code == join_data.invite_code.upper()).first()

    if not squad:
        raise HTTPException(status_code=404, detail="Code d'invitation invalide.")

    # 2. Vérifier si l'utilisateur est déjà membre
    if current_user in squad.members:
        raise HTTPException(status_code=400, detail="Tu fais déjà partie de cette Squad !")

    # 3. Ajouter l'utilisateur à la Squad
    squad.members.append(current_user)
    db.commit()
    db.refresh(squad)

    return squad


# Endpoint 1 : Rejoindre (Le Bar)
@router.post("/{squad_id}/beer-calls/{apero_id}/join/")
async def join_beer_call(
        squad_id: int,
        apero_id: str,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    actual_apero_id = int(apero_id.replace("bc_", ""))

    # NOUVEAU : Vérifier si l'utilisateur a déjà répondu
    existing_participant = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == actual_apero_id,
        AperoParticipant.user_id == current_user.id
    ).first()

    if existing_participant:
        raise HTTPException(status_code=400, detail="Tu as déjà répondu à cet appel de la bière !")

    # 1. Validation IA de la photo
    file_bytes = await file.read()
    if not await is_drink_detected(file_bytes):
        handle_ia_fraud(current_user, db)
        db.commit()
        raise HTTPException(status_code=400, detail="Pas de boisson, pas de Bar ! -15 Caps 📉")

    # 2. Sauvegarde photo
    file_path = f"uploads/aperos/reply_{uuid.uuid4()}.jpg"
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # 3. Enregistrement participation
    participant = AperoParticipant(
        apero_id=actual_apero_id,
        user_id=current_user.id,
        status=ParticipationStatus.JOINED,
        photo_path=file_path
    )

    apero_obj = db.query(Apero).filter(Apero.id == actual_apero_id).first()

    # --- LA CORRECTION EST ICI : On redonne le fuseau UTC à la date ---
    apero_created_at = apero_obj.created_at
    if apero_created_at.tzinfo is None:
        apero_created_at = apero_created_at.replace(tzinfo=timezone.utc)
    # ------------------------------------------------------------------

    time_diff = datetime.now(timezone.utc) - apero_created_at
    diff_seconds = time_diff.total_seconds()

    bonus_flash = 0
    if diff_seconds <= 120:  # < 2 mins
        bonus_flash = 15

    if diff_seconds <= 30:
        award_badge(current_user, "LUCKY_LUKE", db)
    elif diff_seconds <= 180:
        award_badge(current_user, "INCRUSTE", db)

    # 3. Streak Bonus (Assiduité)
    current_user.consecutive_joins += 1
    bonus_streak = 30 if current_user.consecutive_joins >= 3 else 0

    # 4. Badges de Piliers
    join_count = db.query(AperoParticipant).filter(
        AperoParticipant.user_id == current_user.id,
        AperoParticipant.status == ParticipationStatus.JOINED
    ).count()

    # On ajoute +1 pour prendre en compte la participation en cours !
    total_joins = join_count + 1

    if total_joins == 1:
        award_badge(current_user, "BAPTEME", db)
    elif total_joins == 10:
        award_badge(current_user, "HABITUE", db)
    elif total_joins == 50:
        award_badge(current_user, "PILIER", db)

    # On réinitialise les malus
    current_user.consecutive_declines = 0
    current_user.consecutive_piscine = 0

    # Total des récompenses
    total_gained = 30 + bonus_flash + bonus_streak
    current_user.capsules += total_gained

    db.add(participant)
    db.commit()
    return {"message": "Tu es au Bar ! 🍻", "bonus": 30}


# Endpoint 2 : Décliner (La Piscine)
@router.post("/{squad_id}/beer-calls/{apero_id}/decline/")
async def decline_beer_call(
        squad_id: int,
        apero_id: str,
        decline_data: AperoDecline,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    actual_apero_id = int(apero_id.replace("bc_", ""))

    # NOUVEAU : Vérifier si l'utilisateur a déjà répondu
    existing_participant = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == actual_apero_id,
        AperoParticipant.user_id == current_user.id
    ).first()

    if existing_participant:
        raise HTTPException(status_code=400, detail="Tu as déjà répondu à cet appel de la bière !")

    participant = AperoParticipant(
        apero_id=actual_apero_id,
        user_id=current_user.id,
        status=ParticipationStatus.DECLINED,
        excuse=decline_data.excuse
    )

    # Récompense Politesse
    current_user.capsules += 5

    # Cassage de la Streak de présence
    current_user.consecutive_joins = 0

    # Progression Badges Troll
    current_user.consecutive_piscine += 1
    if decline_data.excuse:
        current_user.consecutive_declines += 1

    if current_user.consecutive_piscine >= 5:
        award_badge(current_user, "NAGEUR", db)

    if current_user.consecutive_declines >= 5:
        award_badge(current_user, "CASANIER", db)

    db.add(participant)
    db.commit()
    return {"message": "Plouf ! Direction la piscine. 🌊", "bonus": 5}


@router.get("/{squad_id}/beer-calls/{beer_call_id}/worlds", response_model=WorldsResponse)
def get_beer_call_worlds(
        squad_id: int,
        beer_call_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    actual_apero_id = int(beer_call_id.replace("bc_", ""))

    # 1. Vérifications d'usage
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    if not squad or current_user not in squad.members:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    # 2. Récupérer tous les participants ayant répondu
    participants_db = db.query(AperoParticipant).filter(AperoParticipant.apero_id == actual_apero_id).all()

    bar_participants = []
    piscine_participants = []
    responded_user_ids = set()

    # 3. Répartir les gens dans le Bar ou la Piscine
    for p in participants_db:
        responded_user_ids.add(p.user_id)
        user = p.user

        if p.status == ParticipationStatus.JOINED:
            bar_participants.append({
                "user_id": f"u_{user.id}",
                "username": user.username,
                "avatar_config": user.avatar_config or {},
                # On simule l'URL pour que le front puisse charger l'image
                "proof_photo_url": f"http://127.0.0.1:8000/{p.photo_path}",
                "joined_at": datetime.utcnow()  # Idéalement, à remplacer par p.created_at
            })
        elif p.status == ParticipationStatus.DECLINED:
            piscine_participants.append({
                "user_id": f"u_{user.id}",
                "username": user.username,
                "avatar_config": user.avatar_config or {},
                "excuse": p.excuse,
                "declined_at": datetime.utcnow()  # Idéalement, à remplacer par p.created_at
            })

    # 4. Trouver les Fantômes (Le Dodo)
    # Les fantômes sont les membres de la squad qui NE SONT PAS dans responded_user_ids
    dodo_participants = []
    for member in squad.members:
        if member.id not in responded_user_ids:
            dodo_participants.append({
                "user_id": f"u_{member.id}",
                "username": member.username,
                "avatar_config": member.avatar_config or {}
            })

    # 5. Renvoyer le JSON parfaitement formaté pour la 3D
    return {
        "worlds": {
            "bar": {
                "name": "Le Bar",
                "theme_color": "#D97706",
                "participants": bar_participants
            },
            "piscine": {
                "name": "La Piscine",
                "theme_color": "#06B6D4",
                "participants": piscine_participants
            },
            "dodo": {
                "name": "Le Dodo",
                "theme_color": "#7C3AED",
                "participants": dodo_participants
            }
        }
    }
