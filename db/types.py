"""
Types SQLAlchemy partagés.

`UTCDateTime` corrige un piège de SQLite : une colonne `DateTime(timezone=True)`
relue depuis la base revient *naïve* (SQLite ne stocke pas le fuseau). Toute
comparaison Python avec `datetime.now(timezone.utc)` échouait alors avec
« can't compare offset-naive and offset-aware datetimes ».
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """`DateTime(timezone=True)` qui rend toujours un `datetime` UTC aware."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
