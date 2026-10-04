import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from services.minigames.photo_challenge import PhotoChallengeGame, analyze_photo_with_ai
from services.minigames.drunken_drawing import DrunkenDrawingGame, analyze_drawing_with_ai
from models.apero import Apero

@pytest.mark.asyncio
@patch("services.minigames.photo_challenge.genai.Client")
async def test_photo_challenge_setup(mock_client, db_session):
    mock_instance = MagicMock()
    mock_client.return_value = mock_instance
    mock_response = MagicMock()
    mock_response.text = 'OUI\nSuper photo !'
    mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)

    game = PhotoChallengeGame()
    apero = Apero(id=10, current_game_state={"challenge": "test"})
    
    # Test GenAI parse
    is_valid, comment = await analyze_photo_with_ai("data:image/jpeg;base64,AABB", "test")
    assert is_valid is True
    assert comment == "Super photo !"

@patch("services.minigames.drunken_drawing.genai.Client")
def test_drunken_drawing_setup(mock_client, db_session):
    mock_instance = MagicMock()
    mock_client.return_value = mock_instance
    mock_response = MagicMock()
    mock_response.text = 'NON\nC\'est moche'
    mock_instance.models.generate_content.return_value = mock_response

    game = DrunkenDrawingGame()
    apero = Apero(id=11, current_game_state={"word": "test"})

    is_valid, comment = analyze_drawing_with_ai("data:image/png;base64,AABB", "test")
    assert is_valid is False
    assert comment == "C'est moche"
