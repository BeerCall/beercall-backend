import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# On récupère l'URL depuis les variables d'environnement (configurées dans docker-compose)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./beercall.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Configuration de l'engine
if DATABASE_URL.startswith("postgresql"):
    engine = create_engine(DATABASE_URL)
else:
    # Pour SQLite uniquement
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Enregistrement idempotent du hook de notification d'outbox transactionnelle
from core.realtime_notify import register_realtime_outbox_notifier
register_realtime_outbox_notifier()

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
