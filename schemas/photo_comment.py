from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class PhotoCommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Le commentaire ne peut pas être vide.")
        if len(trimmed) > 500:
            raise ValueError("Le commentaire ne peut pas dépasser 500 caractères.")
        return trimmed


class PhotoCommentOut(BaseModel):
    id: int
    author_id: int
    author_username: str
    content: str
    created_at: datetime
    can_delete: bool
