import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from models.realtime_event import RealtimeEvent
from db.database import SessionLocal
from core.websocket import manager

logger = logging.getLogger(__name__)

def fetch_and_lock_events():
    db = SessionLocal()
    try:
        events = db.query(RealtimeEvent).filter(
            RealtimeEvent.dispatched_at.is_(None)
        ).order_by(RealtimeEvent.created_at.asc()).limit(50).with_for_update(skip_locked=True).all()
        
        results = []
        for e in events:
            results.append({
                "id": e.id,
                "squad_id": e.squad_id,
                "payload": e.payload
            })
            e.dispatched_at = datetime.now(timezone.utc)
        db.commit()
        return results
    except Exception as e:
        db.rollback()
        logger.error(f"Error fetching events: {e}")
        return []
    finally:
        db.close()

def purge_old_events():
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


async def realtime_relay_loop(stop_event: asyncio.Event):
    logger.info("Realtime relay started")
    purge_counter = 0
    while not stop_event.is_set():
        try:
            events = await asyncio.to_thread(fetch_and_lock_events)
            for e in events:
                await manager.broadcast_to_squad(e["squad_id"], e["payload"])
            
            purge_counter += 1
            if purge_counter >= 60: # ~ every minute if polling every 1s
                await asyncio.to_thread(purge_old_events)
                purge_counter = 0

            if not events:
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
