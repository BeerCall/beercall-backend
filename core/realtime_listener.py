"""Bounded synchronous PostgreSQL wake listener for BeerCall realtime relay.

Runs in a dedicated thread using psycopg synchrone with autocommit.
Consumes NOTIFY events on 'beercall_realtime' with bounded timeouts (<= 0.5s)
and wakes the asyncio relay loop via loop.call_soon_threadsafe(wake_event.set).
Provides interruptible backoff (0.5s -> 1s -> 2s -> max 5s) and bounded stop (<= 3s).
Disables cleanly as a no-op on SQLite.
"""
import asyncio
import logging
import threading
import time
from typing import Callable, Optional, Union

from sqlalchemy.engine.url import make_url

from core.realtime_notify import REALTIME_CHANNEL

logger = logging.getLogger(__name__)

# Reconnection backoff steps in seconds
BACKOFF_STEPS = (0.5, 1.0, 2.0, 5.0)
MAX_BACKOFF = 5.0
CONNECT_TIMEOUT = 2.0
NOTIFY_TIMEOUT = 0.5
MAX_STOP_BUDGET = 3.0


def extract_psycopg_conninfo(database_url: str) -> Optional[str]:
    """Parse SQLAlchemy URL into libpq conninfo string without exposing secrets.
    Returns None if dialect is not postgresql (e.g. sqlite).
    """
    try:
        url = make_url(database_url)
    except Exception as exc:
        logger.warning("Failed to parse database URL: %s", exc)
        return None

    if "postgresql" not in url.drivername and "postgres" not in url.drivername:
        return None

    # Render without SQLAlchemy-specific driver suffix
    safe_str = url.render_as_string(hide_password=False)
    if safe_str.startswith("postgresql+psycopg://"):
        return safe_str.replace("postgresql+psycopg://", "postgresql://", 1)
    if safe_str.startswith("postgres://"):
        return safe_str.replace("postgres://", "postgresql://", 1)
    return safe_str


def get_masked_url(database_url: str) -> str:
    """Return URL with masked password for safe logging."""
    try:
        url = make_url(database_url)
        return url.render_as_string(hide_password=True)
    except Exception:
        return "<invalid-url>"


class RealtimeListener:
    """Dedicated background listener thread for PostgreSQL pg_notify events."""

    def __init__(
        self,
        database_url: str,
        loop: asyncio.AbstractEventLoop,
        wake_event: asyncio.Event,
        channel: str = REALTIME_CHANNEL,
        connect_factory: Optional[Callable] = None,
    ) -> None:
        self.database_url = database_url
        self.loop = loop
        self.wake_event = wake_event
        self.channel = channel
        self._connect_factory = connect_factory

        self.conninfo = extract_psycopg_conninfo(database_url)
        self.is_enabled = self.conninfo is not None

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._active_conn = None
        self._conn_lock = threading.Lock()
        self._is_running = False

    def start(self) -> None:
        """Start listener background thread. No-op if disabled (e.g. SQLite)."""
        if not self.is_enabled:
            logger.info("RealtimeListener disabled for non-PostgreSQL dialect (%s).", get_masked_url(self.database_url))
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._is_running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="BeerCall-RealtimeListener",
            daemon=True,
        )
        self._thread.start()
        logger.info("RealtimeListener thread started for %s", get_masked_url(self.database_url))

    def _trigger_wake(self) -> None:
        """Thread-safe signal to asyncio wake_event."""
        if not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.wake_event.set)
            except RuntimeError:
                pass

    def _create_connection(self):
        """Create a new psycopg autocommit connection."""
        if self._connect_factory is not None:
            return self._connect_factory(self.conninfo)

        import psycopg

        return psycopg.connect(
            self.conninfo,
            autocommit=True,
            connect_timeout=CONNECT_TIMEOUT,
        )

    def _run_loop(self) -> None:
        """Main listener loop: connects, listens, and consumes notifications with backoff."""
        backoff_idx = 0

        while not self._stop_event.is_set():
            conn = None
            try:
                conn = self._create_connection()
                with self._conn_lock:
                    self._active_conn = conn

                # Subscribe to the notification channel
                conn.execute(f"LISTEN {self.channel};")
                logger.info("RealtimeListener subscribed to channel '%s'", self.channel)

                # REV-09: Request an immediate catch-up pass on fresh subscribe/reconnect
                self._trigger_wake()
                backoff_idx = 0  # Reset backoff on successful connect and subscribe

                # Consume notifications with bounded timeout
                while not self._stop_event.is_set():
                    # notifies() generator polls with NOTIFY_TIMEOUT
                    for notify in conn.notifies(timeout=NOTIFY_TIMEOUT):
                        self._trigger_wake()
                        if self._stop_event.is_set():
                            break

            except Exception as exc:
                if not self._stop_event.is_set():
                    backoff = BACKOFF_STEPS[min(backoff_idx, len(BACKOFF_STEPS) - 1)]
                    logger.warning(
                        "RealtimeListener connection lost (%s). Retrying in %.1fs...",
                        exc.__class__.__name__,
                        backoff,
                    )
                    backoff_idx += 1
                    # Interruptible backoff sleep
                    self._stop_event.wait(timeout=backoff)
            finally:
                with self._conn_lock:
                    self._active_conn = None
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        self._is_running = False

    def stop(self, timeout: float = MAX_STOP_BUDGET) -> bool:
        """Signal thread to stop, close active connection, and join within budget (<= 3s).
        Returns True if stopped cleanly, False if stop budget was exceeded.
        """
        if not self.is_enabled or self._thread is None:
            return True

        self._stop_event.set()

        # Close active connection to unblock any pending socket reads immediately
        with self._conn_lock:
            conn = self._active_conn
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        # Bounded join
        budget = min(timeout, MAX_STOP_BUDGET)
        self._thread.join(timeout=budget)

        if self._thread.is_alive():
            logger.error(
                "RealtimeListener failed to stop within %.1fs budget. Thread is still alive.",
                budget,
            )
            return False

        logger.info("RealtimeListener stopped cleanly.")
        return True

    async def stop_async(self, timeout: float = MAX_STOP_BUDGET) -> bool:
        """Asynchronous non-blocking wrapper around stop."""
        return await asyncio.to_thread(self.stop, timeout=timeout)
