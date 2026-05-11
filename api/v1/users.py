from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from core.security import get_current_user
from core.security import (
    verify_password,
    create_access_token,
    get_password_hash,
    get_optional_current_user
)
from db.database import get_db
from models.gamification import Skin
from models.user import User
from schemas.user import BuyItemRequest, AvatarSchema, PushTokenUpdate
from schemas.user import ConnectionItem
from schemas.user import (
    FullProfileResponse,
    UserCreate,
    UserProfileResponse,
    UserResponse
)
from services.notifications import send_push_notifications

router = APIRouter()


@router.post("/signup/", response_model=UserResponse)
def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    # 1. Vérification si le pseudo existe
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Ce nom d'avatar est déjà pris !")

    # 2. Création de l'utilisateur avec son avatar JSON
    new_user = User(
        username=user_data.username,
        hashed_password=get_password_hash(user_data.password),
        avatar_config=user_data.avatar.dict(),
        capsules=100
    )

    # On ajoute en base pour générer le new_user.id
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # ==========================================
    # 3. NOUVEAU : Ajout des skins à l'inventaire
    # ==========================================
    # On récupère tous les IDs des modèles choisis dans l'objet avatar
    chosen_skin_ids = [
        user_data.avatar.head,
        user_data.avatar.body,
        user_data.avatar.legs,
        user_data.avatar.feet
    ]
    if user_data.avatar.accessory:
        chosen_skin_ids.append(user_data.avatar.accessory)

    # On va chercher les objets Skin correspondants dans la base de données
    skins_to_grant = db.query(Skin).filter(Skin.id.in_(chosen_skin_ids)).all()

    # On lie ces skins au nouvel utilisateur (remplit la table user_skins automatiquement)
    new_user.skins.extend(skins_to_grant)
    db.commit()  # On sauvegarde l'inventaire
    # ==========================================

    # 4. Génération du token
    token = create_access_token(data={"sub": new_user.username})

    # 5. Retour des infos
    return {
        "id": new_user.id,
        "username": new_user.username,
        "capsules": new_user.capsules,
        "avatar": new_user.avatar_config,
        "access_token": token,
        "token_type": "bearer"
    }


@router.post("/token/")
def login(
        form_data: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db)
):
    # 1. Recherche de l'utilisateur par son username
    user = db.query(User).filter(User.username == form_data.username).first()

    # 2. Vérification de l'existence et du mot de passe
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nom d'utilisateur ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Création du token JWT
    # On met le username dans le "sub" (subject) du token
    access_token = create_access_token(data={"sub": user.username})

    # 4. Retour conforme au standard OAuth2
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


@router.get("/me", response_model=UserProfileResponse)
def read_users_me(current_user: User = Depends(get_current_user)):
    return {
        "username": current_user.username,
        "caps": current_user.capsules,
        "score": current_user.score,
        "title": get_rank_title(current_user.score),
        "avatar": current_user.avatar_config or {},
        "stats": {
            "aperos_created": current_user.aperos_created_count,
            "aperos_joined": current_user.aperos_joined_count,
            "aperos_declined": current_user.aperos_declined_count,
            "aperos_missed": current_user.aperos_missed_count,
            "fraud_count": current_user.ia_fraud_count
        }
    }


@router.get("/profile/", response_model=FullProfileResponse)
def get_full_profile(
        current_user: Optional[User] = Depends(get_optional_current_user),
        db: Session = Depends(get_db)
):
    # Récupérer tout le catalogue depuis la DB (généré par le seed)
    all_skins = db.query(Skin).all()

    # --- CAS 1 : Utilisateur non connecté (Signup) ---
    if not current_user:
        shop_items = [
            {
                "id": skin.id,
                "name": skin.name,
                "category": skin.category,
                "gender": skin.gender,
                "price": 0,  # Tout est gratuit au Signup
                "is_owned": True  # Il peut tout équiper
            }
            for skin in all_skins
        ]

        return {
            "id": 0,
            "username": "Guest",
            "caps": 0,
            "score": 0,
            "title": "Nouveau Venu",
            "avatar": {},
            "unlocked_badges": [],
            "squads": [],
            "shop_items": shop_items,
            "stats": {
                "aperos_created": 0,
                "aperos_joined": 0,
                "aperos_declined": 0,
                "aperos_missed": 0,
                "fraud_count": 0
            }
        }

    # --- CAS 2 : Utilisateur connecté (Dashboard / Boutique) ---
    # Calcul du titre
    title = get_rank_title(current_user.score)

    # Liste des IDs que l'utilisateur possède
    owned_skin_ids = [skin.id for skin in current_user.skins]

    shop_items = [
        {
            "id": skin.id,
            "name": skin.name,
            "category": skin.category,
            "gender": skin.gender,
            "price": skin.price_caps,
            "is_owned": skin.id in owned_skin_ids
        }
        for skin in all_skins
    ]

    return {
        "id": current_user.id,
        "username": current_user.username,
        "caps": current_user.capsules,
        "score": current_user.score,
        "title": title,
        "avatar": current_user.avatar_config or {},
        "unlocked_badges": current_user.badges,
        "squads": current_user.squads,
        "shop_items": shop_items,
        "stats": {
            "aperos_created": current_user.aperos_created_count,
            "aperos_joined": current_user.aperos_joined_count,
            "aperos_declined": current_user.aperos_declined_count,
            "aperos_missed": current_user.aperos_missed_count,
            "fraud_count": current_user.ia_fraud_count
        }
    }


@router.get("/profile/{user_id}/", response_model=FullProfileResponse)
def get_user_profile(
        user_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)  # 🔒 NOUVEAU : Le videur est à l'entrée !
):
    # 1. On nettoie l'ID reçu (ex: "u_2" devient 2)
    try:
        actual_id = int(user_id.replace("u_", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format d'ID utilisateur invalide.")

    # 2. On cherche l'utilisateur ciblé dans la base
    target_user = db.query(User).filter(User.id == actual_id).first()

    if not target_user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    # --- OPTIONNEL : Empêcher de voir son propre profil via cette route ---
    # Si le joueur tape son propre ID, on peut le rediriger mentalement vers /profile/
    if target_user.id == current_user.id:
        pass  # Tu pourrais lever une erreur ici si tu le souhaites, ou juste laisser passer.

    # 3. Calcul dynamique du titre de CE joueur
    title = get_rank_title(current_user.score)

    # 4. On récupère le catalogue de la boutique
    all_skins = db.query(Skin).all()

    # On regarde ce que le joueur ciblé possède
    owned_skin_ids = [skin.id for skin in target_user.skins]

    # On construit son "shop"
    shop_items = [
        {
            "id": skin.id,
            "name": skin.name,
            "category": skin.category,
            "gender": skin.gender,
            "price": skin.price_caps,
            "is_owned": skin.id in owned_skin_ids
        }
        for skin in all_skins
    ]

    # 5. On retourne le profil
    return {
        "id": target_user.id,
        "username": target_user.username,
        "caps": target_user.capsules,
        "score": target_user.score,  # Ajoute ça
        "title": title,
        "avatar": target_user.avatar_config or {},
        "unlocked_badges": target_user.badges,
        "squads": target_user.squads,
        "shop_items": shop_items,
        "stats": {  # Et ça
            "aperos_created": target_user.aperos_created_count,
            "aperos_joined": target_user.aperos_joined_count,
            "aperos_declined": target_user.aperos_declined_count,
            "aperos_missed": target_user.aperos_missed_count,
            "fraud_count": target_user.ia_fraud_count
        }
    }


@router.post("/buy/")
def buy_item(
        buy_request: BuyItemRequest,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # 1. Vérifier si l'item (Skin) existe dans la boutique
    skin = db.query(Skin).filter(Skin.id == buy_request.item_id).first()
    if not skin:
        raise HTTPException(status_code=404, detail="Cet objet n'existe pas dans la boutique.")

    # 2. Vérifier si l'utilisateur possède DÉJÀ cet objet
    # SQLAlchemy charge automatiquement la liste current_user.skins
    if skin in current_user.skins:
        raise HTTPException(status_code=400, detail="Tu possèdes déjà ce skin !")

    # 3. Vérifier le solde de capsules
    if current_user.capsules < skin.price_caps:
        raise HTTPException(
            status_code=400,
            detail=f"Fonds insuffisants. Il te manque {skin.price_caps - current_user.capsules} capsules ! Va lancer un Beer Call 🍻"
        )

    # 4. Effectuer la transaction
    current_user.capsules -= skin.price_caps
    current_user.skins.append(skin)  # Ajoute le lien dans la table user_skins

    # 5. Sauvegarder en base de données
    db.commit()

    return {
        "message": f"Achat réussi : {skin.name} !",
        "new_capsules_balance": current_user.capsules,
        "item_purchased": skin.id
    }


@router.put("/equip/")
def equip_avatar(
        avatar_data: AvatarSchema,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)  # Le joueur doit être connecté
):
    # 1. On liste tous les IDs des skins que le joueur essaie d'équiper
    requested_skins = [
        avatar_data.head,
        avatar_data.body,
        avatar_data.legs,
        avatar_data.feet
    ]
    # L'accessoire est optionnel, on l'ajoute seulement s'il y en a un
    if avatar_data.accessory:
        requested_skins.append(avatar_data.accessory)

    # 2. On récupère la liste des IDs des skins que le joueur possède VRAIMENT
    owned_skin_ids = [skin.id for skin in current_user.skins]

    # 3. Vérification Anti-Triche !
    for skin_id in requested_skins:
        if skin_id not in owned_skin_ids:
            raise HTTPException(
                status_code=403,
                detail=f"Triche détectée 🚨 : Tu ne possèdes pas l'objet '{skin_id}'. Va l'acheter dans la boutique d'abord !"
            )

    # 4. Si tout est bon, on met à jour le JSON de l'avatar en base de données
    # .dict() (ou .model_dump() dans Pydantic v2) transforme l'objet en dictionnaire Python
    current_user.avatar_config = avatar_data.dict()

    # 5. On sauvegarde
    db.commit()

    return {
        "message": "Ta nouvelle tenue est magnifique ! ✨",
        "avatar": current_user.avatar_config
    }


def get_rank_title(score: int) -> str:
    if score < 50: return "Nouvelle Recrue"
    if score < 150: return "Amateur de Houblon"
    if score < 300: return "Pilier de Comptoir"
    if score < 600: return "Maître Brasseur"
    if score < 1000: return "Chevalier de la Pinte"
    if score < 2500: return "Seigneur du Fût"
    if score < 5000: return "Légende de l'Apéro"
    if score < 10000: return "Empereur de la Soif"
    return "Dieu du Beer Call"  # 10 000 et plus 👑


@router.get("/connections/", response_model=List[ConnectionItem])
def get_user_connections(current_user: User = Depends(get_current_user)):
    # 1. On commence par s'ajouter soi-même
    connections_dict = {
        current_user.id: {
            "id": f"u_{current_user.id}",
            "username": current_user.username,
            "caps": current_user.capsules,
            "score": current_user.score,
            "title": get_rank_title(current_user.score),
            "avatar": current_user.avatar_config or {}
        }
    }

    # 2. On ajoute les membres des squads (évite les doublons grâce à l'ID)
    for squad in current_user.squads:
        for member in squad.members:
            if member.id not in connections_dict:
                connections_dict[member.id] = {
                    "id": f"u_{member.id}",
                    "username": member.username,
                    "caps": member.capsules,
                    "score": member.score,
                    "title": get_rank_title(member.score),
                    "avatar": member.avatar_config or {}
                }

    # 3. Tri par SCORE décroissant (Prestige)
    return sorted(connections_dict.values(), key=lambda x: x["score"], reverse=True)


@router.put("/push-token/")
def update_push_token(
        token_data: PushTokenUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    current_user.push_token = token_data.token
    db.commit()
    return {"message": "Push token mis à jour !"}


@router.post("/test-notification/")
def test_push_notification(
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_user)
):
    # 1. On vérifie si l'utilisateur a bien un token enregistré
    if not current_user.push_token:
        # Pour tester que la connexion Firebase marche MÊME SANS TOKEN réel,
        # on peut envoyer un faux token. Firebase nous renverra une erreur spécifique.
        fake_token = "fake_token_pour_tester_la_connexion"
        tokens_to_test = [fake_token]
        msg = "Pas de vrai token en base. Test envoyé avec un faux token (Regarde les logs du serveur !)"
    else:
        tokens_to_test = [current_user.push_token]
        msg = "Notification envoyée sur ton téléphone ! 📱"

    # 2. On déclenche l'envoi
    background_tasks.add_task(
        send_push_notifications,
        tokens=tokens_to_test,
        title="Test Beer Call 🍻",
        body="Si tu vois ça, Firebase fonctionne parfaitement !"
    )

    return {"message": msg}
