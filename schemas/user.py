from pydantic import BaseModel
from typing import Optional, List


class AvatarSchema(BaseModel):
    head: str
    body: str
    legs: str
    feet: str
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
    token_type: str

    class Config:
        from_attributes = True


class UserProfileResponse(BaseModel):
    username: str
    caps: int
    title: str
    avatar: AvatarSchema


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
    title: str
    avatar: dict
    unlocked_badges: List[BadgeResponse] = []
    squads: List[SquadBasicInfo] = []
    shop_items: List[ShopItem] = []


class BuyItemRequest(BaseModel):
    item_id: str


class ConnectionItem(BaseModel):
    id: str
    username: str
    caps: int
    title: str
    avatar: dict

class PushTokenUpdate(BaseModel):
    token: str