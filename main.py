import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

# Charger les variables d'environnement du fichier .env
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.v1.squads import router as squad_router
from api.v1.users import router as user_router
from api.v1.games import router as games_router
from db.database import Base
from db.database import engine, SessionLocal
from models import gamification


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Démarrage du serveur Beer Call et Seeding des modèles 3D...")
    db = SessionLocal()
    models_dir = Path("static/models")

    if models_dir.exists():
        for file in models_dir.glob("*.fbx"):
            if file.stem in {'Bar', 'Floaty_Island', 'Swimming_pool', 'Animations'}:
                continue
            parts = file.stem.split('_')
            if len(parts) == 4:
                gender, category, name, price = parts
                skin_id = file.stem

                existing_skin = db.query(gamification.Skin).filter(gamification.Skin.id == skin_id).first()
                if not existing_skin:
                    new_skin = gamification.Skin(
                        id=skin_id,
                        name=name,
                        category=category,
                        gender=gender,
                        price_caps=int(price)
                    )
                    db.add(new_skin)
        db.commit()

    BADGES = [
        # --- LES CLASSIQUES (Présence) ---
        {"id": "BAPTEME", "name": "Le Baptême", "description": "1er apéro rejoint", "icon": "👼"},
        {"id": "HABITUE", "name": "L'Habitué", "description": "10 apéros rejoints", "icon": "🍻"},
        {"id": "PILIER", "name": "Le Pilier de Comptoir", "description": "50 apéros rejoints", "icon": "🗿"},
        {"id": "LEGENDE", "name": "La Légende", "description": "100 apéros rejoints", "icon": "👑"},

        # --- LES ANIMATEURS (Création) ---
        {"id": "ETINCELLE", "name": "L'Étincelle", "description": "1er apéro créé", "icon": "⚡"},
        {"id": "RABATTEUR", "name": "Le Rabatteur", "description": "10 apéros créés", "icon": "📢"},
        {"id": "AUBERGISTE", "name": "L'Aubergiste", "description": "50 apéros créés", "icon": "🍺"},
        {"id": "DIEU_FETE", "name": "Dieu de la Fête", "description": "100 apéros créés", "icon": "🎆"},

        # --- LA VITESSE (Réactivité) ---
        {"id": "SNIPER", "name": "Le Sniper", "description": "A rejoint en < 10s", "icon": "🎯"},
        {"id": "LUCKY_LUKE", "name": "Lucky Luke", "description": "Présence validée en < 30s", "icon": "⏱️"},
        {"id": "INCRUSTE", "name": "L'Incruste", "description": "A rejoint en < 3 min", "icon": "🥷"},
        {"id": "RETARDATAIRE", "name": "Le Retardataire", "description": "A rejoint dans les 10 dernières minutes",
         "icon": "🐌"},

        # --- LES SÉRIES (Rétention / Streaks) ---
        {"id": "MARATHONIEN", "name": "Le Marathonien", "description": "Présent 3 apéros de suite", "icon": "🏃‍♂️"},
        {"id": "INCREVABLE", "name": "L'Increvable", "description": "Présent 10 apéros de suite", "icon": "🧟‍♂️"},

        # --- LE TROLL & LE CHAMBRAGE ---
        {"id": "FAUSSAIRE", "name": "Le Faussaire", "description": "Triche avérée 3 fois", "icon": "🤥"},
        {"id": "ENNEMI_PUBLIC", "name": "Ennemi Public", "description": "Triche avérée 10 fois", "icon": "🦹‍♂️"},
        {"id": "NAGEUR", "name": "Le Nageur", "description": "A esquivé 5 apéros (La Piscine)", "icon": "🏊"},
        {"id": "CASANIER", "name": "Le Casanier", "description": "A décliné avec excuse 5 fois", "icon": "🏠"},
        {"id": "SOMNAMBULE", "name": "Le Somnambule", "description": "A fait le mort 10 fois (Le Dodo)", "icon": "👻"},
        {"id": "REVENANT", "name": "Le Revenant", "description": "Retour au Bar après 10 absences", "icon": "🧟"},

        # --- BOUTIQUE & ÉVÉNEMENTS ---
        {"id": "FLAMBEUR", "name": "Le Flambeur", "description": "A acheté un objet à 5000 capsules", "icon": "💸"},
        {"id": "PADDOCK_MASTER", "name": "Maître du Paddock",
         "description": "Possède un accessoire des 11 écuries F1 (Cadillac & Audi incluses !)", "icon": "🏎️"},
        {"id": "FASHION_VICTIM", "name": "Fashion Victim", "description": "Possède 10 skins différents", "icon": "👗"}
    ]

    for b in BADGES:
        existing_badge = db.query(gamification.Badge).filter(gamification.Badge.id == b["id"]).first()
        if not existing_badge:
            new_badge = gamification.Badge(**b)
            db.add(new_badge)
    db.commit()

    db.close()

    yield

    print("🛑 Arrêt du serveur Beer Call. À la prochaine ! 🍻")


# Initialisation DB
Base.metadata.create_all(bind=engine)
app = FastAPI(title="Beer Call API", lifespan=lifespan)

# CONFIGURATION CORS - À placer IMPÉRATIVEMENT avant app.include_router
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # L'URL du Front (Vite par défaut)
    allow_credentials=True,
    allow_methods=["*"],  # Autorise OPTIONS, POST, GET, etc.
    allow_headers=["*"],  # Autorise Content-Type, Authorization, etc.
)

# Inclusion des routes
app.include_router(squad_router, prefix="/api/squads", tags=["Squads"])
app.include_router(games_router, prefix="/api/aperos", tags=["Games"])
app.include_router(user_router, prefix="/api/auth", tags=["Authentication"])

# S'assurer que le dossier existe pour éviter un crash au démarrage
os.makedirs("uploads/aperos", exist_ok=True)

# Exposer le dossier 'uploads' pour que le front puisse charger les images
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/models", StaticFiles(directory="static/models"), name="models")
