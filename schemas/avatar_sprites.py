from typing import Literal, Optional
from pydantic import BaseModel

AvatarRenderStatus = Literal["missing", "pending", "ready", "failed", "disabled"]


class AvatarSprites(BaseModel):
    hash: Optional[str] = None
    head_url: Optional[str] = None
    portrait_url: Optional[str] = None
    body_url: Optional[str] = None
    pending: bool = False
    status: AvatarRenderStatus = "missing"

    class Config:
        from_attributes = True
