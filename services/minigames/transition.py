import random
from typing import Dict, Any

from sqlalchemy.orm import Session

from models.apero import Apero
from .base import BaseMiniGame


class TurnTransitionGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "TURN_TRANSITION"

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state
        player_ids = state.get("player_ids", [])
        turn_index = state.get("turn_index", 0)

        # On détermine qui a le téléphone
        current_player_id = player_ids[turn_index % len(player_ids)]
        current_username = next((p.user.username for p in apero.participants if p.user_id == current_player_id),
                                "Inconnu")

        return {
            "game_id": self.game_id,
            "turn_of": current_username,
            "instruction_header": f"Passez le téléphone à {current_username}",
            "title": "Nouveau défi ! 🔥",
            "description": f"{current_username}, prépare-toi à lancer le prochain jeu pour le groupe.",
            "required_sensor": {"type": "BUTTONS"},
            "actions": [{"label": "Je suis prêt !", "action_id": "START_RANDOM_GAME", "style": "primary"}]
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        if action_payload.get("action_id") == "START_RANDOM_GAME":
            # IMPORT LOCAL pour éviter la boucle d'import
            from .registry import GAME_REGISTRY

            # 1. On tire un jeu AU HASARD
            available_games = [gid for gid in GAME_REGISTRY.keys() if gid != "TURN_TRANSITION"]
            next_game_id = random.choice(available_games)

            # 2. On change l'ID du jeu
            apero.current_game_id = next_game_id

            # 3. On laisse le jeu se préparer (choisir sa question, etc.)
            GAME_REGISTRY[next_game_id].setup_game(apero, db)
