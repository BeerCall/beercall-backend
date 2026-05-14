import random
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero
from .base import BaseMiniGame


class PenaltyShootoutGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "PENALTY_SHOOTOUT"

    def setup_game(self, apero: Apero, db: Session) -> None:
        state = dict(apero.current_game_state)
        state["status"] = "PLAYING"  # PLAYING, WON, LOST

        # Difficulté générée par le backend
        state["wind_force"] = random.choice([-2, -1, 0, 1, 2])  # Négatif = vent vers la gauche, Positif = droite
        state["target_size"] = random.choice(["large", "medium", "small"])  # Taille de la lucarne

        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state

        player_ids = state.get("player_ids", [])
        turn_index = state.get("turn_index", 0)
        current_player_id = player_ids[turn_index % len(player_ids)]
        current_username = next((p.user.username for p in apero.participants if p.user_id == current_player_id),
                                "Le Buteur")

        # --- CAS 1 : GAGNÉ ---
        if state.get("status") == "WON":
            return {
                "turn_of": "Gooooooooaaaal ! ⚽",
                "instruction_header": "Résultat",
                "title": "Pleine lucarne !",
                "description": f"Quel tir magnifique de {current_username}. Tu peux distribuer 3 gorgées !",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [{"label": "Je distribue", "action_id": "NEXT_TURN", "style": "primary"}]
            }

        # --- CAS 2 : PERDU ---
        if state.get("status") == "LOST":
            return {
                "turn_of": "Dans les tribunes... 🕊️",
                "instruction_header": "Résultat",
                "title": "Quel raté !",
                "description": f"C'est honteux {current_username}, même ma grand-mère l'aurait mise. Bois 2 gorgées !",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [{"label": "Je bois la honte", "action_id": "NEXT_TURN", "style": "danger"}]
            }

        # --- CAS 3 : EN JEU ---
        wind_text = f"Attention au vent (Force: {state['wind_force']})" if state[
                                                                               'wind_force'] != 0 else "Pas de vent, c'est le moment parfait !"

        return {
            "turn_of": current_username,
            "instruction_header": "Tir au but",
            "title": "Pression sur le Point de Penalty",
            "description": f"Glisse ton doigt (swipe) pour tirer. {wind_text}",
            "required_sensor": {
                "type": "SWIPE_TO_TARGET",
                "wind_force": state["wind_force"],
                "target_size": state["target_size"]
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
