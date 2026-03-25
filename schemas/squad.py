from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from models.apero import ParticipationStatus


class SquadBase(BaseModel):
    name: str
    icon: str
    color: str


class SquadCreate(SquadBase):
    pass


class SquadResponse(SquadBase):
    id: int
    invite_code: Optional[str]

    class Config:
        from_attributes = True


class BeerCallItem(BaseModel):
    id: str
    creator_name: str
    location_name: str
    longitude: float
    latitude: float
    started_at: datetime
    participants_count: int
    has_responded: bool
    user_status: Optional[ParticipationStatus] = None


class SquadDetailsResponse(BaseModel):
    id: str
    name: str
    color: str
    icon: str
    invite_code: str
    active_beer_call: List[BeerCallItem] = []
    past_beer_calls: List[BeerCallItem] = []


class SquadJoin(BaseModel):
    invite_code: str
