import json
from pathlib import Path

from services.avatar_catalog import humanize


def test_humanize_matches_shared_fixture():
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "humanize-cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    assert cases
    for case in cases:
        assert humanize(case["input"]) == case["expected"], case["input"]
