import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

import pytest


@pytest.mark.parametrize("configured_root", [False, True])
def test_api_emits_job_timing_without_worker_logging(configured_root: bool) -> None:
    script = """
import logging
import logging.config
import sys
from uvicorn.config import LOGGING_CONFIG

logging.config.dictConfig(LOGGING_CONFIG)
if sys.argv[1] == 'True':
    logging.basicConfig(level=logging.INFO, format='existing:%(message)s')
previous_handlers = list(logging.getLogger().handlers)

import main
from services.beer_call_jobs import log_job_timing

assert 'workers.beer_call_worker' not in sys.modules
if previous_handlers:
    assert logging.getLogger().handlers == previous_handlers
log_job_timing('enqueued', 'api-logging-regression', 1, 0, status='pending')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(configured_root)],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "APP_ENV": "test",
            "DATABASE_URL": "sqlite:///:memory:",
            "BEERCALL_SECRET_KEY": secrets.token_hex(32),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    lines = [line for line in result.stderr.splitlines() if '"event": "beer_job_timing"' in line]
    assert len(lines) == 1
    payload = json.loads(lines[0][lines[0].index('{'):])
    assert payload["phase"] == "enqueued"
    assert payload["job_id"] == "api-logging-regression"
    assert payload["attempt"] == 0
    assert payload["status"] == "pending"
    if configured_root:
        assert lines[0].startswith("existing:")
