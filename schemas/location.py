import math
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from schemas.avatar_sprites import AvatarSprites


class UserLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)

    @field_validator("latitude")
    @classmethod
    def validate_latitude_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("latitude doit être finie (non-NaN, non-Inf)")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_longitude_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("longitude doit être finie (non-NaN, non-Inf)")
        return v


class MemberLocationResponse(BaseModel):
    user_id: int
    username: str
    latitude: float
    longitude: float
    located_at: datetime
    avatar_sprites: AvatarSprites

    class Config:
        from_attributes = True
