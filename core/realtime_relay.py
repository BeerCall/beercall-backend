import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from core.realtime_listener import RealtimeListener
from core.websocket import manager
from db.database import DATABASE_URL, SessionLocal
from models.realtime_event import RealtimeEvent
from models.squad import Squad
from services.notifications import send_durable_push

logger = logging.getLogger(__name__)

MAX_QUARANTINE_REGISTRY = 4096
QUARANTINE_FAILURE_THRESHOLD = 5
QUARANTINE_DURATION_SECONDS = 300.0


class QuarantineRegistry:
    """In-memory registry tracking consecutive failures and quarantine expiration
    to prevent Head-of-Line (HoL) blocking (CHAL-02, REV-09).
    """

    def __init__(
        self,
        max_entries: int = MAX_QUARANTINE_REGISTRY,
        threshold: int = QUARANTINE_FAILURE_THRESHOLD,
        duration: float = QUARANTINE_DURATION_SECONDS,
    ):
        self.max_entries = max_entries
        self.threshold = threshold
        self.duration = duration
        # event_id -> {"failures": int, "quarantine_until": Optional[float]}
        self._entries: Dict[object, dict] = {}

    def is_quarantined(self, event_id: object, now: float) -> bool:
        entry = self._entries.get(event_id)
        if not entry:
            return False
        q_until = entry.get("quarantine_until")
        if q_until is None:
            return False
        if now >= q_until:
            # Expired quarantine: readmit automatically without restart
            entry["quarantine_until"] = None
            entry["failures"] = 0
            return False
        return True

    def get_quarantined_ids(self, now: float) -> List[object]:
        self.purge_expired(now)
        return [
            ev_id
            for ev_id, entry in self._entries.items()
            if entry.get("quarantine_until") is not None and now < entry["quarantine_until"]
        ]

    def record_failure(self, event_id: object, now: float) -> None:
        self.purge_expired(now)
        if event_id not in self._entries:
            if len(self._entries) >= self.max_entries:
                logger.warning(
                    "Quarantine registry saturated (%d entries). Backoff engaged without silent eviction.",
                    len(self._entries),
                )
                return
            self._entries[event_id] = {"failures": 1, "quarantine_until": None}
        else:
            self._entries[event_id]["failures"] += 1

        failures = self._entries[event_id]["failures"]
        if failures >= self.threshold:
            self._entries[event_id]["quarantine_until"] = now + self.duration
            logger.warning(
                "Event %s quarantined for %.0fs after %d consecutive failures (anti-HoL)",
                str(event_id),
                self.duration,
                failures,
            )

    def record_success(self, event_id: object) -> None:
        self._entries.pop(event_id, None)

    def purge_expired(self, now: float) -> None:
        to_delete = [
            ev_id
            for ev_id, entry in self._entries.items()
            if entry.get("quarantine_until") is not None and now >= entry["quarantine_until"]
        ]
        for ev_id in to_delete:
            del self._entries[ev_id]

    def clear(self) -> None:
        self._entries.clear()


def dispatch_pushes(event_id: object) -> None:
    """Persist each confirmed recipient independently of WebSocket delivery."""
    with SessionLocal() as db:
        event = db.query(RealtimeEvent).filter(RealtimeEvent.id == event_id).first()
        if not event:
            return
        payload = dict(event.payload)
        squad = db.query(Squad).filter(Squad.id == event.squad_id).first()
        if not squad:
            return
        recipients = payload.get("push_recipients")
        if recipients is None:
            recipients = [
                member.id
                for member in squad.members
                if member.push_token and member.id != payload.get("creator_id")
            ]
            payload["push_recipients"] = recipients
            event.payload = dict(payload)
            db.commit()
        completed = list(payload.get("push_completed", []))
        members = {member.id: member for member in squad.members}
        for user_id in recipients:
            if user_id in completed:
                continue
            member = members.get(user_id)
            if member and member.push_token:
                send_durable_push(member.push_token, str(event_id))
            completed.append(user_id)
            payload["push_completed"] = list(completed)
            event.payload = dict(payload)
            db.commit()


def fetch_events_to_dispatch(excluded_ids: Optional[List[object]] = None) -> list[dict]:
    db = SessionLocal()
    try:
        query = db.query(RealtimeEvent).filter(RealtimeEvent.dispatched_at.is_(None))
        if excluded_ids:
            # The caller bounds the registry at 4096. Chunk clauses, never discard
            # exclusions: otherwise the next 50 quarantined rows block healthy work.
            for offset in range(0, len(excluded_ids), 1000):
                query = query.filter(~RealtimeEvent.id.in_(excluded_ids[offset:offset + 1000]))
        events = query.order_by(RealtimeEvent.created_at.asc()).limit(50).all()

        results = []
        for e in events:
            results.append({
                "id": e.id,
                "squad_id": e.squad_id,
                "payload": e.payload,
            })
        return results
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return []
    finally:
        db.close()


def mark_events_dispatched(event_ids: list) -> bool:
    """Mark events as dispatched. Returns True if commit succeeded, False on error."""
    if not event_ids:
        return True
    db = SessionLocal()
    try:
        db.query(RealtimeEvent).filter(RealtimeEvent.id.in_(event_ids)).update(
            {"dispatched_at": datetime.now(timezone.utc)}, synchronize_session=False
        )
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error marking events dispatched: {e}")
        return False
    finally:
        db.close()


def purge_old_events() -> None:
    db = SessionLocal()
    try:
        limit_date = datetime.now(timezone.utc) - timedelta(days=7)
        deleted = (
            db.query(RealtimeEvent)
            .filter(
                RealtimeEvent.dispatched_at.is_not(None),
                RealtimeEvent.created_at < limit_date,
            )
            .delete()
        )
        if deleted > 0:
            db.commit()
            logger.info(f"Purged {deleted} old realtime events")
    except Exception as e:
        db.rollback()
        logger.error(f"Error purging events: {e}")
    finally:
        db.close()


async def realtime_relay_loop(
    stop_event: asyncio.Event,
    wake_event: Optional[asyncio.Event] = None,
    listener: Optional[RealtimeListener] = None,
    quarantine: Optional[QuarantineRegistry] = None,
    database_url: Optional[str] = None,
    clock: Optional[object] = None,
) -> None:
    """Durable realtime relay loop (CHAL-02 / REV-09).

    Features:
    - Bounded synchronous wake listener on PostgreSQL pg_notify
    - Fallback polling every 1.0s if wake listener is inactive or SQLite
    - Continuous drain without sleep when batch of 50 makes progress
    - Anti-HoL blocking quarantine registry (5 failures -> 300s quarantine, max 4096 entries)
    - Monotonic 60s purge timer
    - Listener/idle shutdown bounded to 3s; in-flight delivery has no global deadline
    """
    logger.info("Realtime relay started")

    get_clock = clock if clock is not None else time.monotonic

    if wake_event is None:
        wake_event = asyncio.Event()

    if database_url is None:
        try:
            with SessionLocal() as s:
                bind = s.get_bind()
                if bind is not None and hasattr(bind, "url"):
                    database_url = bind.url.render_as_string(hide_password=False)
        except Exception:
            database_url = None
    if database_url is None:
        database_url = DATABASE_URL

    own_listener = listener is None
    if own_listener:
        listener = RealtimeListener(
            database_url=database_url,
            loop=asyncio.get_running_loop(),
            wake_event=wake_event,
        )
        listener.start()

    if quarantine is None:
        quarantine = QuarantineRegistry()

    last_purge_time = get_clock()

    try:
        while not stop_event.is_set():
            try:
                # 1. Clear wake flag BEFORE fetch so incoming notifications during dispatch are not lost
                wake_event.clear()

                now = get_clock()
                quarantined_ids = quarantine.get_quarantined_ids(now)

                events = await asyncio.to_thread(fetch_events_to_dispatch, quarantined_ids)
                dispatched_count = 0

                for e in events:
                    event_id = e["id"]
                    try:
                        payload = e["payload"]
                        public_payload = {
                            key: value
                            for key, value in payload.items()
                            if not key.startswith("push_")
                        }
                        await manager.broadcast_to_squad(e["squad_id"], public_payload)
                        if (
                            payload.get("type") == "REFRESH_SQUAD"
                            and payload.get("action") == "CREATE"
                        ):
                            await asyncio.to_thread(dispatch_pushes, event_id)

                        marked = await asyncio.to_thread(mark_events_dispatched, [event_id])
                        if marked:
                            dispatched_count += 1
                            quarantine.record_success(event_id)
                        else:
                            quarantine.record_failure(event_id, get_clock())
                    except Exception:
                        logger.exception("Delivery failed for event %s; retained for retry", event_id)
                        quarantine.record_failure(event_id, get_clock())

                # 2. Monotonic purge every 60 seconds
                now_mono = get_clock()
                if now_mono - last_purge_time >= 60.0:
                    await asyncio.to_thread(purge_old_events)
                    last_purge_time = now_mono

                # 3. Drain loop continuation:
                # After a full batch of 50, continue draining immediately if progress was made
                if len(events) == 50 and dispatched_count > 0 and not stop_event.is_set():
                    continue

                if stop_event.is_set():
                    break

                # 4. Wait for wake, stop, or fallback polling timeout (1.0s)
                # Annuler/attendre les tâches perdantes pour ne pas fuiter
                wake_task = asyncio.create_task(wake_event.wait())
                stop_task = asyncio.create_task(stop_event.wait())
                done, pending = await asyncio.wait(
                    [wake_task, stop_task],
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            except Exception as e:
                logger.error(f"Unhandled error in realtime relay loop: {e}")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
    finally:
        if listener is not None:
            await listener.stop_async(timeout=3.0)
        logger.info("Realtime relay stopped")
