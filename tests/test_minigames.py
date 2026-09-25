import pytest
from services.minigames.registry import GAME_REGISTRY

def test_all_minigames_instantiation_and_base_methods():
    for game_id, game_instance in GAME_REGISTRY.items():
        assert game_instance.game_id == game_id
        # ensure get_sdui_payload is defined
        assert hasattr(game_instance, "get_sdui_payload")
        assert hasattr(game_instance, "handle_action")
