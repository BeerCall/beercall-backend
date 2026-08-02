import random
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero
from .base import BaseMiniGame


class MaxPressureGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "MAX_PRESSURE"

    def setup_game(self, apero: Apero, db: Session) -> None:
        state = dict(apero.current_game_state)
        state["status"] = "PLAYING"  # PLAYING, WON, LOST

        # Difficulté : Nombre de secousses à atteindre dans un temps donné
        state["target_shakes"] = random.choice([20, 30, 40])
        state["duration_ms"] = random.choice([5000, 7000])  # 5 ou 7 secondes pour tout donner

        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state

        # On récupère le joueur actuel
        player_ids = state.get("player_ids", [])
        turn_index = state.get("turn_index", 0)
        current_player_id = player_ids[turn_index % len(player_ids)]
        current_username = next((p.user.username for p in apero.participants if p.user_id == current_player_id),
                                "Le Secoueur")

        # --- CAS 1 : GAGNÉ ---
        if state.get("status") == "WON":
            return {
                "game_id": self.game_id,
                "turn_of": "Bouteille explosée 🍾",
                "instruction_header": "Résultat",
                "title": "Quelle énergie !",
                "description": f"Bravo {current_username}, tu as fait exploser la pression ! Distribue 4 gorgées.",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [{"label": "C'est distribué !", "action_id": "NEXT_TURN", "style": "primary"}]
            }

        # --- CAS 2 : PERDU ---
        if state.get("status") == "LOST":
            return {
                "game_id": self.game_id,
                "turn_of": "Pschitt... 💨",
                "instruction_header": "Résultat",
                "title": "Manque de jus...",
                "description": f"Dommage {current_username}, tu n'as pas secoué assez vite. Bois 2 gorgées !",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [{"label": "Je bois...", "action_id": "NEXT_TURN", "style": "danger"}]
            }

        # --- CAS 3 : EN JEU ---
        return {
            "game_id": self.game_id,
            "turn_of": current_username,
            "instruction_header": "Jeu de force",
            "title": "Pression Maximale 💥",
            "description": f"Secoue le téléphone le plus vite possible pour atteindre {state['target_shakes']} secousses en {int(state['duration_ms'] / 1000)} secondes !",
            "required_sensor": {
                "type": "ACCELEROMETER",
                "target_shakes": state["target_shakes"],
                "duration_ms": state["duration_ms"]
            },
            "actions": []
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        action_id = action_payload.get("action_id", "")
        state = dict(apero.current_game_state)

        if action_id == "GAME_WON":
            state["status"] = "WON"
            apero.current_game_state = state
        elif action_id == "GAME_LOST":
            state["status"] = "LOST"
            apero.current_game_state = state
        elif action_id == "NEXT_TURN":
            state["turn_index"] = state.get("turn_index", 0) + 1
            apero.current_game_state = state
            apero.current_game_id = "TURN_TRANSITION"
