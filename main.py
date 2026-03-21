import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.v1.squads import router as squad_router
from api.v1.users import router as user_router
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
            if file.stem in {'Bar', 'Floaty_Island', 'Swimming_pool'}:
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
        {"id": "BAPTEME", "name": "Le Baptême", "description": "1er apéro rejoint", "icon": "👼"},
        {"id": "HABITUE", "name": "L'Habitué", "description": "10 apéros rejoints", "icon": "🍻"},
        {"id": "PILIER", "name": "Le Pilier de Comptoir", "description": "50 apéros rejoints", "icon": "🗿"},
        {"id": "LEGENDE", "name": "La Légende", "description": "100 apéros rejoints", "icon": "👑"},
        {"id": "ETINCELLE", "name": "L'Étincelle", "description": "1er apéro créé", "icon": "⚡"},
        {"id": "RABATTEUR", "name": "Le Rabatteur", "description": "10 apéros créés", "icon": "📢"},
        {"id": "LUCKY_LUKE", "name": "Lucky Luke", "description": "Présence validée en < 30s", "icon": "⏱️"},
        {"id": "INCRUSTE", "name": "L'Incruste", "description": "A rejoint en < 3 min", "icon": "🥷"},
        {"id": "FAUSSAIRE", "name": "Le Faussaire", "description": "Triche avérée 3 fois", "icon": "🤥"},
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
app.include_router(user_router, prefix="/api/auth", tags=["Authentication"])

# S'assurer que le dossier existe pour éviter un crash au démarrage
os.makedirs("uploads/aperos", exist_ok=True)

# Exposer le dossier 'uploads' pour que le front puisse charger les images
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/models", StaticFiles(directory="static/models"), name="models")
