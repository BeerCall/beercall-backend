from pydantic import BaseModel, Field
from typing import Optional, List
from schemas.avatar_sprites import AvatarSprites


class UserStats(BaseModel):
    aperos_created: int
    aperos_joined: int
    aperos_declined: int
    aperos_missed: int
    fraud_count: int


class AvatarSchema(BaseModel):
    head: str
    body: str
    legs: str
    feet: str
    animation: str
    accessory: Optional[str] = None
    gender: str


class UserCreate(BaseModel):
    username: str
    password: str
    avatar: AvatarSchema


class UserResponse(BaseModel):
    id: int
    username: str
    capsules: int
    avatar: AvatarSchema
    access_token: str
    refresh_token: str
    token_type: str

    class Config:
        from_attributes = True


class UserProfileResponse(BaseModel):
    username: str
    caps: int
    score: int
    title: str
    avatar: AvatarSchema
    stats: UserStats
    avatar_sprites: AvatarSprites = Field(default_factory=AvatarSprites)


class BadgeResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None

    class Config:
        from_attributes = True


class SquadBasicInfo(BaseModel):
    id: int
    name: str
    icon: str
    color: str

    class Config:
        from_attributes = True


class ShopItem(BaseModel):
    id: str
    name: str
    category: str
    gender: str
    price: int
    is_owned: bool


class FullProfileResponse(BaseModel):
    id: int
    username: str
    caps: int
    score: int
    title: str
    avatar: dict
    unlocked_badges: List[BadgeResponse] = []
    squads: List[SquadBasicInfo] = []
    shop_items: List[ShopItem] = []
    stats: UserStats
    avatar_sprites: AvatarSprites = Field(default_factory=AvatarSprites)


class BuyItemRequest(BaseModel):
    item_id: str


class ConnectionItem(BaseModel):
    id: str
    username: str
    caps: int
    score: int
    title: str
    avatar: dict
    avatar_sprites: AvatarSprites = Field(default_factory=AvatarSprites)


class PushTokenUpdate(BaseModel):
    token: str
