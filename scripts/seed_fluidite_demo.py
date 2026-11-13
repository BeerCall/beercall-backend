#!/usr/bin/env python3
"""
C02 — Script de seed synthétique pour la démo locale de fluidité.

Garde de sécurité stricte :
- APP_ENV == 'test'
- Nom de DB == 'beercall_fluidite_demo'
- Hôte DB dans ('db_beercall', 'localhost', '127.0.0.1')

Crée :
- 3 comptes clés : FluiditeDemo, FluiditeSolo, FluiditeOutsider (mdp: FluiditeTest2026!)
- 49 autres utilisateurs dans Fluidite50 (code FLUID50), soit 50 membres.
- Squad secondaire Fluidite40 (code FLUID40) avec 40 des 50 membres.
- 50 tenues d'avatar distinctes avec 50 hashes uniques, skins attachés à l'inventaire.
- Positions GPS synthétiques autour de Paris.
- 20 apéros tests dans Fluidite50 + 1 apéro actif dans Fluidite40 (10 joined, 10 declined, 20 ghost).
- Photos de preuve réelles générées via Pillow dans uploads/proofs/.
- Planification des jobs de sprites pour les 50 tenues (statut queued).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any

from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Ajustement du sys.path pour importer le backend
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.security import get_password_hash
from db.database import Base, SessionLocal, engine
from models.apero import Apero, AperoParticipant, AperoStatus, ParticipationStatus
from models.gamification import Skin
from models.squad import Squad
from models.user import User
from services.avatar_sprites import (
    compute_config_hash,
    normalize_avatar_payload,
    schedule_avatar_render,
)


def assert_safe_environment(db_session: Session) -> None:
    """Double garde de sécurité interdisant toute écriture hors de l'environnement de test isolé."""
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if app_env != "test":
        raise RuntimeError(
            f"Garde de sécurité violée : APP_ENV doit valoir 'test', reçu '{app_env}'"
        )

    bind = db_session.get_bind()
    db_name = bind.url.database or ""
    if db_name != "beercall_fluidite_demo":
        raise RuntimeError(
            f"Garde de sécurité violée : base interdite '{db_name}'. "
            "Seule la base 'beercall_fluidite_demo' est autorisée pour ce script."
        )

    host = bind.url.host or ""
    if host not in ("db_beercall", "localhost", "127.0.0.1"):
        raise RuntimeError(
            f"Garde de sécurité violée : hôte base non autorisé '{host}'"
        )


DEMO_PASSWORD_HASH = get_password_hash("FluiditeTest2026!")

# Composants réels issus du catalogue GLB extrait en A03 (162 items)
MEN_HEADS = [
    "Men_head_Casual_0", "Men_head_Casual2_0", "Men_head_Beach_150",
    "Men_head_Adventurer_150", "Men_head_Farmer_150", "Men_head_Horse_150",
    "Men_head_King_150", "Men_head_Punk_150", "Men_head_SpaceSuit_150",
    "Men_head_Suit_150", "Men_head_Swat_150", "Men_head_Worker_0"
]
MEN_BODIES = [
    "Men_body_Casual_0", "Men_body_Casual2_0", "Men_body_Beach_300",
    "Men_body_Adventurer_300", "Men_body_Farmer_300", "Men_body_King_300",
    "Men_body_Punk_300", "Men_body_SpaceSuit_300", "Men_body_Suit_300",
    "Men_body_Swat_300", "Men_body_Worker_0"
]
MEN_LEGS = [
    "Men_legs_Casual_0", "Men_legs_Casual2_0", "Men_legs_Beach_200",
    "Men_legs_Adventurer_200", "Men_legs_Farmer_200", "Men_legs_King_200",
    "Men_legs_Punk_200", "Men_legs_SpaceSuit_200", "Men_legs_Suit_200",
    "Men_legs_Swat_200", "Men_legs_Worker_0"
]
MEN_FEET = [
    "Men_feet_Casual_0", "Men_feet_Casual2_0", "Men_feet_Beach_100",
    "Men_feet_Adventurer_100", "Men_feet_Farmer_100", "Men_feet_King_100",
    "Men_feet_Punk_100", "Men_feet_SpaceSuit_100", "Men_feet_Suit_100",
    "Men_feet_Swat_100", "Men_feet_Worker_0"
]

WOMEN_HEADS = [
    "Women_head_Casual_0", "Women_head_Adventurer_150", "Women_head_Formal_150",
    "Women_head_Medieval_150", "Women_head_Punk_150", "Women_head_SciFi_150",
    "Women_head_Soldier_150", "Women_head_Suit_150", "Women_head_Witch_150",
    "Women_head_Worker_0"
]
WOMEN_BODIES = [
    "Women_body_Casual_0", "Women_body_Adventurer_300", "Women_body_Formal_300",
    "Women_body_Medieval_300", "Women_body_Punk_300", "Women_body_SciFi_300",
    "Women_body_Soldier_300", "Women_body_Suit_300", "Women_body_Witch_300",
    "Women_body_Worker_0"
]
WOMEN_LEGS = [
    "Women_legs_Casual_0", "Women_legs_Adventurer_200", "Women_legs_Formal_200",
    "Women_legs_Medieval_200", "Women_legs_Punk_200", "Women_legs_SciFi_200",
    "Women_legs_Soldier_200", "Women_legs_Suit_200", "Women_legs_Witch_200",
    "Women_legs_Worker_0"
]
WOMEN_FEET = [
    "Women_feet_Casual_0", "Women_feet_Adventurer_100", "Women_feet_Formal_100",
    "Women_feet_Medieval_100", "Women_feet_Punk_100", "Women_feet_SciFi_100",
    "Women_feet_Soldier_100", "Women_feet_Suit_100", "Women_feet_Witch_100",
    "Women_feet_Worker_0"
]

MEN_ACCESSORIES = [
    "none",
    "Men_accessory_Beer_500",
    "Men_accessory_Backpack_0",
    "Men_accessory_McLaren_5000",
    "Men_accessory_Pikachu_2000",
    "Men_accessory_Alpine_5000",
    "Men_accessory_AstonMartin_5000",
    "Men_accessory_Audi_5000",
    "Men_accessory_Charmander_2000",
]

WOMEN_ACCESSORIES = [
    "none",
    "Women_accessory_Beer_500",
    "Women_accessory_Backpack_0",
    "Women_accessory_McLaren_5000",
    "Women_accessory_Pikachu_2000",
    "Women_accessory_Alpine_5000",
    "Women_accessory_AstonMartin_5000",
    "Women_accessory_Audi_5000",
    "Women_accessory_Charmander_2000",
]


def generate_distinct_configs(count: int = 50) -> List[Dict[str, Any]]:
    """Génère `count` configurations d'avatar garanties distinctes par leur hash SHA-256."""
    configs: List[Dict[str, Any]] = []
    seen_hashes = set()

    for i in range(count):
        is_women = (i % 2 == 1)
        gender = "Women" if is_women else "Men"
        heads = WOMEN_HEADS if is_women else MEN_HEADS
        bodies = WOMEN_BODIES if is_women else MEN_BODIES
        legs = WOMEN_LEGS if is_women else MEN_LEGS
        feet = WOMEN_FEET if is_women else MEN_FEET
        accs = WOMEN_ACCESSORIES if is_women else MEN_ACCESSORIES

        h_idx = i % len(heads)
        b_idx = (i * 2 + 1) % len(bodies)
        l_idx = (i * 3 + 2) % len(legs)
        f_idx = (i * 5 + 3) % len(feet)
        a_idx = (i * 7) % len(accs)

        acc = accs[a_idx]
        cfg: Dict[str, Any] = {
            "gender": gender,
            "head": heads[h_idx],
            "body": bodies[b_idx],
            "legs": legs[l_idx],
            "feet": feet[f_idx],
            "accessory": None if acc == "none" else acc,
            "animation": "Idle",
        }

        # Au moins une tenue avec accessoire en rotation personnalisée (CHAL-03)
        if i == 0:
            cfg["accessory"] = "Men_accessory_Pikachu_2000"
            cfg["accessory_transform"] = {"rotation": [0, 0, 0.5]}
        elif i == 1:
            cfg["accessory"] = "Women_accessory_McLaren_5000"

        norm = normalize_avatar_payload(cfg)
        assert norm is not None, f"Configuration {i} invalide : {cfg}"
        h = compute_config_hash(norm)

        # Si collision de hash, décaler pour garantir 50 hashes uniques
        salt = 1
        while h in seen_hashes:
            cfg["head"] = heads[(h_idx + salt) % len(heads)]
            norm = normalize_avatar_payload(cfg)
            assert norm is not None
            h = compute_config_hash(norm)
            salt += 1

        seen_hashes.add(h)
        configs.append(cfg)

    assert len(seen_hashes) == count, f"Attendu {count} hashes uniques, obtenu {len(seen_hashes)}"
    return configs


def ensure_skin_exists(db: Session, piece_id: str, category: str, gender: str) -> Skin:
    """Retourne un article du catalogue disponible, sans jamais en créer.

    Le seed de démonstration ne doit pas inventer d'articles : un fichier
    manquant ou masqué doit faire échouer explicitement le seed. Les IDs
    logiques du seed restent stables quand le catalogue publié ajoute des
    jetons de transform entre le nom et le prix.
    """

    skin = (
        db.query(Skin)
        .filter(
            Skin.id == piece_id,
            Skin.available.is_(True),
        )
        .first()
    )
    if skin is None:
        skin = resolve_catalog_skin(db, piece_id, category, gender)
    if skin is None:
        raise ValueError(
            f"Article absent ou indisponible dans le catalogue : '{piece_id}' "
            f"({gender}/{category}). Lance catalog:sync après avoir préparé le fichier."
        )
    if skin.category != category or skin.gender != gender:
        raise ValueError(
            f"Article incohérent : '{piece_id}' est {skin.gender}/{skin.category}, "
            f"attendu {gender}/{category}."
        )
    return skin


def resolve_catalog_skin(db: Session, piece_id: str, category: str, gender: str) -> Skin | None:
    """Résout un ID logique vers le sourceId publié, s'il est unique.

    Exemple : ``Men_accessory_Pikachu_2000`` correspond au sourceId publié
    ``Men_accessory_Pikachu_px75_pz-60_ry169d862196_s50_2000``. Un identifiant
    ambigu est refusé plutôt que choisi arbitrairement.
    """

    parts = piece_id.split("_")
    if len(parts) < 4:
        return None
    prefix = "_".join(parts[:3])
    price = parts[-1]
    escaped_prefix = prefix.replace("_", "\\_")
    candidates = (
        db.query(Skin)
        .filter(
            Skin.gender == gender,
            Skin.category == category,
            Skin.available.is_(True),
            Skin.id.like(f"{escaped_prefix}\\_%", escape="\\"),
            Skin.id.like(f"%\\_{price}", escape="\\"),
        )
        .all()
    )
    if len(candidates) > 1:
        names = ", ".join(sorted(skin.id for skin in candidates))
        raise ValueError(
            f"Plusieurs articles correspondent à '{piece_id}' ({gender}/{category}) : {names}"
        )
    return candidates[0] if candidates else None


def canonicalize_config(db: Session, config: Dict[str, Any]) -> Dict[str, Any]:
    """Remplace les IDs logiques d'une configuration par les IDs publiés."""

    gender = config["gender"]
    resolved = dict(config)
    for category in ("head", "body", "legs", "feet"):
        piece_id = config.get(category)
        if piece_id and piece_id != "none":
            resolved[category] = ensure_skin_exists(db, piece_id, category, gender).id
    accessory = config.get("accessory")
    if accessory and accessory != "none":
        resolved["accessory"] = ensure_skin_exists(db, accessory, "accessory", gender).id
    return resolved


def create_synthetic_proof_photo(uploads_dir: Path, user_id: int, apero_id: int) -> str:
    """Génère une photo de preuve synthétique Pillow 400x400 et retourne son URL relative."""
    proofs_dir = uploads_dir / "proofs"
    proofs_dir.mkdir(parents=True, exist_ok=True)

    filename = f"proof_{apero_id}_{user_id}.jpg"
    filepath = proofs_dir / filename

    if not filepath.exists():
        img = Image.new("RGB", (400, 400), color=(245, 158, 11))  # Ambre BeerCall
        draw = ImageDraw.Draw(img)
        draw.rectangle([20, 20, 380, 380], outline=(255, 255, 255), width=4)
        draw.text((40, 180), f"BEER CALL #{apero_id}\nSoldat {user_id}", fill=(255, 255, 255))
        img.save(filepath, format="JPEG", quality=85)

    return f"/uploads/proofs/{filename}"


def seed_fluidite_demo(db: Session, uploads_dir: Path = Path("uploads")) -> Dict[str, Any]:
    """Exécute le seed complet et idempotent de la base de démonstration."""
    assert_safe_environment(db)

    print("[seed] Environnement sécurisé vérifié (APP_ENV=test, DB=beercall_fluidite_demo).")

    generated = generate_distinct_configs(50)
    configs = [canonicalize_config(db, cfg) for cfg in generated]
    hashes = {compute_config_hash(normalize_avatar_payload(cfg)) for cfg in configs}
    assert len(hashes) == len(configs), "Les configurations canoniques doivent rester distinctes"
    print(f"[seed] {len(configs)} configurations distinctes générées ({len(hashes)} hashes uniques).")

    # 1. Squads
    squad50 = db.query(Squad).filter_by(invite_code="FLUID50").first()
    if not squad50:
        squad50 = Squad(name="Fluidite50", icon="beer", color="#FFCC00", invite_code="FLUID50")
        db.add(squad50)
        db.flush()

    squad40 = db.query(Squad).filter_by(invite_code="FLUID40").first()
    if not squad40:
        squad40 = Squad(name="Fluidite40", icon="waves", color="#06B6D4", invite_code="FLUID40")
        db.add(squad40)
        db.flush()

    squad_out = db.query(Squad).filter_by(invite_code="OUTSIDER").first()
    if not squad_out:
        squad_out = Squad(name="OutsiderSquad", icon="pizza", color="#EC4899", invite_code="OUTSIDER")
        db.add(squad_out)
        db.flush()

    # 2. Utilisateurs
    # Compte principal
    demo_user = db.query(User).filter_by(username="FluiditeDemo").first()
    if not demo_user:
        demo_user = User(
            username="FluiditeDemo",
            hashed_password=DEMO_PASSWORD_HASH,
            capsules=5000,
            avatar_config=configs[0],
            last_latitude=48.8566,
            last_longitude=2.3522,
            last_location_at=datetime.now(timezone.utc),
        )
        db.add(demo_user)
        db.flush()
    else:
        demo_user.avatar_config = configs[0]
        demo_user.capsules = 5000

    if squad50 not in demo_user.squads:
        demo_user.squads.append(squad50)
    if squad40 not in demo_user.squads:
        demo_user.squads.append(squad40)

    # Compte solo (sans squad)
    solo_user = db.query(User).filter_by(username="FluiditeSolo").first()
    if not solo_user:
        solo_user = User(
            username="FluiditeSolo",
            hashed_password=DEMO_PASSWORD_HASH,
            capsules=100,
            avatar_config=configs[1],
            last_latitude=48.8600,
            last_longitude=2.3400,
            last_location_at=datetime.now(timezone.utc),
        )
        db.add(solo_user)
        db.flush()
    else:
        solo_user.avatar_config = configs[1]

    # Compte extérieur (autre squad)
    outsider_user = db.query(User).filter_by(username="FluiditeOutsider").first()
    if not outsider_user:
        outsider_user = User(
            username="FluiditeOutsider",
            hashed_password=DEMO_PASSWORD_HASH,
            capsules=100,
            avatar_config=configs[2],
            last_latitude=48.8700,
            last_longitude=2.3200,
            last_location_at=datetime.now(timezone.utc),
        )
        db.add(outsider_user)
        db.flush()
    else:
        outsider_user.avatar_config = configs[2]
    if squad_out not in outsider_user.squads:
        outsider_user.squads.append(squad_out)

    all_50_users: List[User] = [demo_user]

    # 49 autres utilisateurs fictifs
    now = datetime.now(timezone.utc)
    for i in range(1, 50):
        username = f"FluiditeUser_{i}"
        user = db.query(User).filter_by(username=username).first()
        lat = 48.8566 + (i % 7 - 3) * 0.005 + (i * 0.0005)
        lng = 2.3522 + ((i * 3) % 7 - 3) * 0.005 + (i * 0.0005)

        cfg = configs[i]
        if not user:
            user = User(
                username=username,
                hashed_password=DEMO_PASSWORD_HASH,
                capsules=1000 + i * 20,
                avatar_config=cfg,
                last_latitude=lat,
                last_longitude=lng,
                last_location_at=now - timedelta(minutes=i * 3),
            )
            db.add(user)
            db.flush()
        else:
            user.avatar_config = cfg
            user.last_latitude = lat
            user.last_longitude = lng
            user.last_location_at = now - timedelta(minutes=i * 3)

        if squad50 not in user.squads:
            user.squads.append(squad50)

        # 40 membres dans Fluidite40
        if i < 40 and squad40 not in user.squads:
            user.squads.append(squad40)

        all_50_users.append(user)

    # 3. Attacher les skins aux inventaires et planifier les rendus
    print("[seed] Attribution des skins d'inventaire et planification des jobs de sprites...")
    for user in all_50_users:
        cfg = user.avatar_config or {}
        gender = cfg.get("gender", "Men")
        for cat in ("head", "body", "legs", "feet"):
            piece_id = cfg.get(cat)
            if piece_id:
                skin = ensure_skin_exists(db, piece_id, cat, gender)
                if skin not in user.skins:
                    user.skins.append(skin)
        acc = cfg.get("accessory")
        if acc and acc != "none":
            skin = ensure_skin_exists(db, acc, "accessory", gender)
            if skin not in user.skins:
                user.skins.append(skin)

        # Planification A07
        schedule_avatar_render(db, user)

    db.flush()

    # 4. Apéros dans Fluidite50 (20 apéros : 5 actifs, 10 programmés, 5 terminés)
    print("[seed] Création des 20 apéros de test dans Fluidite50...")
    existing_aperos = db.query(Apero).filter_by(squad_id=squad50.id).all()
    if len(existing_aperos) < 20:
        for idx in range(len(existing_aperos), 20):
            creator = all_50_users[idx % len(all_50_users)]
            if idx < 5:
                status = AperoStatus.ACTIVE
                started = now - timedelta(hours=idx + 1)
                scheduled = None
                ended = None
            elif idx < 15:
                status = AperoStatus.SCHEDULED
                started = None
                scheduled = now + timedelta(hours=idx - 4)
                ended = None
            else:
                status = AperoStatus.ENDED
                started = now - timedelta(days=idx - 14, hours=2)
                scheduled = None
                ended = now - timedelta(days=idx - 14)

            apero = Apero(
                squad_id=squad50.id,
                creator_id=creator.id,
                location_name=f"Spot Fluidité #{idx + 1}",
                latitude=48.8566 + (idx * 0.002),
                longitude=2.3522 + (idx * 0.002),
                status=status,
                started_at=started,
                scheduled_for=scheduled,
                ended_at=ended,
            )
            db.add(apero)
            db.flush()

    # 5. Apéro actif de test dans Fluidite40 (10 joined, 10 declined, 20 ghost)
    apero40 = (
        db.query(Apero)
        .filter_by(squad_id=squad40.id, status=AperoStatus.ACTIVE)
        .first()
    )
    if not apero40:
        print("[seed] Création de l'apéro actif de référence dans Fluidite40...")
        apero40 = Apero(
            squad_id=squad40.id,
            creator_id=demo_user.id,
            location_name="Le Bar des Braves 40",
            latitude=48.8580,
            longitude=2.3540,
            status=AperoStatus.ACTIVE,
            started_at=now - timedelta(minutes=45),
        )
        db.add(apero40)
        db.flush()

    # Participants de l'apéro 40
    members40 = all_50_users[:40]
    for idx, u in enumerate(members40):
        part = (
            db.query(AperoParticipant)
            .filter_by(apero_id=apero40.id, user_id=u.id)
            .first()
        )
        if idx < 10:
            # 10 JOINED, chacun avec une vraie photo de preuve synthétique
            # servie par le volume de test (une par participant Bar).
            photo = create_synthetic_proof_photo(uploads_dir, u.id, apero40.id)
            if not part:
                part = AperoParticipant(
                    apero_id=apero40.id,
                    user_id=u.id,
                    status=ParticipationStatus.JOINED,
                    photo_path=photo,
                )
                db.add(part)
            elif not part.photo_path:
                part.photo_path = photo
        elif not part:
            if idx < 20:
                # 10 DECLINED avec excuses
                excuses = ["Piscine municipale", "Aquaponey", "Fatigué", "Trop de bières hier", "Dentiste"]
                part = AperoParticipant(
                    apero_id=apero40.id,
                    user_id=u.id,
                    status=ParticipationStatus.DECLINED,
                    excuse=excuses[idx % len(excuses)],
                )
            else:
                # 20 GHOST
                part = AperoParticipant(
                    apero_id=apero40.id,
                    user_id=u.id,
                    status=ParticipationStatus.GHOST,
                )
            db.add(part)

    db.commit()
    print("[seed] Seed terminé avec succès. Commit en base confirmé.")

    return {
        "status": "success",
        "users_count": len(all_50_users),
        "squads": ["Fluidite50", "Fluidite40", "OutsiderSquad"],
        "apero40_id": apero40.id,
    }


if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed_fluidite_demo(db)
    finally:
        db.close()
