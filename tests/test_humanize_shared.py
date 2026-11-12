import json
from pathlib import Path

from services.avatar_catalog import humanize


def test_humanize_matches_shared_fixture():
    # Copie versionnée du contrat partagé avec beercall-frontend : le dépôt
    # backend est le seul livré au runner CI, le dossier parent ne l'est pas.
    fixture = Path(__file__).resolve().parent / "fixtures" / "humanize-cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    assert cases
    for case in cases:
        assert humanize(case["input"]) == case["expected"], case["input"]
