import asyncio
import logging
from datetime import datetime, timezone, timedelta
from models.realtime_event import RealtimeEvent
from db.database import SessionLocal
from core.websocket import manager
from models.squad import Squad
from services.notifications import send_durable_push

logger = logging.getLogger(__name__)

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
            recipients = [member.id for member in squad.members
                          if member.push_token and member.id != payload.get("creator_id")]
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

def fetch_events_to_dispatch() -> list[dict]:
    db = SessionLocal()
    try:
        events = db.query(RealtimeEvent).filter(
            RealtimeEvent.dispatched_at.is_(None)
        ).order_by(RealtimeEvent.created_at.asc()).limit(50).all()
        
        results = []
        for e in events:
            results.append({
                "id": e.id,
                "squad_id": e.squad_id,
                "payload": e.payload
            })
        return results
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return []
    finally:
        db.close()

def mark_events_dispatched(event_ids: list) -> None:
    if not event_ids:
        return
    db = SessionLocal()
    try:
        db.query(RealtimeEvent).filter(RealtimeEvent.id.in_(event_ids)).update(
            {"dispatched_at": datetime.now(timezone.utc)}, synchronize_session=False
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error marking events dispatched: {e}")
    finally:
        db.close()

def purge_old_events() -> None:
    db = SessionLocal()
    try:
        limit_date = datetime.now(timezone.utc) - timedelta(days=7)
        deleted = db.query(RealtimeEvent).filter(
            RealtimeEvent.dispatched_at.is_not(None),
            RealtimeEvent.created_at < limit_date
        ).delete()
        if deleted > 0:
            db.commit()
            logger.info(f"Purged {deleted} old realtime events")
    except Exception as e:
        db.rollback()
        logger.error(f"Error purging events: {e}")
    finally:
        db.close()


async def realtime_relay_loop(stop_event: asyncio.Event) -> None:
    logger.info("Realtime relay started")
    purge_counter = 0
    while not stop_event.is_set():
        try:
            events = await asyncio.to_thread(fetch_events_to_dispatch)
            
            for e in events:
                try:
                    payload = e["payload"]
                    public_payload = {key: value for key, value in payload.items()
                                      if not key.startswith("push_")}
                    await manager.broadcast_to_squad(e["squad_id"], public_payload)
                    if payload.get("type") == "REFRESH_SQUAD" and payload.get("action") == "CREATE":
                        await asyncio.to_thread(dispatch_pushes, e["id"])
                    await asyncio.to_thread(mark_events_dispatched, [e["id"]])
                except Exception:
                    logger.exception("Delivery failed for event %s; retained for retry", e["id"])
                
            purge_counter += 1
            if purge_counter >= 60: # ~ every minute if polling every 1s
                await asyncio.to_thread(purge_old_events)
                purge_counter = 0

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        except Exception as e:
            logger.error(f"Unhandled error in realtime relay loop: {e}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
    logger.info("Realtime relay stopped")
