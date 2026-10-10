from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.minigames.photo_challenge import analyze_photo_with_ai
from services.minigames.drunken_drawing import analyze_drawing_with_ai


PHOTO_ERROR = "Le juge IA s'est étouffé avec une cacahuète (Erreur API)."
DRAWING_ERROR = "Le critique d'art s'est endormi sur le comptoir (Erreur API)."


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario, text, expected", [
    ("offline", None, (True, "Mode hors-ligne : Le jury populaire valide à l'unanimité !")),
    ("yes", "OUI\ncomment", (True, "comment")),
    ("no", "NON\ncomment", (False, "comment")),
    ("one_line", "OUI", (True, "Je reste sans voix...")),
    ("empty", "", (False, PHOTO_ERROR)),
    ("client_error", None, (False, PHOTO_ERROR)),
])
async def test_photo_provider_contract(monkeypatch: pytest.MonkeyPatch, scenario: str, text: str | None, expected: tuple[bool, str]) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-a-secret")
    if scenario == "offline":
        monkeypatch.delenv("GEMINI_API_KEY")
    with patch("services.minigames.photo_challenge.genai.Client") as client:
        if scenario == "client_error":
            client.side_effect = RuntimeError("provider unavailable")
        response = MagicMock(text=text)
        client.return_value.aio.models.generate_content = AsyncMock(return_value=response)
        assert await analyze_photo_with_ai("data:image/jpeg;base64,AABB", "test") == expected
        if scenario == "offline":
            client.assert_not_called()
        elif scenario != "client_error":
            client.return_value.aio.models.generate_content.assert_awaited_once()


@pytest.mark.parametrize("scenario, text, expected", [
    ("offline", None, (True, "Mode hors-ligne : On va dire que c'est de l'art abstrait, c'est validé !")),
    ("yes", "OUI\ncomment", (True, "comment")),
    ("no", "NON\ncomment", (False, "comment")),
    ("one_line", "OUI", (True, "Je n'ai même pas les mots...")),
    ("empty", "", (False, DRAWING_ERROR)),
    ("client_error", None, (False, DRAWING_ERROR)),
])
def test_drawing_provider_contract(monkeypatch: pytest.MonkeyPatch, scenario: str, text: str | None, expected: tuple[bool, str]) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-a-secret")
    if scenario == "offline":
        monkeypatch.delenv("GEMINI_API_KEY")
    with patch("services.minigames.drunken_drawing.genai.Client") as client:
        if scenario == "client_error":
            client.side_effect = RuntimeError("provider unavailable")
        client.return_value.models.generate_content.return_value = MagicMock(text=text)
        assert analyze_drawing_with_ai("data:image/png;base64,AABB", "test") == expected
        if scenario == "offline":
            client.assert_not_called()
        elif scenario != "client_error":
            client.return_value.models.generate_content.assert_called_once()
