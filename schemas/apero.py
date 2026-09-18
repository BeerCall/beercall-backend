from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field, validator


class AperoDecline(BaseModel):
    excuse: str


class ScheduledAperoCreate(BaseModel):
    location_name: str = Field(min_length=1)
    latitude: float
    longitude: float
    scheduled_for: datetime

    @validator("latitude")
    def valid_latitude(cls, value):
        if not -90 <= value <= 90:
            raise ValueError("latitude doit être comprise entre -90 et 90")
        return value

    @validator("longitude")
    def valid_longitude(cls, value):
        if not -180 <= value <= 180:
            raise ValueError("longitude doit être comprise entre -180 et 180")
        return value

    @validator("scheduled_for")
    def timezone_aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_for doit inclure un fuseau horaire")
        return value


class AperoLifecycleResponse(BaseModel):
    id: int
    status: str
    location_name: Optional[str] = None
    latitude: float
    longitude: float
    creator_id: int
    scheduled_for: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    participants_count: int = 0


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
