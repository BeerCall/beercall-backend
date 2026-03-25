from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel


class AperoDecline(BaseModel):
    excuse: str


class WorldParticipant(BaseModel):
    user_id: str
    username: str
    avatar_config: Dict[str, Any]
    proof_photo_url: Optional[str] = None
    joined_at: Optional[datetime] = None
    excuse: Optional[str] = None
    declined_at: Optional[datetime] = None


class WorldDetails(BaseModel):
    name: str
    theme_color: str
    participants: List[WorldParticipant]


class WorldsResponse(BaseModel):
    worlds: Dict[str, WorldDetails]


class AperoJoinRequest(BaseModel):
    lat: float
    lon: float
