import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from models.realtime_event import RealtimeEvent
from db.database import SessionLocal
from core.websocket import manager
from models.squad import Squad
from services.notifications import send_push_notifications
from db.database import SessionLocal

logger = logging.getLogger(__name__)

def fetch_events_to_dispatch():
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

def mark_events_dispatched(event_ids: list):
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
            events = await asyncio.to_thread(fetch_events_to_dispatch)
            
            dispatched_ids = []
            for e in events:
                # 1. Envoi Temps Réel (WS)
                await manager.broadcast_to_squad(e["squad_id"], e["payload"])
                
                # 2. Envoi Notifications Push (si CREATION)
                payload = e["payload"]
                if payload.get("type") == "REFRESH_SQUAD" and payload.get("action") == "CREATE":
                    def send_pushes(squad_id):
                        db = SessionLocal()
                        try:
                            squad = db.query(Squad).filter(Squad.id == squad_id).first()
                            if squad:
                                tokens = [member.push_token for member in squad.members if member.push_token]
                                if tokens:
                                    send_push_notifications(tokens, "Nouveau Beer Call ! 🍻", "Un apéro t'attend !")
                        except Exception as ex:
                            logger.error(f"Error sending pushes in relay: {ex}")
                        finally:
                            db.close()
                    await asyncio.to_thread(send_pushes, e["squad_id"])

                dispatched_ids.append(e["id"])
                
            if dispatched_ids:
                await asyncio.to_thread(mark_events_dispatched, dispatched_ids)
            
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
