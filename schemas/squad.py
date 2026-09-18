from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from models.apero import AperoStatus, ParticipationStatus


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
    creator_id: int
    location_name: str
    longitude: float
    latitude: float
    status: AperoStatus
    scheduled_for: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    participants_count: int
    has_responded: bool
    user_status: Optional[ParticipationStatus] = None
    can_start: bool = False


class SquadDetailsResponse(BaseModel):
    id: str
    name: str
    color: str
    icon: str
    invite_code: str
    active_beer_call: List[BeerCallItem] = []
    scheduled_beer_calls: List[BeerCallItem] = []
    past_beer_calls: List[BeerCallItem] = []


class SquadJoin(BaseModel):
    invite_code: str
