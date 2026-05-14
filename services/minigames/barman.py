import random
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero
from .base import BaseMiniGame


class BarmanGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "BARMAN_EQUILIBRISTE"

    def setup_game(self, apero: Apero, db: Session) -> None:
        state = dict(apero.current_game_state)
        state["status"] = "PLAYING"  # PLAYING, WON, LOST

        # Difficulté aléatoire
        state["max_tilt_angle"] = random.choice([10, 15, 20])  # En degrés
        state["duration_ms"] = random.choice([8000, 10000, 15000])  # Entre 8 et 15 secondes

        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state

        # Récupération du joueur actuel
        player_ids = state.get("player_ids", [])
        turn_index = state.get("turn_index", 0)
        current_player_id = player_ids[turn_index % len(player_ids)]
        current_username = next((p.user.username for p in apero.participants if p.user_id == current_player_id),
                                "Le Barman")

        # --- CAS 1 : GAGNÉ ---
        if state.get("status") == "WON":
            return {
                "turn_of": "Victoire 🏆",
                "instruction_header": "Résultat",
                "title": "Service parfait !",
                "description": f"Bien joué {current_username}, aucune goutte n'est tombée. Distribue 3 gorgées !",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [{"label": "C'est distribué", "action_id": "NEXT_TURN", "style": "primary"}]
            }

        # --- CAS 2 : PERDU ---
        if state.get("status") == "LOST":
            return {
                "turn_of": "Désastre 💥",
                "instruction_header": "Résultat",
                "title": "Tout est par terre !",
                "description": f"Catastrophe {current_username}, tu as fait tomber le plateau ! Bois 3 gorgées.",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [{"label": "Je bois...", "action_id": "NEXT_TURN", "style": "danger"}]
            }

        # --- CAS 3 : EN JEU ---
        return {
            "turn_of": current_username,
            "instruction_header": "Jeu d'équilibre",
            "title": "Le Plateau de Pintes 🍺",
            "description": "Pose le téléphone à plat sur ta paume. S'il penche trop, tu perds !",
            "required_sensor": {
                "type": "GYROSCOPE",
                "max_tilt_angle": state["max_tilt_angle"],
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
