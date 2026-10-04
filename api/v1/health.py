from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from sqlalchemy import text
from db.database import get_db

router = APIRouter()

@router.get("/live")
def liveness():
    return {"status": "ok"}

@router.get("/ready")
def readiness(response: Response, db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        response.status_code = 503
        return {"status": "error", "message": "database unavailable"}
