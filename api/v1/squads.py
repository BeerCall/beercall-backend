import os
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request, BackgroundTasks
from sqlalchemy.orm import Session

from core.security import get_current_user
from db.database import get_db
from models.apero import Apero, AperoStatus
from models.apero import AperoParticipant, ParticipationStatus
from models.squad import Squad
from models.user import User
from schemas.apero import AperoDecline, WorldsResponse, ScheduledAperoCreate
from schemas.squad import SquadCreate, SquadDetailsResponse
from services.apero_lifecycle import APERO_DURATION, MAX_START_DISTANCE_METERS, get_apero_for_squad, get_squad_member, start_scheduled_apero, validate_distance
from schemas.squad import SquadResponse, SquadJoin
from services.gamification import handle_ia_fraud, award_badge, check_and_award_ghost_badges, apply_apero_start_rewards
from services.notifications import send_push_notifications, notify_scheduled_apero, notify_started_scheduled_apero
from services.photo_validation import is_drink_detected, calculate_geodistance

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


from datetime import datetime, timezone, timedelta


@router.post("/{squad_id}/beer-calls/")
async def create_beer_call(
        squad_id: int,
        background_tasks: BackgroundTasks,
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

    # Définir la limite de temps pour un apéro "actif" (4 heures)
    now = datetime.now(timezone.utc)
    time_limit = now - timedelta(hours=4)

    # --- NOUVELLE RÈGLE : L'utilisateur a-t-il déjà un apéro en cours ? ---
    user_active_apero = db.query(Apero).filter(
        Apero.creator_id == current_user.id,
        Apero.status == AperoStatus.ACTIVE,
        Apero.ended_at > now,
    ).first()

    if user_active_apero:
        raise HTTPException(
            status_code=400,
            detail="Tu as déjà lancé un Beer Call il y a moins de 4 heures ! Laisse les autres profiter de celui-ci avant d'en recréer un."
        )
    # ----------------------------------------------------------------------

    # --- RÈGLE EXISTANTE (1.5) : Vérifier s'il y a déjà un apéro actif à proximité ---
    active_aperos = db.query(Apero).filter(
        Apero.squad_id == squad_id,
        Apero.status == AperoStatus.ACTIVE,
        Apero.ended_at > now,
    ).all()

    for existing_apero in active_aperos:
        distance = calculate_geodistance(
            latitude, longitude,
            existing_apero.latitude, existing_apero.longitude
        )
        if distance <= 500:
            raise HTTPException(
                status_code=400,
                detail=f"Un Beer Call est déjà en cours tout près ({int(distance)}m) ! Rejoins-le plutôt."
            )
    # ------------------------------------------------------------------------

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
        photo_path=file_path,
        status=AperoStatus.ACTIVE,
        started_at=now,
        ended_at=now + APERO_DURATION,
    )

    db.add(new_apero)
    db.commit()
    db.refresh(new_apero)

    # Ajouter le créateur comme 1er participant validé (au Bar)
    creator_participant = AperoParticipant(
        apero_id=new_apero.id,
        user_id=current_user.id,
        status=ParticipationStatus.JOINED,
        photo_path=file_path
    )
    db.add(creator_participant)
    previous_apero = db.query(Apero).filter(Apero.squad_id == squad_id,
                                            Apero.location_name == location_name).first()
    bonus_explo = 20 if not previous_apero else 0

    # MISE À JOUR DES COMPTEURS DU CRÉATEUR
    current_user.capsules += (50 + bonus_explo)
    current_user.consecutive_joins += 1
    current_user.consecutive_declines = 0
    current_user.consecutive_piscine = 0

    # DISTRIBUTION DES BADGES (Création)
    created_count = db.query(Apero).filter(Apero.creator_id == current_user.id).count()
    if created_count >= 1: award_badge(current_user, "ETINCELLE", db)
    if created_count >= 10: award_badge(current_user, "RABATTEUR", db)
    if created_count >= 50: award_badge(current_user, "AUBERGISTE", db)
    if created_count >= 100: award_badge(current_user, "DIEU_FETE", db)
    # DISTRIBUTION DES BADGES (Présence & Streaks)
    join_count = db.query(AperoParticipant).filter(
        AperoParticipant.user_id == current_user.id,
        AperoParticipant.status == ParticipationStatus.JOINED
    ).count() + 1  # +1 car le commit du participant n'est pas encore fait
    if join_count >= 1: award_badge(current_user, "BAPTEME", db)
    if join_count >= 10: award_badge(current_user, "HABITUE", db)
    if join_count >= 50: award_badge(current_user, "PILIER", db)
    if join_count >= 100: award_badge(current_user, "LEGENDE", db)
    if current_user.consecutive_joins >= 3: award_badge(current_user, "MARATHONIEN", db)
    if current_user.consecutive_joins >= 10: award_badge(current_user, "INCREVABLE", db)

    db.commit()

    target_tokens = [m.push_token for m in squad.members if m.id != current_user.id and m.push_token]
    background_tasks.add_task(
        send_push_notifications,
        tokens=target_tokens,
        title="🍺 RUPTURE DE SOBRIÉTÉ !",
        body=f"{current_user.username} a craqué et réclame du renfort ! Viens sauver son foie !"
    )
    return {
        "message": "Beer Call lancé avec succès ! 🍻",
        "apero_id": new_apero.id,
        "bonus_capsules": 50,
        "total_capsules": current_user.capsules
    }


@router.post("/{squad_id}/scheduled-beer-calls/")
def create_scheduled_beer_call(
        squad_id: int,
        scheduled: ScheduledAperoCreate,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    try:
        squad = get_squad_member(db, squad_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    scheduled_for = scheduled.scheduled_for.astimezone(timezone.utc)
    if scheduled_for <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="La date doit être dans le futur")
    existing_aperos = db.query(Apero).filter(
        Apero.squad_id == squad_id, 
        Apero.status.in_([AperoStatus.SCHEDULED, AperoStatus.ACTIVE])
    ).all()
    
    for existing in existing_aperos:
        existing_time = existing.scheduled_for if existing.status == AperoStatus.SCHEDULED else (existing.started_at or existing.created_at)
        if existing_time:
            if existing_time.tzinfo is None:
                existing_time = existing_time.replace(tzinfo=timezone.utc)
            
            time_diff = abs((scheduled_for - existing_time).total_seconds())
            if time_diff < 4 * 3600:  # Moins de 4 heures d'écart
                if calculate_geodistance(scheduled.latitude, scheduled.longitude, existing.latitude, existing.longitude) <= 500:
                    status_str = "programmé" if existing.status == AperoStatus.SCHEDULED else "en cours"
                    raise HTTPException(status_code=400, detail=f"Un apéro est déjà {status_str} à cet endroit dans ce créneau horaire")
    apero = Apero(squad_id=squad_id, creator_id=current_user.id, location_name=scheduled.location_name,
                  latitude=scheduled.latitude, longitude=scheduled.longitude,
                  status=AperoStatus.SCHEDULED, scheduled_for=scheduled_for)
    db.add(apero)
    db.commit()
    db.refresh(apero)
    tokens = [m.push_token for m in squad.members if m.id != current_user.id and m.push_token]
    background_tasks.add_task(notify_scheduled_apero, tokens, squad_id, apero.id,
                              apero.location_name, scheduled_for.isoformat())
    return apero


@router.post("/{squad_id}/beer-calls/{apero_id}/start/")
async def start_scheduled_beer_call(
        squad_id: int, apero_id: str, background_tasks: BackgroundTasks,
        file: UploadFile = File(...), latitude: float = Form(...), longitude: float = Form(...),
        db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    try:
        squad = get_squad_member(db, squad_id, current_user.id)
        apero = get_apero_for_squad(db, squad_id, int(apero_id.replace("bc_", "")))
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if apero.status != AperoStatus.SCHEDULED:
        raise HTTPException(status_code=409, detail="Cet apéro n'est plus programmé")

    # Empêcher le démarrage si un apéro est déjà en cours au même endroit
    active_aperos = db.query(Apero).filter(Apero.squad_id == squad_id, Apero.status == AperoStatus.ACTIVE).all()
    for active_apero in active_aperos:
        if calculate_geodistance(latitude, longitude, active_apero.latitude, active_apero.longitude) <= 500:
            raise HTTPException(status_code=400, detail="Un apéro est déjà en cours à proximité")

    distance = validate_distance(apero, latitude, longitude)
    if distance > MAX_START_DISTANCE_METERS:
        handle_ia_fraud(current_user, db)
        db.commit()
        raise HTTPException(status_code=403, detail=f"Tu es à {int(distance)}m, approche-toi à moins de 500m")
    file_bytes = await file.read()
    if not await is_drink_detected(file_bytes):
        handle_ia_fraud(current_user, db)
        db.commit()
        raise HTTPException(status_code=400, detail="Pas de boisson, pas de démarrage")
    os.makedirs("uploads/aperos", exist_ok=True)
    file_path = f"uploads/aperos/{uuid.uuid4()}.{(file.filename or 'jpg').split('.')[-1]}"
    with open(file_path, "wb") as output:
        output.write(file_bytes)
    started, result = start_scheduled_apero(db, apero.id, current_user.id, file_path)
    if not started:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cet apéro a déjà été démarré")
    apply_apero_start_rewards(current_user, started, db)
    db.commit()
    tokens = [m.push_token for m in squad.members if m.id != current_user.id and m.push_token]
    background_tasks.add_task(notify_started_scheduled_apero, tokens, squad_id, started.id, started.location_name or "ce lieu")
    return {"message": "Apéro démarré", "apero_id": started.id, "status": started.status.value,
            "started_at": started.started_at, "ended_at": started.ended_at}


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

    active_beer_call, scheduled_beer_calls, past_beer_calls = [], [], []
    for apero in aperos:
        apero_end = apero.ended_at or (apero.created_at + timedelta(hours=4))
        if apero_end.tzinfo is None:
            apero_end = apero_end.replace(tzinfo=timezone.utc)

        if apero.status == AperoStatus.ACTIVE and apero_end <= datetime.now(timezone.utc):
            apero.status = AperoStatus.ENDED
            if not apero.ended_at:
                apero.ended_at = apero_end
            db.commit()
        joined_count = db.query(AperoParticipant).filter(
            AperoParticipant.apero_id == apero.id,
            AperoParticipant.status == ParticipationStatus.JOINED
        ).count()
        user_participant = db.query(AperoParticipant).filter(
            AperoParticipant.apero_id == apero.id, AperoParticipant.user_id == current_user.id
        ).first()
        def force_utc(dt):
            if dt and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        item = {
            "id": f"bc_{apero.id}",
            "creator_name": apero.creator.username,
            "creator_id": apero.creator_id,
            "location_name": apero.location_name or "Lieu inconnu",
            "longitude": apero.longitude,
            "latitude": apero.latitude,
            "status": apero.status,
            "scheduled_for": force_utc(apero.scheduled_for),
            "started_at": force_utc(apero.started_at),
            "ended_at": force_utc(apero.ended_at),
            "participants_count": joined_count,
            "has_responded": user_participant is not None,
            "user_status": user_participant.status if user_participant else None,
            "can_start": apero.status == AperoStatus.SCHEDULED,
        }
        if apero.status == AperoStatus.SCHEDULED:
            scheduled_beer_calls.append(item)
        elif apero.status == AperoStatus.ACTIVE:
            active_beer_call.append(item)
        else:
            past_beer_calls.append(item)

    return {
        "id": f"sq_{squad.id}",
        "name": squad.name,
        "color": squad.color,
        "icon": squad.icon,
        "invite_code": squad.invite_code,
        "active_beer_call": active_beer_call,
        "scheduled_beer_calls": scheduled_beer_calls,
        "past_beer_calls": past_beer_calls
    }


@router.post("/join", response_model=SquadResponse)
def join_squad(
        join_data: SquadJoin,
        background_tasks: BackgroundTasks,
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
    target_tokens = [m.push_token for m in squad.members if m.id != current_user.id and m.push_token]
    background_tasks.add_task(
        send_push_notifications,
        tokens=target_tokens,
        title="🥩 NOUVELLE CHAIR À PÂTÉ !",
        body=f"{current_user.username} vient de débarquer dans '{squad.name}'. Préparez le bizutage !"
    )

    db.refresh(squad)
    return squad


@router.post("/{squad_id}/beer-calls/{apero_id}/join/")
async def join_beer_call(
        squad_id: int,
        background_tasks: BackgroundTasks,
        apero_id: str,
        lat: float = Form(...),
        lon: float = Form(...),
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    actual_apero_id = int(apero_id.replace("bc_", ""))

    # 0. Vérifier si l'utilisateur a déjà répondu
    existing_participant = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == actual_apero_id,
        AperoParticipant.user_id == current_user.id
    ).first()

    if existing_participant:
        raise HTTPException(status_code=400, detail="Tu as déjà répondu à cet appel de la bière !")

    # --- NOUVEAU : VÉRIFICATION GÉOGRAPHIQUE ---
    apero_obj = db.query(Apero).filter(Apero.id == actual_apero_id).first()
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    if not apero_obj or apero_obj.squad_id != squad_id:
        raise HTTPException(status_code=404, detail="Apéro introuvable dans cette squad")
    if not squad or current_user not in squad.members:
        raise HTTPException(status_code=403, detail="Tu ne fais pas partie de cette Squad")
    if apero_obj.status != AperoStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Cet apéro n'est pas actif")

    distance = calculate_geodistance(lat, lon, apero_obj.latitude, apero_obj.longitude)

    if distance > 500:
        # TRICHERIE DISTANCE : On applique le malus IA direct (ia_fraud_count + malus points)
        handle_ia_fraud(current_user, db)
        db.commit()
        raise HTTPException(
            status_code=403,
            detail=f"Triche détectée ! Tu es à {int(distance)}m. Malus appliqué. 📉"
        )
    # --------------------------------------------

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

    past_ghost_streak = check_and_award_ghost_badges(current_user, db)
    if past_ghost_streak >= 10:
        award_badge(current_user, "REVENANT", db)

    # 1. Enregistrement de la participation
    participant = AperoParticipant(
        apero_id=actual_apero_id,
        user_id=current_user.id,
        status=ParticipationStatus.JOINED,
        photo_path=file_path
    )

    # 2. Gestion des dates pour les bonus de vitesse
    apero_started_at = apero_obj.started_at or apero_obj.created_at
    if apero_started_at.tzinfo is None:
        apero_started_at = apero_started_at.replace(tzinfo=timezone.utc)

    diff_seconds = (datetime.now(timezone.utc) - apero_started_at).total_seconds()

    # 3. BONUS FLASH ET BADGES DE VITESSE
    bonus_flash = 15 if diff_seconds <= 120 else 0

    if diff_seconds <= 10:
        award_badge(current_user, "SNIPER", db)
    elif diff_seconds <= 30:
        award_badge(current_user, "LUCKY_LUKE", db)
    elif diff_seconds <= 180:
        award_badge(current_user, "INCRUSTE", db)

    if diff_seconds >= 13800:  # Les 10 dernières minutes des 4h d'ouverture
        award_badge(current_user, "RETARDATAIRE", db)

    # 4. MISE À JOUR DES COMPTEURS STREAKS
    current_user.consecutive_joins += 1
    current_user.consecutive_declines = 0
    current_user.consecutive_piscine = 0

    bonus_streak = 30 if current_user.consecutive_joins >= 3 else 0
    total_gained = 30 + bonus_flash + bonus_streak
    current_user.capsules += total_gained

    # 5. BADGES DE PRÉSENCE & STREAKS (avec >=)
    join_count = db.query(AperoParticipant).filter(
        AperoParticipant.user_id == current_user.id,
        AperoParticipant.status == ParticipationStatus.JOINED
    ).count() + 1

    if join_count >= 1: award_badge(current_user, "BAPTEME", db)
    if join_count >= 10: award_badge(current_user, "HABITUE", db)
    if join_count >= 50: award_badge(current_user, "PILIER", db)
    if join_count >= 100: award_badge(current_user, "LEGENDE", db)

    if current_user.consecutive_joins >= 3: award_badge(current_user, "MARATHONIEN", db)
    if current_user.consecutive_joins >= 10: award_badge(current_user, "INCREVABLE", db)

    db.add(participant)
    db.commit()

    # --- Notifications (ton code conservé à 100%) ---
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    declined_participants = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == actual_apero_id,
        AperoParticipant.status == ParticipationStatus.DECLINED
    ).all()
    declined_ids = [p.user_id for p in declined_participants]

    target_tokens = [
        m.push_token for m in squad.members
        if m.id != current_user.id and m.id not in declined_ids and m.push_token
    ]

    background_tasks.add_task(
        send_push_notifications,
        tokens=target_tokens,
        title="🚀 UN SOIVARD DE PLUS !",
        body=f"{current_user.username} a ramené sa fraise ! Tournée générale !"
    )

    return {"message": "Tu es au Bar ! 🍻", "bonus": total_gained}


# Endpoint 2 : Décliner (La Piscine)
@router.post("/{squad_id}/beer-calls/{apero_id}/decline/")
async def decline_beer_call(
        squad_id: int,
        background_tasks: BackgroundTasks,
        apero_id: str,
        decline_data: AperoDecline,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    actual_apero_id = int(apero_id.replace("bc_", ""))
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    apero = db.query(Apero).filter(Apero.id == actual_apero_id).first()
    if not squad or current_user not in squad.members:
        raise HTTPException(status_code=403, detail="Tu ne fais pas partie de cette Squad")
    if not apero or apero.squad_id != squad_id:
        raise HTTPException(status_code=404, detail="Apéro introuvable dans cette squad")
    if apero.status != AperoStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Cet apéro n'est pas actif")

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

    current_user.capsules += 15
    current_user.consecutive_joins = 0
    current_user.consecutive_piscine += 1

    if decline_data.excuse:
        current_user.consecutive_declines += 1
    else:
        current_user.consecutive_declines = 0  # Casse la série si pas d'excuse !

    if current_user.consecutive_piscine >= 5:
        award_badge(current_user, "NAGEUR", db)

    if current_user.consecutive_declines >= 5:
        award_badge(current_user, "CASANIER", db)

    db.add(participant)
    db.commit()

    # NOTIF : Uniquement ceux qui ont rejoint le Bar (JOINED)
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    apero = db.query(Apero).filter(Apero.id == actual_apero_id).first()

    joined_participants = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == actual_apero_id,
        AperoParticipant.status == ParticipationStatus.JOINED
    ).all()

    target_tokens = [
        p.user.push_token for p in joined_participants
        if p.user_id != current_user.id and p.user.push_token
    ]

    location = apero.location_name or "inconnu"
    background_tasks.add_task(
        send_push_notifications,
        tokens=target_tokens,
        title="🤡 ALERTE FRAGILE !",
        body=f"{current_user.username} s'est dégonflé pour {location}... Tu paieras le triple la prochaine fois !"
    )

    return {"message": "Plouf ! Direction la piscine. 🌊", "bonus": 15}


@router.get("/{squad_id}/beer-calls/{beer_call_id}/worlds", response_model=WorldsResponse)
def get_beer_call_worlds(
        squad_id: int,
        beer_call_id: str,
        request: Request,
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
                "proof_photo_url": f"{request.base_url}{p.photo_path}",
                "joined_at": datetime.utcnow()
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
