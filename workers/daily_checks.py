import logging
import asyncio
import signal
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
from db.database import SessionLocal
from services.gamification import run_daily_apero_checks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("daily_worker")

def do_daily_checks():
    logger.info("🕒 Lancement de la tâche cron : daily_apero_checks")
    db = SessionLocal()
    try:
        processed = run_daily_apero_checks(db, datetime.now(timezone.utc))
        logger.info(f"✅ Daily checks terminés : {processed} apéros traités.")
    except Exception as e:
        logger.error(f"❌ Erreur lors des checks quotidiens: {e}")
        db.rollback()
    finally:
        db.close()

async def main():
    logger.info("🚀 Démarrage du Daily Worker")

    # Rattrapage au lancement
    do_daily_checks()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(do_daily_checks, 'cron', hour=0, minute=5) # 00:05 UTC
    scheduler.start()
    
    logger.info("⏰ Scheduler démarré, prochain run à 00:05 UTC.")

    stop_event = asyncio.Event()

    def stop_worker():
        logger.info("🛑 Signal reçu, arrêt du worker...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_worker)
        except NotImplementedError:
            pass # Windows

    await stop_event.wait()
    scheduler.shutdown()
    logger.info("👋 Daily Worker arrêté.")

if __name__ == "__main__":
    asyncio.run(main())
